from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TrimlightConfigEntry
from .const import COMBINED_EFFECT_NAME, REPEATS
from .coordinator import TrimlightCoordinator
from .entity import TrimlightDailyScheduleEntity, TrimlightEntity
from .trimlight import DAILY_SCHEDULE_COUNT, SwitchState

OPTIONS = {"off": SwitchState.OFF, "manual": SwitchState.MANUAL, "timer": SwitchState.TIMER}


async def async_setup_entry(hass: HomeAssistant, entry: TrimlightConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            TrimlightModeSelect(coordinator, entry),
            *(TrimlightDailyEffectSelect(coordinator, entry, i) for i in range(DAILY_SCHEDULE_COUNT)),
            *(TrimlightDailyRepeatSelect(coordinator, entry, i) for i in range(DAILY_SCHEDULE_COUNT)),
        ]
    )


class TrimlightModeSelect(TrimlightEntity, SelectEntity):
    """Off, manual (whatever was last shown), or timer (the schedules stored on the controller)."""

    _attr_translation_key = "mode"
    _attr_options = list(OPTIONS)

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry) -> None:
        super().__init__(coordinator, entry, "mode")

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return next((name for name, state in OPTIONS.items() if state == data.switch_state), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_switch_state(OPTIONS[option])


class TrimlightDailyEffectSelect(TrimlightDailyScheduleEntity, SelectEntity):
    """The saved effect (or the combined effect) a daily schedule shows."""

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry, index: int) -> None:
        super().__init__(coordinator, entry, index, "effect")

    @property
    def options(self) -> list[str]:
        return [*self.coordinator.saved_effects(), COMBINED_EFFECT_NAME]

    @property
    def current_option(self) -> str | None:
        return None if (schedule := self._schedule) is None else self.coordinator.saved_effect_name(schedule.effect_id)

    async def async_select_option(self, option: str) -> None:
        await self._async_change(effect_id=self.coordinator.schedulable_effect_id(option))


class TrimlightDailyRepeatSelect(TrimlightDailyScheduleEntity, SelectEntity):
    _attr_options = list(REPEATS)

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry, index: int) -> None:
        super().__init__(coordinator, entry, index, "repeat")

    @property
    def current_option(self) -> str | None:
        return None if (schedule := self._schedule) is None else next((key for key, value in REPEATS.items() if value == schedule.repeat), None)

    async def async_select_option(self, option: str) -> None:
        await self._async_change(repeat=REPEATS[option])
