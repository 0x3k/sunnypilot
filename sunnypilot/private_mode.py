import os

PRIVATE_MODE_FLAG = "/data/private_mode"


def is_private_mode() -> bool:
  return os.path.exists(PRIVATE_MODE_FLAG)


def set_private_mode(enabled: bool) -> None:
  if enabled:
    with open(PRIVATE_MODE_FLAG, "w") as f:
      f.write("1")
  else:
    try:
      os.remove(PRIVATE_MODE_FLAG)
    except FileNotFoundError:
      pass
