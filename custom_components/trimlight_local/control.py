"""Who runs the lights: the controller's schedules (timer mode), or a change someone made, which lasts a while like a thermostat hold."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.util import dt as dt_util

# Minutes a change lasts before the schedules take over again; None lasts until something else is changed.
HOLD_CHOICES: tuple[int | None, ...] = (30, 60, 120, 240, None)
DEFAULT_HOLD_MINUTES = 60
BY_SCHEDULE = "schedule"
BY_YOU = "you"
BY_APP = "app"


@dataclass(frozen=True)
class Window:
    """One night of a schedule, from when it switches the lights on to when it switches them off."""

    start: datetime
    end: datetime
    effect_id: int
    kind: str
    key: int


@dataclass(frozen=True)
class Hold:
    """A change made outside the schedules: by Home Assistant ("you") or anything else ("app"). It ends at `until`, or never when None."""

    by: str
    until: datetime | None

    def as_dict(self) -> dict[str, str | None]:
        return {"by": self.by, "until": None if self.until is None else self.until.isoformat()}

    @classmethod
    def from_dict(cls, value: object) -> Hold | None:
        if not isinstance(value, dict) or value.get("by") not in (BY_YOU, BY_APP):
            return None
        until = value.get("until")
        return cls(value["by"], dt_util.parse_datetime(until) if isinstance(until, str) else None)


def lit_window(windows: list[Window], now: datetime) -> Window | None:
    """The window lighting the schedules now; a calendar schedule comes before a daily one."""
    active = [w for w in windows if w.start <= now < w.end]
    return min(active, key=lambda w: (w.kind != "calendar", w.start), default=None)


def next_change(windows: list[Window], now: datetime) -> datetime | None:
    """When the schedules next switch the lights on or off."""
    return min((t for w in windows for t in (w.start, w.end) if t > now), default=None)


def next_start(windows: list[Window], now: datetime) -> Window | None:
    return min((w for w in windows if w.start > now), key=lambda w: (w.start, w.kind != "calendar"), default=None)
