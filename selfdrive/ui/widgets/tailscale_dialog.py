"""
On-device Tailscale sign-in dialog.

Spawns `tailscale up` in the background so tailscaled emits a login URL on stderr,
parses the URL, renders it as a QR code (same raylib+qrcode pattern as
pairing_dialog.py), and auto-closes when BackendState transitions to Running.
"""
import re
import subprocess
import threading
import time

import numpy as np
import pyray as rl
import qrcode

from openpilot.common.swaglog import cloudlog
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import IconButton
from openpilot.sunnypilot.tailscale import (
  TAILSCALE_BIN,
  TAILSCALE_SOCKET,
  get_backend_state,
  is_tailscale_installed,
  run_tailscale_cli,
)

LOGIN_URL_RE = re.compile(r"https://login\.tailscale\.com/\S+")
STATUS_POLL_INTERVAL = 1.0


class TailscaleDialog(Widget):
  """Runs `tailscale up`, captures the login URL, renders QR until signed in."""

  def __init__(self):
    super().__init__()
    self._close_btn = IconButton(gui_app.texture("icons/close.png", 80, 80))
    self._close_btn.set_click_callback(self._on_close)

    self._qr_texture: rl.Texture | None = None
    self._login_url: str = ""
    self._status_text: str = tr("Starting Tailscale sign-in...")
    self._backend_state: str = ""
    self._proc: subprocess.Popen | None = None
    self._stderr_thread: threading.Thread | None = None
    self._last_poll = 0.0
    self._lock = threading.Lock()

    if not is_tailscale_installed():
      self._status_text = tr("Tailscale is not installed yet. Install it first.")
      return

    self._start_up()

  def _start_up(self) -> None:
    # Best-effort hostname — tailscaled may already know it from state, so this is a fallback.
    try:
      self._proc = subprocess.Popen(
        [TAILSCALE_BIN, f"--socket={TAILSCALE_SOCKET}", "up", "--ssh"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
      )
    except OSError as e:
      cloudlog.exception(f"TailscaleDialog: failed to spawn tailscale up: {e}")
      with self._lock:
        self._status_text = tr("Failed to start sign-in. Check Tailscale is installed.")
      return

    self._stderr_thread = threading.Thread(target=self._read_url, daemon=True)
    self._stderr_thread.start()

  def _read_url(self) -> None:
    assert self._proc is not None
    for stream in (self._proc.stderr, self._proc.stdout):
      if stream is None:
        continue
      try:
        for line in iter(stream.readline, ""):
          if not line:
            break
          m = LOGIN_URL_RE.search(line)
          if m:
            with self._lock:
              self._login_url = m.group(0)
              self._status_text = tr("Scan the QR code with your phone to sign in.")
            self._generate_qr(m.group(0))
            return
      except (OSError, ValueError):
        return

  def _generate_qr(self, url: str) -> None:
    try:
      qr = qrcode.QRCode(
        version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10, border=4,
      )
      qr.add_data(url)
      qr.make(fit=True)
      pil_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
      img_array = np.array(pil_img, dtype=np.uint8)

      rl_image = rl.Image()
      rl_image.data = rl.ffi.cast("void *", img_array.ctypes.data)
      rl_image.width = pil_img.width
      rl_image.height = pil_img.height
      rl_image.mipmaps = 1
      rl_image.format = rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8

      texture = rl.load_texture_from_image(rl_image)
      with self._lock:
        if self._qr_texture and self._qr_texture.id != 0:
          rl.unload_texture(self._qr_texture)
        self._qr_texture = texture
    except Exception:
      cloudlog.exception("TailscaleDialog: QR generation failed")

  def _poll_state(self) -> None:
    now = time.monotonic()
    if now - self._last_poll < STATUS_POLL_INTERVAL:
      return
    self._last_poll = now
    state = get_backend_state()
    with self._lock:
      self._backend_state = state
    if state == "Running":
      with self._lock:
        self._status_text = tr("Signed in. Tailscale is active.")
      # let the user see the success state briefly before auto-close
      gui_app.pop_widget()

  def _on_close(self) -> None:
    self._shutdown()
    gui_app.pop_widget()

  def _shutdown(self) -> None:
    if self._proc and self._proc.poll() is None:
      try:
        self._proc.terminate()
        self._proc.wait(timeout=2)
      except subprocess.TimeoutExpired:
        self._proc.kill()
      except OSError:
        pass
    # If the user cancelled without signing in, tell tailscaled to stop the login attempt
    # so the next sign-in starts fresh.
    try:
      run_tailscale_cli(["logout"], timeout=3)
    except Exception:
      pass

  def _render(self, rect: rl.Rectangle) -> int:
    rl.clear_background(rl.Color(224, 224, 224, 255))
    self._poll_state()

    margin = 70
    content = rl.Rectangle(rect.x + margin, rect.y + margin, rect.width - 2 * margin, rect.height - 2 * margin)

    close_size = 80
    pad = 20
    self._close_btn.render(rl.Rectangle(content.x - pad, content.y - pad, close_size + pad * 2, close_size + pad * 2))

    y = content.y + close_size + 40
    title_font = gui_app.font(FontWeight.NORMAL)
    title = tr("Sign in to Tailscale")
    left_width = int(content.width * 0.5 - 15)

    title_wrapped = wrap_text(title_font, title, 75, left_width)
    rl.draw_text_ex(title_font, "\n".join(title_wrapped), rl.Vector2(content.x, y), 75, 0.0, rl.BLACK)
    y += len(title_wrapped) * 75 + 60

    body_font = gui_app.font(FontWeight.BOLD)
    with self._lock:
      status = self._status_text
      url = self._login_url
    status_wrapped = wrap_text(body_font, status, 40, left_width)
    rl.draw_text_ex(body_font, "\n".join(status_wrapped), rl.Vector2(content.x, y), 40, 0.0, rl.BLACK)
    y += len(status_wrapped) * 40 + 40

    if url:
      url_wrapped = wrap_text(body_font, url, 28, left_width)
      rl.draw_text_ex(body_font, "\n".join(url_wrapped), rl.Vector2(content.x, y), 28, 0.0, rl.Color(60, 60, 60, 255))

    # QR on the right
    right_width = content.width // 2 - 20
    qr_size = int(min(right_width, content.height) - 40)
    qr_x = int(content.x + left_width + 40 + (right_width - qr_size) // 2)
    qr_y = int(content.y)
    self._render_qr(rl.Rectangle(qr_x, qr_y, qr_size, qr_size))

    return -1

  def _render_qr(self, rect: rl.Rectangle) -> None:
    with self._lock:
      tex = self._qr_texture
    if not tex:
      rl.draw_rectangle_rounded(rect, 0.1, 20, rl.Color(240, 240, 240, 255))
      font = gui_app.font(FontWeight.BOLD)
      msg = tr("Generating sign-in link...")
      size = measure_text_cached(font, msg, 30)
      rl.draw_text_ex(
        font, msg,
        rl.Vector2(rect.x + (rect.width - size.x) // 2, rect.y + rect.height // 2 - 15),
        30, 0.0, rl.Color(80, 80, 80, 255),
      )
      return
    source = rl.Rectangle(0, 0, tex.width, tex.height)
    rl.draw_texture_pro(tex, source, rect, rl.Vector2(0, 0), 0, rl.WHITE)

  def __del__(self):
    self._shutdown()
    with self._lock:
      if self._qr_texture and self._qr_texture.id != 0:
        rl.unload_texture(self._qr_texture)
        self._qr_texture = None
