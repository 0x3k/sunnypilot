"""
File-based Tailscale integration helpers.

Flags and state live under /data/tailscale/ so they survive reboot and OTA.
File-based (not Params) to avoid a C++ rebuild on prebuilt release branches —
same rationale as sunnypilot/private_mode.py.
"""
import json
import os
import subprocess

TAILSCALE_ROOT = "/data/tailscale"
TAILSCALE_BIN_DIR = os.path.join(TAILSCALE_ROOT, "bin")
TAILSCALE_STATE_DIR = os.path.join(TAILSCALE_ROOT, "state")

TAILSCALED_BIN = os.path.join(TAILSCALE_BIN_DIR, "tailscaled")
TAILSCALE_BIN = os.path.join(TAILSCALE_BIN_DIR, "tailscale")

TAILSCALE_SOCKET = os.path.join(TAILSCALE_ROOT, "tailscaled.sock")
TAILSCALE_STATE_FILE = os.path.join(TAILSCALE_STATE_DIR, "tailscaled.state")

TAILSCALE_ENABLED_FLAG = os.path.join(TAILSCALE_ROOT, "enabled")
TAILSCALE_INSTALL_REQUESTED_FLAG = os.path.join(TAILSCALE_ROOT, "install_requested")
TAILSCALE_AUTHKEY_FILE = os.path.join(TAILSCALE_ROOT, "authkey")

CLI_TIMEOUT = 10


def _ensure_root() -> None:
  os.makedirs(TAILSCALE_ROOT, exist_ok=True)


def is_tailscale_enabled() -> bool:
  return os.path.exists(TAILSCALE_ENABLED_FLAG)


def set_tailscale_enabled(enabled: bool) -> None:
  if enabled:
    _ensure_root()
    with open(TAILSCALE_ENABLED_FLAG, "w") as f:
      f.write("1")
  else:
    try:
      os.remove(TAILSCALE_ENABLED_FLAG)
    except FileNotFoundError:
      pass


def is_tailscale_installed() -> bool:
  return (os.path.exists(TAILSCALED_BIN) and os.access(TAILSCALED_BIN, os.X_OK)
          and os.path.exists(TAILSCALE_BIN) and os.access(TAILSCALE_BIN, os.X_OK))


def request_tailscale_install() -> None:
  _ensure_root()
  with open(TAILSCALE_INSTALL_REQUESTED_FLAG, "w") as f:
    f.write("1")


def clear_tailscale_install_request() -> None:
  try:
    os.remove(TAILSCALE_INSTALL_REQUESTED_FLAG)
  except FileNotFoundError:
    pass


def is_tailscale_install_requested() -> bool:
  return os.path.exists(TAILSCALE_INSTALL_REQUESTED_FLAG)


def read_authkey() -> str:
  try:
    with open(TAILSCALE_AUTHKEY_FILE) as f:
      return f.read().strip()
  except FileNotFoundError:
    return ""


def write_authkey(key: str) -> None:
  _ensure_root()
  with open(TAILSCALE_AUTHKEY_FILE, "w") as f:
    f.write(key)
  os.chmod(TAILSCALE_AUTHKEY_FILE, 0o600)


def run_tailscale_cli(args: list[str], timeout: float = CLI_TIMEOUT) -> subprocess.CompletedProcess:
  """Run `tailscale <args>` pointed at our socket. Caller handles non-zero returncode."""
  return subprocess.run(
    [TAILSCALE_BIN, f"--socket={TAILSCALE_SOCKET}", *args],
    capture_output=True, text=True, timeout=timeout, check=False,
  )


def get_tailscale_status() -> dict | None:
  """Parsed `tailscale status --json` output, or None if the daemon isn't responding."""
  if not is_tailscale_installed():
    return None
  try:
    result = run_tailscale_cli(["status", "--json"], timeout=5)
    if result.returncode != 0:
      return None
    return json.loads(result.stdout)
  except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
    return None


def get_backend_state() -> str:
  """One of: 'NoState', 'NeedsLogin', 'NeedsMachineAuth', 'Stopped', 'Starting', 'Running', or '' if unknown."""
  status = get_tailscale_status()
  if status is None:
    return ""
  return str(status.get("BackendState", ""))


def is_signed_in() -> bool:
  return get_backend_state() == "Running"
