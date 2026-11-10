from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .const import CONF_DEVICE_CODE, DOMAIN
from .trimlight import ControllerBusy, DiscoveredDevice, TrimlightClient, TrimlightError, discover

_LOGGER = logging.getLogger(__name__)


class TrimlightConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, DiscoveredDevice] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._discovered is None:
            try:
                self._discovered = {d.host: d for d in await discover(timeout=2.0)}
            except OSError as err:
                _LOGGER.debug("Discovery failed: %r", err)
                self._discovered = {}
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            device = self._discovered.get(host)
            if device is not None and device.device_code:
                for entry in self._async_current_entries():
                    if entry.data.get(CONF_DEVICE_CODE) == device.device_code:
                        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: host})
                        return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            client = TrimlightClient(host)
            try:
                await client.connect()
            except ControllerBusy:
                errors["base"] = "busy"
            except TrimlightError as err:
                _LOGGER.debug("Cannot connect to %s: %s", host, err)
                errors["base"] = "cannot_connect"
            else:
                data = {CONF_HOST: host, **({CONF_DEVICE_CODE: device.device_code} if device and device.device_code else {})}
                return self.async_create_entry(title=device.name if device and device.name else "Trimlight", data=data)
            finally:
                await client.close()

        configured = {entry.unique_id for entry in self._async_current_entries()}
        unconfigured = [d for d in self._discovered.values() if d.host not in configured]
        found = ", ".join(f"{d.name} ({d.host})" for d in unconfigured) or "none"
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(vol.Schema({vol.Required(CONF_HOST): str}), user_input or {}),
            errors=errors,
            description_placeholders={"found": found},
        )

