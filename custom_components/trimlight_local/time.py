from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TrimlightConfigEntry
from .coordinator import TrimlightCoordinator
from .entity import TrimlightDailyScheduleEntity
from .trimlight import DAILY_SCHEDULE_COUNT


async def async_setup_entry(hass: HomeAssistant, entry: TrimlightConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(TrimlightDailyTime(coordinator, entry, i, field) for i in range(DAILY_SCHEDULE_COUNT) for field in ("on_time", "off_time"))


class TrimlightDailyTime(TrimlightDailyScheduleEntity, TimeEntity):
    """When a daily schedule turns the lights on or off; an off time before the on time ends the next morning."""

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry, index: int, field: str) -> None:
        super().__init__(coordinator, entry, index, field)
        self._field = field

    @property
    def native_value(self) -> time | None:
        return None if (schedule := self._schedule) is None else getattr(schedule, self._field)

    async def async_set_value(self, value: time) -> None:
        await self._async_change(**{self._field: value.replace(second=0, microsecond=0)})
