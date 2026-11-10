from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TrimlightConfigEntry
from .coordinator import TrimlightCoordinator
from .entity import TrimlightEntity
from .music import DELAY_RANGE_MS


async def async_setup_entry(hass: HomeAssistant, entry: TrimlightConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([TrimlightSettingNumber(coordinator, entry, "speed", 0, 255), TrimlightSettingNumber(coordinator, entry, "pixel_len", 1, 90), TrimlightMusicDelay(coordinator, entry)])


class TrimlightSettingNumber(TrimlightEntity, NumberEntity):
    """Effect speed or effect length (dots). Changing it re-sends the effect showing now, if that effect uses it."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry, setting: str, minimum: int, maximum: int) -> None:
        super().__init__(coordinator, entry, f"effect_{setting}")
        self._setting = setting
        self._attr_translation_key = f"effect_{setting}"
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> int:
        return getattr(self.coordinator.effect_settings, self._setting)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_effect_settings(**{self._setting: int(value)})


class TrimlightMusicDelay(TrimlightEntity, NumberEntity):
    """How far the lights lag the speaker when following music; more delay for speakers that buffer longer."""

    _attr_translation_key = "music_delay"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 50
    _attr_native_min_value = DELAY_RANGE_MS[0]
    _attr_native_max_value = DELAY_RANGE_MS[1]
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry) -> None:
        super().__init__(coordinator, entry, "music_delay")

    @property
    def native_value(self) -> int:
        return self.coordinator.music.settings.delay_ms

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.music.async_set(delay_ms=int(value))
