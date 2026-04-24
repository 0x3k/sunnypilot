import threading
import time

from openpilot.selfdrive.ui.widgets.tailscale_dialog import TailscaleDialog
from openpilot.sunnypilot.tailscale import (
  get_self_ip, is_signed_in, is_tailscale_enabled, is_tailscale_install_requested, is_tailscale_installed,
  request_tailscale_install, set_tailscale_enabled, uninstall_tailscale,
)
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.layouts.settings.network import WifiNetworkButton
from openpilot.selfdrive.ui.mici.layouts.settings.network.wifi_ui import WifiUIMici
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigMultiToggle, BigParamControl, BigToggle
from openpilot.selfdrive.ui.mici.widgets.dialog import BigInputDialog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.lib.prime_state import PrimeType
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.wifi_manager import WifiManager, Network, MeteredType


class NetworkLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()

    self._wifi_manager = WifiManager()
    self._wifi_manager.set_active(False)
    self._wifi_ui = WifiUIMici(self._wifi_manager)

    self._wifi_manager.add_callbacks(
      networks_updated=self._on_network_updated,
    )

    # ******** Tethering ********
    def tethering_toggle_callback(checked: bool):
      self._tethering_toggle_btn.set_enabled(False)
      self._tethering_password_btn.set_enabled(False)
      self._network_metered_btn.set_enabled(False)
      self._wifi_manager.set_tethering_active(checked)

    self._tethering_toggle_btn = BigToggle("enable tethering", "", toggle_callback=tethering_toggle_callback)

    def tethering_password_callback(password: str):
      if password:
        self._tethering_toggle_btn.set_enabled(False)
        self._tethering_password_btn.set_enabled(False)
        self._wifi_manager.set_tethering_password(password)

    def tethering_password_clicked():
      tethering_password = self._wifi_manager.tethering_password
      dlg = BigInputDialog("enter password...", tethering_password, minimum_length=8,
                           confirm_callback=tethering_password_callback)
      gui_app.push_widget(dlg)

    txt_tethering = gui_app.texture("icons_mici/settings/network/tethering.png", 64, 54)
    self._tethering_password_btn = BigButton("tethering password", "", txt_tethering)
    self._tethering_password_btn.set_click_callback(tethering_password_clicked)

    # ******** Network Metered ********
    def network_metered_callback(value: str):
      self._network_metered_btn.set_enabled(False)
      metered = {
        'default': MeteredType.UNKNOWN,
        'metered': MeteredType.YES,
        'unmetered': MeteredType.NO
      }.get(value, MeteredType.UNKNOWN)
      self._wifi_manager.set_current_network_metered(metered)

    # TODO: signal for current network metered type when changing networks, this is wrong until you press it once
    # TODO: disable when not connected
    self._network_metered_btn = BigMultiToggle("network usage", ["default", "metered", "unmetered"], select_callback=network_metered_callback)
    self._network_metered_btn.set_enabled(False)

    self._wifi_button = WifiNetworkButton(self._wifi_manager)
    self._wifi_button.set_click_callback(lambda: gui_app.push_widget(self._wifi_ui))

    # ******** Advanced settings ********
    # ******** Roaming toggle ********
    self._roaming_btn = BigParamControl("enable roaming", "GsmRoaming")

    # ******** APN settings ********
    self._apn_btn = BigButton("apn settings", "edit")
    self._apn_btn.set_click_callback(self._edit_apn)

    # ******** Cellular metered toggle ********
    self._cellular_metered_btn = BigParamControl("cellular metered", "GsmMetered")

    # ******** Tailscale ********
    self._tailscale_toggle_btn = BigToggle(
      "tailscale",
      initial_state=is_tailscale_enabled(),
      toggle_callback=lambda state: set_tailscale_enabled(state),
    )
    self._tailscale_install_btn = BigButton("install tailscale", "install")
    self._tailscale_install_btn.set_click_callback(lambda: request_tailscale_install())
    self._tailscale_uninstall_btn = BigButton("uninstall tailscale", "uninstall")
    self._tailscale_uninstall_btn.set_click_callback(lambda: uninstall_tailscale())
    self._tailscale_signin_btn = BigButton("sign in to tailscale", "sign in")
    self._tailscale_signin_btn.set_click_callback(lambda: gui_app.push_widget(TailscaleDialog()))
    self._tailscale_status_btn = BigButton("tailscale", "")

    # Cached tailscale state refreshed off-thread so _update_state doesn't block on the CLI.
    self._ts_lock = threading.Lock()
    self._ts_signed_in = False
    self._ts_self_ip = ""
    self._ts_refresh_scheduled = 0.0
    self._ts_refresh_interval = 3.0

    # Main scroller ----------------------------------
    self._scroller.add_widgets([
      self._wifi_button,
      self._network_metered_btn,
      self._tethering_toggle_btn,
      self._tethering_password_btn,
      # /* Advanced settings
      self._roaming_btn,
      self._apn_btn,
      self._cellular_metered_btn,
      # */
      self._tailscale_toggle_btn,
      self._tailscale_install_btn,
      self._tailscale_uninstall_btn,
      self._tailscale_status_btn,
      self._tailscale_signin_btn,
    ])

  def _update_state(self):
    super()._update_state()

    # If not using prime SIM, show GSM settings and enable IPv4 forwarding
    show_cell_settings = ui_state.prime_state.get_type() in (PrimeType.NONE, PrimeType.LITE)
    self._wifi_manager.set_ipv4_forward(show_cell_settings)
    self._roaming_btn.set_visible(show_cell_settings)
    self._apn_btn.set_visible(show_cell_settings)
    self._cellular_metered_btn.set_visible(show_cell_settings)

    # Tailscale: refresh toggle state from the file flag, swap visible buttons based on install / sign-in
    enabled = is_tailscale_enabled()
    installed = is_tailscale_installed()
    self._refresh_tailscale_async(installed)
    with self._ts_lock:
      signed_in = self._ts_signed_in
      self_ip = self._ts_self_ip
    # A local toggle without a daemon round-trip is authoritative for the checkbox; the backend
    # state only matters for which *other* buttons are visible.
    self._tailscale_toggle_btn.set_checked(enabled)

    self._tailscale_install_btn.set_visible(enabled and not installed)
    if is_tailscale_install_requested():
      self._tailscale_install_btn.set_value("installing...")
    else:
      self._tailscale_install_btn.set_value("install")

    self._tailscale_uninstall_btn.set_visible(enabled and installed)

    self._tailscale_signin_btn.set_visible(enabled and installed and not signed_in)

    self._tailscale_status_btn.set_visible(enabled and installed and signed_in)
    if signed_in:
      self._tailscale_status_btn.set_value(self_ip or "connected")

  def show_event(self):
    super().show_event()
    self._wifi_manager.set_active(True)

    # Process wifi callbacks while at any point in the nav stack
    gui_app.add_nav_stack_tick(self._wifi_manager.process_callbacks)

  def hide_event(self):
    super().hide_event()
    self._wifi_manager.set_active(False)

    gui_app.remove_nav_stack_tick(self._wifi_manager.process_callbacks)

  def _edit_apn(self):
    def update_apn(apn: str):
      apn = apn.strip()
      if apn == "":
        ui_state.params.remove("GsmApn")
      else:
        ui_state.params.put("GsmApn", apn)

    current_apn = ui_state.params.get("GsmApn") or ""
    dlg = BigInputDialog("enter APN...", current_apn, minimum_length=0, confirm_callback=update_apn)
    gui_app.push_widget(dlg)

  def _refresh_tailscale_async(self, installed: bool) -> None:
    now = time.monotonic()
    if now - self._ts_refresh_scheduled < self._ts_refresh_interval:
      return
    self._ts_refresh_scheduled = now

    if not installed:
      with self._ts_lock:
        self._ts_signed_in = False
        self._ts_self_ip = ""
      return

    def refresh():
      signed_in = is_signed_in()
      ip = get_self_ip() if signed_in else ""
      with self._ts_lock:
        self._ts_signed_in = signed_in
        self._ts_self_ip = ip

    threading.Thread(target=refresh, daemon=True).start()

  def _on_network_updated(self, networks: list[Network]):
    # Update tethering state
    tethering_active = self._wifi_manager.is_tethering_active()
    # TODO: use real signals (like activated/settings changed, etc.) to speed up re-enabling buttons
    self._tethering_toggle_btn.set_enabled(True)
    self._tethering_password_btn.set_enabled(True)
    self._network_metered_btn.set_enabled(lambda: not tethering_active and bool(self._wifi_manager.ipv4_address))
    self._tethering_toggle_btn.set_checked(tethering_active)

    # Update network metered
    self._network_metered_btn.set_value(
      {
        MeteredType.UNKNOWN: 'default',
        MeteredType.YES: 'metered',
        MeteredType.NO: 'unmetered'
      }.get(self._wifi_manager.current_network_metered, 'default'))
