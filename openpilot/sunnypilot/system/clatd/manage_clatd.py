#!/usr/bin/env python3
"""
Supervisor for tayga (CLAT / 464XLAT) on comma-four. Self-gates on /data/clat/enabled.

Detects the cellular bearer by polling ppp0 for a global IPv6, derives the /64,
configures clat0, and supervises tayga. On bearer change (IPv6 prefix changes
when the modem renegotiates) or supervisor shutdown, tears down cleanly.

Wi-Fi preference is preserved by adding the IPv4 default route via clat0 at
metric 800 — wlan0's default (600) still wins, native ppp0's broken default
(1000) loses.

Pattern mirrors sunnypilot/system/tailscaled/manage_tailscaled.py.
"""
import os
import signal
import subprocess
import time

from setproctitle import setproctitle

from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.clatd import (
  CLAT_BUILD_DIR,
  CLAT_IPV4_LOCAL,
  CLAT_IPV4_TAYGA,
  CLAT_IPV6_SUFFIX,
  CLAT_RUN_DIR,
  CLAT_ROUTE_METRIC,
  CLAT_STATE_DIR,
  NAT64_PREFIX,
  TAYGA_BIN,
  TAYGA_CONF,
  clear_clat_install_request,
  is_clat_enabled,
  is_clat_install_requested,
  is_clat_installed,
)
from openpilot.sunnypilot.system.clatd import installer

DISABLED_POLL_INTERVAL = 5
NO_BEARER_POLL_INTERVAL = 5
RUNNING_POLL_INTERVAL = 3
RESTART_BACKOFF = 5
TUN_NAME = "clat0"
WAN_IFACE = "ppp0"
TUN_MTU = 1280


def _ensure_dirs() -> None:
  for d in (CLAT_RUN_DIR, CLAT_STATE_DIR, CLAT_BUILD_DIR):
    os.makedirs(d, exist_ok=True)


def _run(cmd: list[str], check: bool = False, timeout: float = 10) -> subprocess.CompletedProcess:
  return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def _sudo(cmd: list[str], check: bool = False, timeout: float = 10) -> subprocess.CompletedProcess:
  return _run(["sudo", "-n", *cmd], check=check, timeout=timeout)


def _bearer_ipv6_prefix() -> str:
  """Return the bearer's /64 prefix (e.g. '2607:fb91:acc:51bc::') if a global IPv6 is on WAN_IFACE, else ''."""
  try:
    result = _run(["ip", "-6", "-o", "addr", "show", WAN_IFACE, "scope", "global"], timeout=3)
  except (subprocess.TimeoutExpired, OSError):
    return ""
  for line in result.stdout.splitlines():
    parts = line.split()
    if len(parts) < 4 or parts[2] != "inet6":
      continue
    addr = parts[3].split("/")[0]
    groups = addr.split(":")
    if len(groups) < 4:
      continue
    return ":".join(groups[:4]) + "::"
  return ""


def _write_config(map6: str) -> None:
  conf = (
    f"tun-device {TUN_NAME}\n"
    + f"ipv4-addr {CLAT_IPV4_TAYGA}\n"
    + f"prefix {NAT64_PREFIX}\n"
    + "wkpf-strict no\n"
    + f"map {CLAT_IPV4_LOCAL} {map6}\n"
    + "log drop reject\n"
    + f"data-dir {CLAT_STATE_DIR}\n"
  )
  tmp = TAYGA_CONF + ".tmp"
  with open(tmp, "w") as f:
    f.write(conf)
  os.replace(tmp, TAYGA_CONF)


def _enable_forwarding() -> None:
  for path in ("/proc/sys/net/ipv4/conf/all/forwarding", "/proc/sys/net/ipv6/conf/all/forwarding"):
    _sudo(["sh", "-c", f"echo 1 > {path}"])


def _bring_up_clat(map6: str) -> None:
  """Create clat0, attach addresses, install routes. Idempotent-ish — best-effort cleanup first."""
  _tear_down_clat(silent=True)
  _sudo([TAYGA_BIN, "-c", TAYGA_CONF, "--mktun"], check=True, timeout=10)
  _sudo(["ip", "link", "set", TUN_NAME, "up"], check=True)
  _sudo(["ip", "link", "set", TUN_NAME, "mtu", str(TUN_MTU)], check=True)
  _sudo(["ip", "-4", "addr", "add", f"{CLAT_IPV4_LOCAL}/32", "dev", TUN_NAME], check=True)
  _sudo(["ip", "-6", "route", "add", f"{map6}/128", "dev", TUN_NAME], check=True)
  _sudo(["ip", "-4", "route", "add", "default", "dev", TUN_NAME, "src", CLAT_IPV4_LOCAL,
         "metric", str(CLAT_ROUTE_METRIC)], check=True)
  _enable_forwarding()


def _tear_down_clat(silent: bool = False) -> None:
  # Remove in reverse order. Failures are expected if components weren't up.
  _sudo(["ip", "-4", "route", "del", "default", "dev", TUN_NAME, "metric", str(CLAT_ROUTE_METRIC)])
  # The /128 IPv6 route lookup needs the prefix; flush via the interface instead.
  _sudo(["ip", "-6", "route", "flush", "dev", TUN_NAME])
  _sudo(["ip", "link", "set", TUN_NAME, "down"])
  if os.path.exists(TAYGA_CONF):
    _sudo([TAYGA_BIN, "-c", TAYGA_CONF, "--rmtun"])
  if not silent:
    cloudlog.info("manage_clatd: clat0 torn down")


def _spawn_tayga() -> subprocess.Popen:
  cmd = ["sudo", "-n", TAYGA_BIN, "-c", TAYGA_CONF, "--nodetach", "--stdout"]
  cloudlog.info(f"manage_clatd: spawning {' '.join(cmd)}")
  return subprocess.Popen(cmd, start_new_session=False)


class _SignalForwarder:
  def __init__(self):
    self.proc: subprocess.Popen | None = None
    self.shutting_down = False
    signal.signal(signal.SIGTERM, self._handle)
    signal.signal(signal.SIGINT, self._handle)

  def _handle(self, signum, frame):
    self.shutting_down = True
    if self.proc and self.proc.poll() is None:
      cloudlog.info(f"manage_clatd: forwarding signal {signum} to tayga")
      try:
        self.proc.terminate()
        self.proc.wait(timeout=5)
      except subprocess.TimeoutExpired:
        self.proc.kill()
      except OSError:
        pass
    try:
      _tear_down_clat()
    except Exception:
      pass
    os._exit(0)


def _stop_tayga(forwarder: "_SignalForwarder") -> None:
  if forwarder.proc and forwarder.proc.poll() is None:
    try:
      forwarder.proc.terminate()
      forwarder.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
      forwarder.proc.kill()
    except OSError:
      pass
  forwarder.proc = None


def main() -> None:
  setproctitle("manage_clatd")
  forwarder = _SignalForwarder()
  _ensure_dirs()

  while not forwarder.shutting_down:
    if not is_clat_enabled():
      time.sleep(DISABLED_POLL_INTERVAL)
      continue

    if is_clat_install_requested():
      cloudlog.info("manage_clatd: building tayga")
      installer.install()
      clear_clat_install_request()

    if not is_clat_installed():
      time.sleep(DISABLED_POLL_INTERVAL)
      continue

    prefix = _bearer_ipv6_prefix()
    if not prefix:
      time.sleep(NO_BEARER_POLL_INTERVAL)
      continue

    map6 = f"{prefix}{CLAT_IPV6_SUFFIX}"
    try:
      _write_config(map6)
      _bring_up_clat(map6)
      forwarder.proc = _spawn_tayga()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
      cloudlog.exception(f"manage_clatd: setup failed: {e}")
      _tear_down_clat(silent=True)
      time.sleep(RESTART_BACKOFF)
      continue

    cloudlog.event("manage_clatd.up", prefix=prefix, map6=map6)

    # Supervise: react to disable, prefix change, or tayga exit.
    while not forwarder.shutting_down:
      if not is_clat_enabled():
        cloudlog.info("manage_clatd: enabled flag cleared, stopping")
        break
      if _bearer_ipv6_prefix() != prefix:
        cloudlog.info("manage_clatd: bearer IPv6 prefix changed, recycling")
        break
      try:
        exitcode = forwarder.proc.wait(timeout=RUNNING_POLL_INTERVAL)
        cloudlog.event("manage_clatd.tayga_exited", exitcode=exitcode)
        break
      except subprocess.TimeoutExpired:
        continue

    _stop_tayga(forwarder)
    _tear_down_clat()

    if not forwarder.shutting_down:
      time.sleep(RESTART_BACKOFF)


if __name__ == "__main__":
  main()
