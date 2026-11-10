from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .const import DOMAIN
from .trimlight import DiscoveredDevice, TrimlightClient, TrimlightError, discover

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
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            client = TrimlightClient(host)
            try:
                await client.connect()
            except TrimlightError as err:
                _LOGGER.debug("Cannot connect to %s: %s", host, err)
                errors["base"] = "cannot_connect"
            else:
                device = self._discovered.get(host)
                return self.async_create_entry(title=device.name if device and device.name else f"Trimlight {host}", data={CONF_HOST: host})
            finally:
                await client.close()

        configured = {entry.unique_id for entry in self._async_current_entries()}
        unconfigured = [d for d in self._discovered.values() if d.host not in configured]
        default_host = (user_input or {}).get(CONF_HOST) or (unconfigured[0].host if unconfigured else "")
        found = ", ".join(f"{d.name} ({d.host})" for d in unconfigured) or "none"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=default_host): str}),
            errors=errors,
            description_placeholders={"found": found},
        )

