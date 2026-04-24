"""
Tailscale settings panel for sunnypilot.

File-based config under /data/tailscale/ — no Params, no C++ rebuild required
on prebuilt branches (same rationale as sunnypilot/private_mode.py).
"""
import pyray as rl

from openpilot.selfdrive.ui.widgets.tailscale_dialog import TailscaleDialog
from openpilot.sunnypilot.tailscale import (
  is_tailscale_enabled, is_tailscale_install_requested, is_tailscale_installed,
  request_tailscale_install, set_tailscale_enabled,
)
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp, toggle_item_sp
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller_tici import Scroller, LineSeparator


class TailscaleHeader(Widget):
  def __init__(self):
    super().__init__()
    self._title = UnifiedLabel(
      text=tr("Tailscale"),
      font_size=80,
      font_weight=FontWeight.BOLD,
      text_color=rl.WHITE,
      alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
      alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_TOP,
      wrap_text=False,
      elide=False,
    )
    self._description = UnifiedLabel(
      text=tr("Secure remote access over your tailnet. SSH is enabled via Tailscale SSH."),
      font_size=36,
      font_weight=FontWeight.NORMAL,
      text_color=rl.Color(180, 180, 180, 255),
      alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
      alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_TOP,
      wrap_text=True,
      elide=False,
    )
    self._padding = 20
    self._spacing = 12

  def set_parent_rect(self, parent_rect: rl.Rectangle) -> None:
    super().set_parent_rect(parent_rect)
    content_width = int(parent_rect.width - (self._padding * 2))
    title_h = self._title.get_content_height(content_width)
    desc_h = self._description.get_content_height(content_width)
    self._rect.width = parent_rect.width
    self._rect.height = self._padding + title_h + self._spacing + desc_h + self._padding

  def _render(self, rect: rl.Rectangle):
    content_width = rect.width - (self._padding * 2)
    y = rect.y + self._padding
    title_h = self._title.get_content_height(int(content_width))
    self._title.render(rl.Rectangle(rect.x + self._padding, y, content_width, title_h))
    y += title_h + self._spacing
    desc_h = self._description.get_content_height(int(content_width))
    self._description.render(rl.Rectangle(rect.x + self._padding, y, content_width, desc_h))


class TailscaleLayout(Widget):
  def __init__(self):
    super().__init__()
    self._enable_toggle = toggle_item_sp(
      title=tr("Enable Tailscale"),
      description=tr("Master switch. When enabled, the device installs Tailscale on first use " +
                     "and reconnects automatically after reboot."),
      initial_state=is_tailscale_enabled(),
      callback=self._enable_callback,
    )
    self._install_btn = button_item_sp(
      title=tr("Install Tailscale"),
      button_text=tr("INSTALL"),
      description=tr("Download the Tailscale binaries (~15 MB) into /data/tailscale/bin. " +
                     "Requires a network connection."),
      callback=self._install_callback,
    )
    self._signin_btn = button_item_sp(
      title=tr("Sign in to Tailscale"),
      button_text=tr("SIGN IN"),
      description=tr("Open a QR code to authenticate this device with your tailnet."),
      callback=self._signin_callback,
    )

    items = [
      TailscaleHeader(),
      LineSeparator(),
      self._enable_toggle,
      LineSeparator(),
      self._install_btn,
      LineSeparator(),
      self._signin_btn,
    ]
    self._scroller = Scroller(items, line_separator=False, spacing=0)

  def _enable_callback(self, state: bool):
    set_tailscale_enabled(state)

  def _install_callback(self):
    request_tailscale_install()

  def _signin_callback(self):
    gui_app.push_widget(TailscaleDialog())

  def _update_state(self):
    super()._update_state()
    enabled = is_tailscale_enabled()
    installed = is_tailscale_installed()
    self._enable_toggle.action_item.set_state(enabled)

    self._install_btn.action_item.set_enabled(enabled and not installed)
    if is_tailscale_install_requested():
      self._install_btn.action_item.set_text(tr("INSTALLING..."))
    else:
      self._install_btn.action_item.set_text(tr("INSTALL") if not installed else tr("INSTALLED"))

    self._signin_btn.action_item.set_enabled(enabled and installed)

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    super().show_event()
    self._scroller.show_event()
