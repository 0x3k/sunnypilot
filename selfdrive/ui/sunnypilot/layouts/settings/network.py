"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import threading
import time
import pyray as rl

from openpilot.selfdrive.ui.widgets.tailscale_dialog import TailscaleDialog
from openpilot.sunnypilot.tailscale import (
  is_tailscale_enabled, is_tailscale_install_requested, is_tailscale_installed,
  request_tailscale_install, set_tailscale_enabled,
)
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp, toggle_item_sp
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.widgets.network import NetworkUI, PanelType


class NetworkUISP(NetworkUI):
  def __init__(self, wifi_manager):
    super().__init__(wifi_manager)

    self.scan_button = Button(tr("Scan"), self._scan_clicked, button_style=ButtonStyle.NORMAL, font_size=60, border_radius=30)
    self.scan_button.set_rect(rl.Rectangle(0, 0, 400, 100))

    self._scanning = False
    self._wifi_manager.add_callbacks(networks_updated=self._on_networks_updated)

    self._install_tailscale_section()

  def _install_tailscale_section(self):
    """Append Tailscale controls to the Advanced Network Settings scroller."""
    self._tailscale_toggle = toggle_item_sp(
      title=tr("Tailscale"),
      description=tr("Connect this device to your tailnet for remote access over SSH. " +
                     "When enabled, Tailscale installs on first use and reconnects automatically after reboot."),
      initial_state=is_tailscale_enabled(),
      callback=self._tailscale_toggle_callback,
    )
    self._tailscale_install_btn = button_item_sp(
      title=tr("Install Tailscale"),
      button_text=tr("INSTALL"),
      description=tr("Download the Tailscale binaries (~15 MB) to /data/tailscale/bin."),
      callback=self._tailscale_install_callback,
    )
    self._tailscale_signin_btn = button_item_sp(
      title=tr("Sign in to Tailscale"),
      button_text=tr("SIGN IN"),
      description=tr("Open a QR code to authenticate this device with your tailnet."),
      callback=self._tailscale_signin_callback,
    )

    scroller = self._advanced_panel._scroller
    scroller.add_widget(self._tailscale_toggle)
    scroller.add_widget(self._tailscale_install_btn)
    scroller.add_widget(self._tailscale_signin_btn)

  def _tailscale_toggle_callback(self, state: bool):
    set_tailscale_enabled(state)

  def _tailscale_install_callback(self):
    request_tailscale_install()

  def _tailscale_signin_callback(self):
    gui_app.push_widget(TailscaleDialog())

  def _refresh_tailscale_state(self):
    enabled = is_tailscale_enabled()
    installed = is_tailscale_installed()
    self._tailscale_toggle.action_item.set_state(enabled)
    self._tailscale_install_btn.action_item.set_enabled(enabled and not installed)
    if is_tailscale_install_requested():
      self._tailscale_install_btn.action_item.set_text(tr("INSTALLING..."))
    elif installed:
      self._tailscale_install_btn.action_item.set_text(tr("INSTALLED"))
    else:
      self._tailscale_install_btn.action_item.set_text(tr("INSTALL"))
    self._tailscale_signin_btn.action_item.set_enabled(enabled and installed)

  def _update_state(self):
    super()._update_state()
    self._refresh_tailscale_state()

  def _scan_clicked(self):
    self._scanning = True
    self.scan_button.set_text(tr("Scanning..."))
    self.scan_button.set_enabled(False)

    threading.Thread(target=self._wifi_manager._update_networks, daemon=True).start()
    self._wifi_manager._request_scan()
    self._wifi_manager._last_network_update = time.monotonic()

  def _on_networks_updated(self, networks):
    if self._scanning:
      self._scanning = False
      self.scan_button.set_text(tr("Scan"))
      self.scan_button.set_enabled(True)

  def _render(self, rect: rl.Rectangle):
    super()._render(rect)

    if self._current_panel == PanelType.WIFI:
      self.scan_button.set_position(self._rect.x, self._rect.y + 20)
      self.scan_button.render()
