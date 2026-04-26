"""
File-based CLAT (464XLAT) integration helpers.

Flags and state live under /data/clat/ so they survive reboot and OTA.
File-based (not Params) to avoid a C++ rebuild on prebuilt release branches —
same rationale as sunnypilot/private_mode.py and sunnypilot/tailscale.py.

CLAT translates IPv4 traffic to IPv6 so the device can reach IPv4 destinations
when the cellular bearer is IPv6-only with NAT64/PLAT (Google Fi data-only SIMs,
T-Mobile, etc.). Without CLAT on these networks, IPv4 ICMP works but IPv4 TCP
silently times out.
"""
import os
import shutil

CLAT_ROOT = "/data/clat"
CLAT_BIN_DIR = os.path.join(CLAT_ROOT, "bin")
CLAT_BUILD_DIR = os.path.join(CLAT_ROOT, "build")
CLAT_STATE_DIR = os.path.join(CLAT_ROOT, "state")
CLAT_RUN_DIR = os.path.join(CLAT_ROOT, "run")

TAYGA_BIN = os.path.join(CLAT_BIN_DIR, "tayga")
TAYGA_CONF = os.path.join(CLAT_RUN_DIR, "tayga.conf")

CLAT_ENABLED_FLAG = os.path.join(CLAT_ROOT, "enabled")
CLAT_INSTALL_REQUESTED_FLAG = os.path.join(CLAT_ROOT, "install_requested")

# clat0 IPv4 numbering (RFC 7335 reserves 192.0.0.0/29 for 464XLAT).
CLAT_IPV4_LOCAL = "192.0.0.1"
CLAT_IPV4_TAYGA = "192.0.0.2"

# Well-known NAT64 prefix (RFC 6052/8215). Probed working on Google Fi / T-Mobile.
NAT64_PREFIX = "64:ff9b::/96"

# Loses to wlan0 (default 600); wins over native ppp0 IPv4 default (1000).
CLAT_ROUTE_METRIC = 800

# IPv6 host suffix for the CLAT endpoint claimed from the bearer's /64.
# Picked to be unlikely to collide with SLAAC privacy addresses.
CLAT_IPV6_SUFFIX = "feed:c1a7"


def _ensure_root() -> None:
  os.makedirs(CLAT_ROOT, exist_ok=True)


def is_clat_enabled() -> bool:
  return os.path.exists(CLAT_ENABLED_FLAG)


def set_clat_enabled(enabled: bool) -> None:
  if enabled:
    _ensure_root()
    with open(CLAT_ENABLED_FLAG, "w") as f:
      f.write("1")
  else:
    try:
      os.remove(CLAT_ENABLED_FLAG)
    except FileNotFoundError:
      pass


def is_clat_installed() -> bool:
  return os.path.exists(TAYGA_BIN) and os.access(TAYGA_BIN, os.X_OK)


def request_clat_install() -> None:
  _ensure_root()
  with open(CLAT_INSTALL_REQUESTED_FLAG, "w") as f:
    f.write("1")


def clear_clat_install_request() -> None:
  try:
    os.remove(CLAT_INSTALL_REQUESTED_FLAG)
  except FileNotFoundError:
    pass


def is_clat_install_requested() -> bool:
  return os.path.exists(CLAT_INSTALL_REQUESTED_FLAG)


def uninstall_clat() -> bool:
  """Remove the tayga binary directory. Returns True if something was removed."""
  if not os.path.isdir(CLAT_BIN_DIR):
    return False
  try:
    shutil.rmtree(CLAT_BIN_DIR)
    return True
  except OSError:
    return False
