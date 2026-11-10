"""Schedule times that follow sunrise or sunset.

The controller only stores clock times, so Home Assistant works the time out from its own location, writes it, and
rewrites it on each day the schedule is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import DOMAIN

SUN_EVENTS = (SUN_EVENT_SUNSET, SUN_EVENT_SUNRISE)
SUN_OFFSET = vol.All(vol.Coerce(int), vol.Range(min=-240, max=240))


def now() -> datetime:
    return dt_util.now()


@dataclass(frozen=True)
class SunTime:
    """Sunrise or sunset, moved by `offset` minutes (negative is before)."""

    event: str
    offset: int = 0

    def on_day(self, hass: HomeAssistant, day: date) -> time | None:
        """The local clock time on `day`, to the nearest minute; None where the sun does not rise or set that day."""
        moment = get_astral_event_date(hass, self.event, day)
        if moment is None:
            return None
        return (dt_util.as_local(moment) + timedelta(minutes=self.offset, seconds=30)).time().replace(second=0, microsecond=0)

    def as_dict(self) -> dict[str, Any]:
        return {"event": self.event, "offset": self.offset}


TimeSetting = time | SunTime


@dataclass
class SunRule:
    """Which of a schedule's times follow the sun, and the clock times Home Assistant last wrote for them."""

    on: SunTime | None = None
    off: SunTime | None = None
    # Compared with the controller's times to notice a change made elsewhere, such as in the phone app.
    written: tuple[time, time] | None = None


def window_times(hass: HomeAssistant, on: TimeSetting, off: TimeSetting, day: date) -> tuple[time, time] | None:
    """The clock times of the on/off window that starts on `day`; an off time not after the on time is the next morning."""
    on_time = on if isinstance(on, time) else on.on_day(hass, day)
    if isinstance(off, time):
        off_time = off
    else:
        off_time = off.on_day(hass, day)
        if off_time is not None and on_time is not None and off_time <= on_time:
            off_time = off.on_day(hass, day + timedelta(days=1))
    if on_time is None or off_time is None:
        return None
    return on_time, off_time


def time_setting(clock: time | None, event: str | None, offset: int, label: str) -> TimeSetting:
    """An action's time field: a clock time, or sunrise/sunset with an offset in minutes."""
    if event is not None:
        return SunTime(event, offset)
    if clock is None:
        raise ServiceValidationError(f"Give an {label} time, or sunrise or sunset")
    return clock


def rule_for(on: TimeSetting, off: TimeSetting, written: tuple[time, time]) -> SunRule | None:
    rule = SunRule(on if isinstance(on, SunTime) else None, off if isinstance(off, SunTime) else None, written)
    return rule if rule.on is not None or rule.off is not None else None


@dataclass
class SunRules:
    """Sun-following schedule times for one controller, kept in Home Assistant's storage."""

    store: Store
    calendar: dict[int, SunRule] = field(default_factory=dict)
    daily: dict[int, SunRule] = field(default_factory=dict)

    @classmethod
    def for_entry(cls, hass: HomeAssistant, entry_id: str) -> SunRules:
        return cls(Store(hass, 1, f"{DOMAIN}.{entry_id}.sun"))

    async def async_load(self) -> None:
        stored = await self.store.async_load() or {}
        self.calendar = {int(k): _load_rule(v) for k, v in stored.get("calendar", {}).items()}
        self.daily = {int(k): _load_rule(v) for k, v in stored.get("daily", {}).items()}

    async def async_save(self) -> None:
        await self.store.async_save(
            {"calendar": {str(k): _dump_rule(v) for k, v in self.calendar.items()}, "daily": {str(k): _dump_rule(v) for k, v in self.daily.items()}}
        )


def _load_rule(value: dict[str, Any]) -> SunRule:
    def sun(item: dict[str, Any] | None) -> SunTime | None:
        return None if item is None else SunTime(item["event"], int(item.get("offset", 0)))

    written = value.get("written")
    return SunRule(sun(value.get("on")), sun(value.get("off")), None if written is None else (time.fromisoformat(written[0]), time.fromisoformat(written[1])))


def _dump_rule(rule: SunRule) -> dict[str, Any]:
    return {
        "on": None if rule.on is None else rule.on.as_dict(),
        "off": None if rule.off is None else rule.off.as_dict(),
        "written": None if rule.written is None else [t.strftime("%H:%M") for t in rule.written],
    }
