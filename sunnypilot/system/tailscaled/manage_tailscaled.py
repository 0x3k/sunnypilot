#!/usr/bin/env python3
"""
Supervisor for tailscaled on comma-four. Self-gates on /data/tailscale/enabled, handles
stale-socket cleanup, installs binaries on request, and forwards shutdown signals to the
child so tailscaled exits cleanly on device shutdown.

Runs as a PythonProcess (always_run). When the enable flag is off, it sleeps and re-checks —
toggling the flag at runtime takes effect without a manager restart.
"""
import os
import signal
import subprocess
import time

from setproctitle import setproctitle

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.tailscale import (
  TAILSCALED_BIN,
  TAILSCALE_SOCKET,
  TAILSCALE_STATE_DIR,
  TAILSCALE_STATE_FILE,
  clear_tailscale_install_request,
  get_backend_state,
  is_tailscale_enabled,
  is_tailscale_install_requested,
  is_tailscale_installed,
  read_authkey,
  run_tailscale_cli,
)
from openpilot.sunnypilot.system.tailscaled import installer

DISABLED_POLL_INTERVAL = 30
RESTART_BACKOFF = 5
READINESS_TIMEOUT = 10


def _ensure_state_dir() -> None:
  os.makedirs(TAILSCALE_STATE_DIR, exist_ok=True)
  try:
    os.chmod(TAILSCALE_STATE_DIR, 0o700)
  except OSError:
    pass


def _existing_daemon_responsive() -> bool:
  if not os.path.exists(TAILSCALE_SOCKET):
    return False
  try:
    result = run_tailscale_cli(["status", "--json"], timeout=3)
    return result.returncode == 0
  except (subprocess.TimeoutExpired, OSError):
    return False


def _cleanup_stale() -> None:
  try:
    subprocess.run(
      [TAILSCALED_BIN, "--cleanup", f"--statedir={TAILSCALE_STATE_DIR}"],
      capture_output=True, timeout=15, check=False,
    )
  except (subprocess.TimeoutExpired, OSError) as e:
    cloudlog.warning(f"manage_tailscaled: cleanup failed: {e}")
  try:
    if os.path.exists(TAILSCALE_SOCKET):
      os.remove(TAILSCALE_SOCKET)
  except OSError:
    pass


def _spawn_tailscaled() -> subprocess.Popen:
  _ensure_state_dir()
  cmd = [
    TAILSCALED_BIN,
    f"--state={TAILSCALE_STATE_FILE}",
    f"--statedir={TAILSCALE_STATE_DIR}",
    f"--socket={TAILSCALE_SOCKET}",
    "--tun=userspace-networking",
  ]
  cloudlog.info(f"manage_tailscaled: spawning {' '.join(cmd)}")
  return subprocess.Popen(cmd, start_new_session=False)


def _wait_for_socket(deadline: float) -> bool:
  while time.monotonic() < deadline:
    if os.path.exists(TAILSCALE_SOCKET):
      try:
        result = run_tailscale_cli(["status", "--json"], timeout=2)
        if result.returncode == 0:
          return True
      except (subprocess.TimeoutExpired, OSError):
        pass
    time.sleep(0.5)
  return False


def _hostname() -> str:
  params = Params()
  for key in ("DongleId", "HardwareSerial"):
    val = params.get(key)
    if val:
      return str(val)
  return "comma"


def _try_login_if_needed() -> None:
  state = get_backend_state()
  if state == "Running":
    return
  authkey = read_authkey()
  if not authkey:
    cloudlog.info(f"manage_tailscaled: BackendState={state or 'unknown'}, no authkey — waiting for UI sign-in")
    return
  cloudlog.info(f"manage_tailscaled: BackendState={state}, running `tailscale up` with stored authkey")
  try:
    result = run_tailscale_cli(
      ["up", f"--authkey={authkey}", f"--hostname={_hostname()}", "--ssh", "--reset"],
      timeout=60,
    )
    if result.returncode != 0:
      cloudlog.error(f"manage_tailscaled: `tailscale up` failed: {result.stderr.strip()}")
  except (subprocess.TimeoutExpired, OSError) as e:
    cloudlog.exception(f"manage_tailscaled: `tailscale up` raised: {e}")


class _SignalForwarder:
  def __init__(self):
    self.proc: subprocess.Popen | None = None
    self.shutting_down = False
    signal.signal(signal.SIGTERM, self._handle)
    signal.signal(signal.SIGINT, self._handle)

  def _handle(self, signum, frame):
    self.shutting_down = True
    if self.proc and self.proc.poll() is None:
      cloudlog.info(f"manage_tailscaled: forwarding signal {signum} to tailscaled")
      try:
        self.proc.terminate()
        self.proc.wait(timeout=5)
      except subprocess.TimeoutExpired:
        self.proc.kill()
      except OSError:
        pass
    os._exit(0)


def main() -> None:
  setproctitle("manage_tailscaled")
  forwarder = _SignalForwarder()

  while not forwarder.shutting_down:
    if not is_tailscale_enabled():
      time.sleep(DISABLED_POLL_INTERVAL)
      continue

    if is_tailscale_install_requested() or not is_tailscale_installed():
      cloudlog.info("manage_tailscaled: installing tailscale binaries")
      ok = installer.install()
      clear_tailscale_install_request()
      if not ok:
        time.sleep(DISABLED_POLL_INTERVAL)
        continue

    if _existing_daemon_responsive():
      cloudlog.info("manage_tailscaled: adopting already-running tailscaled")
    else:
      _cleanup_stale()
      try:
        forwarder.proc = _spawn_tailscaled()
      except OSError as e:
        cloudlog.exception(f"manage_tailscaled: failed to spawn tailscaled: {e}")
        time.sleep(RESTART_BACKOFF)
        continue

    if not _wait_for_socket(time.monotonic() + READINESS_TIMEOUT):
      cloudlog.warning("manage_tailscaled: socket never came up; restarting")
      if forwarder.proc:
        forwarder.proc.terminate()
        forwarder.proc = None
      time.sleep(RESTART_BACKOFF)
      continue

    _try_login_if_needed()

    if forwarder.proc is not None:
      exitcode = forwarder.proc.wait()
      cloudlog.event("manage_tailscaled.tailscaled_exited", exitcode=exitcode)
      forwarder.proc = None
      if forwarder.shutting_down:
        break
      time.sleep(RESTART_BACKOFF)
    else:
      while not forwarder.shutting_down and is_tailscale_enabled() and _existing_daemon_responsive():
        time.sleep(DISABLED_POLL_INTERVAL)


if __name__ == "__main__":
  main()
