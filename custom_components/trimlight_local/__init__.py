"""Local control of Trimlight Edge controllers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from . import websocket
from .const import DOMAIN, PANEL_URL_PATH, SUN_UPDATE_HOUR
from .coordinator import TrimlightCoordinator
from .trimlight import TrimlightClient

PLATFORMS = [Platform.CALENDAR, Platform.LIGHT, Platform.NUMBER, Platform.SELECT, Platform.SWITCH, Platform.TIME]
UI_URL = f"/{DOMAIN}/trimlight-ui.js"
UI_FILE = Path(__file__).parent / "frontend" / "trimlight-ui.js"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

TrimlightConfigEntry = ConfigEntry[TrimlightCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the Trimlight page, the pattern card and the data they read, so installing the integration installs them too."""
    websocket.async_register(hass)
    await hass.http.async_register_static_paths([StaticPathConfig(UI_URL, str(UI_FILE), True)])
    if "frontend" in hass.config.components:
        # The file is served with long cache headers; a new version query makes browsers fetch an updated one.
        version = hashlib.sha256(await hass.async_add_executor_job(UI_FILE.read_bytes)).hexdigest()[:8]
        module_url = f"{UI_URL}?v={version}"
        add_extra_js_url(hass, module_url)
        await panel_custom.async_register_panel(
            hass, frontend_url_path=PANEL_URL_PATH, webcomponent_name="trimlight-panel", sidebar_title="Trimlight", sidebar_icon="mdi:led-strip-variant", module_url=module_url
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TrimlightConfigEntry) -> bool:
    # Every sync also sets the controller's clock, so use Home Assistant's timezone rather than the host OS's.
    client = TrimlightClient(entry.data[CONF_HOST], clock=dt_util.now)
    coordinator = TrimlightCoordinator(hass, entry, client)
    await coordinator.async_load_stored()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(async_track_time_change(hass, coordinator.async_refresh_schedules, hour=SUN_UPDATE_HOUR, minute=0, second=0))
    entry.async_create_background_task(hass, coordinator.async_refresh_schedules(), "trimlight_local schedule dates and times")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TrimlightConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.close()
    return unloaded
