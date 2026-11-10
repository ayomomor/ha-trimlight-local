from __future__ import annotations

from datetime import date, datetime, timedelta, time

import voluptuous as vol
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.light import ATTR_EFFECT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TrimlightConfigEntry
from .const import (
    ATTR_END_DATE,
    ATTR_HOLIDAY,
    ATTR_HOLIDAY_END_OFFSET,
    ATTR_HOLIDAY_START_OFFSET,
    ATTR_WAITING_ID,
    ATTR_OFF_OFFSET,
    ATTR_OFF_SUN,
    ATTR_OFF_TIME,
    ATTR_ON_OFFSET,
    ATTR_ON_SUN,
    ATTR_ON_TIME,
    ATTR_SCHEDULE_ID,
    ATTR_START_DATE,
    SERVICE_DELETE_SCHEDULE,
    SERVICE_SET_SCHEDULE,
)
from .coordinator import TrimlightCoordinator, next_daily_day
from .holiday_rules import HOLIDAY_OFFSET, HOLIDAYS, HolidayRule, holiday_name
from .entity import TrimlightEntity
from .sun import SUN_EVENTS, SUN_OFFSET, time_setting, window_times
from .trimlight import Repeat

_SCHEDULE_ID = vol.All(vol.Coerce(int), vol.Range(min=1, max=255))
# Enough to find the next night of a schedule that runs once a year.
_LOOKAHEAD = timedelta(days=367)


async def async_setup_entry(hass: HomeAssistant, entry: TrimlightConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([TrimlightScheduleCalendar(entry.runtime_data, entry)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_SCHEDULE,
        {
            vol.Optional(ATTR_SCHEDULE_ID): _SCHEDULE_ID,
            vol.Required(ATTR_EFFECT): cv.string,
            vol.Exclusive(ATTR_START_DATE, "dates"): cv.date,
            vol.Optional(ATTR_END_DATE): cv.date,
            vol.Exclusive(ATTR_HOLIDAY, "dates"): vol.In(list(HOLIDAYS)),
            vol.Optional(ATTR_HOLIDAY_START_OFFSET, default=0): HOLIDAY_OFFSET,
            vol.Optional(ATTR_HOLIDAY_END_OFFSET, default=0): HOLIDAY_OFFSET,
            vol.Optional(ATTR_WAITING_ID): cv.string,
            vol.Exclusive(ATTR_ON_TIME, "on"): cv.time,
            vol.Exclusive(ATTR_ON_SUN, "on"): vol.In(SUN_EVENTS),
            vol.Optional(ATTR_ON_OFFSET, default=0): SUN_OFFSET,
            vol.Exclusive(ATTR_OFF_TIME, "off"): cv.time,
            vol.Exclusive(ATTR_OFF_SUN, "off"): vol.In(SUN_EVENTS),
            vol.Optional(ATTR_OFF_OFFSET, default=0): SUN_OFFSET,
        },
        "async_set_schedule",
    )
    platform.async_register_entity_service(SERVICE_DELETE_SCHEDULE, {vol.Required(ATTR_SCHEDULE_ID): _SCHEDULE_ID}, "async_delete_schedule")


class TrimlightScheduleCalendar(TrimlightEntity, CalendarEntity):
    """The controller's calendar schedules, one event per night; they run when Mode is Timer."""

    _attr_translation_key = "schedules"

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry) -> None:
        super().__init__(coordinator, entry, "schedules")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming = (e for e in self._events(now - timedelta(days=1), now + _LOOKAHEAD) if e.end > now)
        return min(upcoming, key=lambda e: e.start, default=None)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        return sorted((e for e in self._events(start_date, end_date) if e.end > start_date and e.start < end_date), key=lambda e: e.start)

    async def async_set_schedule(
        self,
        effect: str,
        start_date: date | None = None,
        schedule_id: int | None = None,
        end_date: date | None = None,
        on_time: time | None = None,
        on_sun: str | None = None,
        on_offset: int = 0,
        off_time: time | None = None,
        off_sun: str | None = None,
        off_offset: int = 0,
        holiday: str | None = None,
        holiday_start_offset: int = 0,
        holiday_end_offset: int = 0,
        waiting_id: str | None = None,
    ) -> None:
        on = time_setting(on_time, on_sun, on_offset, "on")
        off = time_setting(off_time, off_sun, off_offset, "off")
        rule = None if holiday is None else HolidayRule(holiday, holiday_start_offset, holiday_end_offset)
        await self.coordinator.async_set_calendar_schedule(schedule_id, effect, start_date, end_date or start_date, on, off, rule, waiting_id)

    async def async_delete_schedule(self, schedule_id: int) -> None:
        await self.coordinator.async_delete_calendar_schedule(schedule_id)

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        data = self.coordinator.data
        if data is None:
            return []
        tz = dt_util.get_default_time_zone()
        # A night that starts the day before `start` can still be running at `start`.
        first = dt_util.as_local(start).date() - timedelta(days=1)
        days = [first + timedelta(days=n) for n in range((dt_util.as_local(end).date() - first).days + 1)]
        coordinator = self.coordinator
        events = []

        def add(day: date, times: tuple[time, time], summary: str, description: str, uid: str) -> None:
            on = datetime.combine(day, times[0], tz)
            off = datetime.combine(day, times[1], tz)
            if off <= on:
                off += timedelta(days=1)
            events.append(CalendarEvent(start=on, end=off, summary=summary, description=description, uid=uid))

        for schedule in data.calendar:
            summary = coordinator.saved_effect_name(schedule.effect_id) or f"Effect {schedule.effect_id}"
            rule = coordinator.holiday_rule(schedule.id)
            description = f"Trimlight schedule {schedule.id}" + (f", following {HOLIDAYS[rule.holiday].name}" if rule and rule.holiday in HOLIDAYS else "")
            for day in days:
                try:
                    covered = coordinator.calendar_covers(schedule, day)
                except ServiceValidationError:
                    break
                if covered:
                    add(day, coordinator.calendar_window(schedule, day), summary, description, f"{schedule.id}-{day.isoformat()}")
        today = dt_util.now().date()
        for key, waiting in coordinator.holidays.waiting.items():
            summary = coordinator.saved_effect_name(waiting.effect_id) or f"Effect {waiting.effect_id}"
            description = f"Trimlight {holiday_name(waiting.rule.holiday)} schedule, held by Home Assistant until it can go back on the controller"
            for day in days:
                if day >= today and coordinator.holiday_runs_on(key, waiting.rule, day) and (times := window_times(self.hass, waiting.on, waiting.off, day)):
                    add(day, times, summary, description, f"{key}-{day.isoformat()}")
        for index, daily in enumerate(data.daily):
            # A "today only" schedule's day is not reported back by the controller.
            if not daily.enabled or daily.repeat == Repeat.TODAY_ONLY:
                continue
            summary = f"{coordinator.saved_effect_name(daily.effect_id) or f'Effect {daily.effect_id}'} (daily)"
            for day in days:
                if next_daily_day(daily.repeat, day) == day:
                    add(day, coordinator.daily_window(index, daily, day), summary, f"Trimlight daily schedule {index + 1}", f"daily{index + 1}-{day.isoformat()}")
        return events
