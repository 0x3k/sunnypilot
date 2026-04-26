"""
Builds tayga from source into /data/clat/bin/tayga.

Tayga upstream does not publish prebuilt binaries, so we clone a pinned commit
of apalrd/tayga and run `make` against the AGNOS toolchain (gcc 13). The result
is a small (~150 KB) aarch64 ELF dynamically linked against AGNOS's libc.

Pattern follows sunnypilot/system/tailscaled/installer.py — retries, atomic
replace, no-op when already installed.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import time

from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.clatd import (
  CLAT_BIN_DIR,
  TAYGA_BIN,
)

TAYGA_REPO = "https://github.com/apalrd/tayga.git"
TAYGA_COMMIT = "8a028c787c86389a75dd4ef8cc01c9501133e966"

CLONE_TIMEOUT = 120
BUILD_TIMEOUT = 180
NUM_RETRIES = 3


def _ensure_dirs() -> None:
  os.makedirs(CLAT_BIN_DIR, exist_ok=True)


def _chmod_exec(path: str) -> None:
  current = stat.S_IMODE(os.lstat(path).st_mode)
  os.chmod(path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _clone_pinned(dest: str) -> None:
  # Shallow clone is fine because we fetch a specific commit by SHA and don't
  # need history. `git -c advice.detachedHead=false` quiets the detached-HEAD warning.
  subprocess.run(
    ["git", "clone", "--quiet", TAYGA_REPO, dest],
    check=True, capture_output=True, timeout=CLONE_TIMEOUT,
  )
  subprocess.run(
    ["git", "-C", dest, "-c", "advice.detachedHead=false", "checkout", "--quiet", TAYGA_COMMIT],
    check=True, capture_output=True, timeout=30,
  )


def _build(src_dir: str) -> str:
  """Run `make` in src_dir; return path to the built binary."""
  subprocess.run(
    ["make", "-C", src_dir],
    check=True, capture_output=True, timeout=BUILD_TIMEOUT,
  )
  built = os.path.join(src_dir, "tayga")
  if not os.path.exists(built):
    raise FileNotFoundError(f"tayga binary not produced at {built}")
  return built


def install() -> bool:
  """Blocking install. Returns True on success."""
  _ensure_dirs()
  for attempt in range(NUM_RETRIES):
    with tempfile.TemporaryDirectory(prefix="tayga_build_") as tmpdir:
      src_dir = os.path.join(tmpdir, "tayga")
      try:
        cloudlog.info(f"clat installer: cloning {TAYGA_REPO} @ {TAYGA_COMMIT[:12]} (attempt {attempt + 1})")
        _clone_pinned(src_dir)
        cloudlog.info("clat installer: building tayga")
        built = _build(src_dir)

        tmp_path = TAYGA_BIN + ".tmp"
        shutil.copyfile(built, tmp_path)
        _chmod_exec(tmp_path)
        os.replace(tmp_path, TAYGA_BIN)
        cloudlog.info(f"clat installer: installed {TAYGA_BIN}")
        return True
      except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else ""
        cloudlog.warning(f"clat installer: command failed (rc={e.returncode}): {stderr.strip()[:500]}")
      except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        cloudlog.warning(f"clat installer: attempt {attempt + 1} failed ({e})")
      time.sleep(2)
  cloudlog.error("clat installer: all retries exhausted")
  return False
