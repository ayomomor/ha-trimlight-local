from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TrimlightConfigEntry
from .coordinator import TrimlightCoordinator
from .entity import TrimlightDailyScheduleEntity, TrimlightEntity
from .trimlight import DAILY_SCHEDULE_COUNT


async def async_setup_entry(hass: HomeAssistant, entry: TrimlightConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            TrimlightReverseSwitch(coordinator, entry),
            TrimlightFollowMusicSwitch(coordinator, entry),
            *(TrimlightDailyScheduleSwitch(coordinator, entry, i) for i in range(DAILY_SCHEDULE_COUNT)),
        ]
    )


class TrimlightReverseSwitch(TrimlightEntity, SwitchEntity):
    """Runs built-in effects in the opposite direction."""

    _attr_translation_key = "effect_reverse"

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry) -> None:
        super().__init__(coordinator, entry, "effect_reverse")

    @property
    def is_on(self) -> bool:
        return self.coordinator.effect_settings.reverse

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_effect_settings(reverse=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_effect_settings(reverse=False)


class TrimlightFollowMusicSwitch(TrimlightEntity, SwitchEntity):
    """While on, songs Home Assistant plays on the chosen speaker drive a music effect; when they stop, the lights go back."""

    _attr_translation_key = "follow_music"

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry) -> None:
        super().__init__(coordinator, entry, "follow_music")

    @property
    def is_on(self) -> bool:
        return self.coordinator.music.settings.on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        music = self.coordinator.music
        return {"speaker": music.settings.speaker, "status": music.status, "detail": music.detail}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.music.async_set(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.music.async_set(on=False)


class TrimlightDailyScheduleSwitch(TrimlightDailyScheduleEntity, SwitchEntity):
    """Whether a daily schedule is in use; daily schedules run when Mode is Timer."""

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry, index: int) -> None:
        super().__init__(coordinator, entry, index, "enabled")

    @property
    def is_on(self) -> bool | None:
        return None if (schedule := self._schedule) is None else schedule.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_change(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_change(enabled=False)
