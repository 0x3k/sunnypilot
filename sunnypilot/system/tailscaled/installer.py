"""
Downloads tailscaled + tailscale static binaries into /data/tailscale/bin.

Pattern mirrors sunnypilot/mapd/mapd_installer.py (requests + streaming + retry + atomic replace).
aarch64 only — comma-four is the only supported target for now.
"""
import os
import shutil
import stat
import tarfile
import tempfile
import time
from pathlib import Path

import requests

from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.tailscale import (
  TAILSCALE_BIN_DIR,
  TAILSCALED_BIN,
  TAILSCALE_BIN,
)

TAILSCALE_VERSION = "1.80.3"
TAILSCALE_URL = (
  f"https://pkgs.tailscale.com/stable/tailscale_{TAILSCALE_VERSION}_arm64.tgz"
)

DOWNLOAD_TIMEOUT = 120
NUM_RETRIES = 3


def _ensure_dirs() -> None:
  os.makedirs(TAILSCALE_BIN_DIR, exist_ok=True)


def _chmod_exec(path: str) -> None:
  current = stat.S_IMODE(os.lstat(path).st_mode)
  os.chmod(path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract_binaries(archive_path: Path, dest_dir: str) -> None:
  """Pull just the tailscaled and tailscale binaries out of the upstream tarball."""
  with tarfile.open(archive_path, "r:gz") as tar:
    for member in tar.getmembers():
      name = os.path.basename(member.name)
      if name in ("tailscaled", "tailscale") and member.isfile():
        extracted = tar.extractfile(member)
        if extracted is None:
          continue
        final_path = os.path.join(dest_dir, name)
        tmp_path = final_path + ".tmp"
        with open(tmp_path, "wb") as out:
          shutil.copyfileobj(extracted, out)
          out.flush()
          os.fsync(out.fileno())
        os.replace(tmp_path, final_path)
        _chmod_exec(final_path)


def install() -> bool:
  """Blocking install. Returns True on success."""
  _ensure_dirs()
  with tempfile.TemporaryDirectory(prefix="tailscale_dl_") as tmpdir:
    archive = Path(tmpdir) / "tailscale.tgz"
    for attempt in range(NUM_RETRIES):
      try:
        cloudlog.info(f"tailscale installer: downloading {TAILSCALE_URL} (attempt {attempt + 1})")
        with requests.get(TAILSCALE_URL, stream=True, timeout=DOWNLOAD_TIMEOUT) as resp:
          resp.raise_for_status()
          with open(archive, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
              if chunk:
                f.write(chunk)
        _extract_binaries(archive, TAILSCALE_BIN_DIR)
        if os.path.exists(TAILSCALED_BIN) and os.path.exists(TAILSCALE_BIN):
          cloudlog.info("tailscale installer: binaries installed")
          return True
        cloudlog.error("tailscale installer: archive had no tailscaled/tailscale binaries")
        return False
      except requests.exceptions.RequestException as e:
        cloudlog.warning(f"tailscale installer: download failed ({e}); retrying")
        time.sleep(2)
      except (tarfile.TarError, OSError) as e:
        cloudlog.exception(f"tailscale installer: extract failed ({e})")
        return False
  cloudlog.error("tailscale installer: all retries exhausted")
  return False
