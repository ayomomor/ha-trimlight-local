"""Calendar schedules whose dates follow a holiday that moves from year to year, such as Easter or Thanksgiving.

The controller stores a month and day that repeat every year, so Home Assistant writes the dates of the holiday's
coming occurrence and moves them on once it has passed.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .sun import SunTime, TimeSetting

_LOGGER = logging.getLogger(__name__)

MAX_OFFSET_DAYS = 60
HOLIDAY_OFFSET = vol.All(vol.Coerce(int), vol.Range(min=-MAX_OFFSET_DAYS, max=MAX_OFFSET_DAYS))
MONDAY, THURSDAY, SUNDAY = 0, 3, 6
LOOKUP_YEARS_AHEAD = 12


def easter(year: int) -> date:
    """Western Easter Sunday, by the Meeus/Jones/Butcher Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday) // 451
    month, day = divmod(h + weekday - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Monday is 0) of a month; n = -1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


@dataclass(frozen=True)
class Holiday:
    """A holiday worked out by a rule, or looked up in the holidays library by country and English name."""

    name: str
    group: str
    rule: Callable[[int], date] | None = None
    lookup: tuple[str, str] | None = None


def orthodox_easter(year: int) -> date:
    """Orthodox Easter Sunday: the Julian computus (Meeus), moved to the Gregorian calendar, which is 13 days ahead from 1900 to 2099."""
    d = (19 * (year % 19) + 15) % 30
    e = (2 * (year % 4) + 4 * (year % 7) - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


def _fixed(month: int, day: int) -> Callable[[int], date]:
    return lambda year: date(year, month, day)


def _from_easter(days: int) -> Callable[[int], date]:
    return lambda year: easter(year) + timedelta(days=days)


US = "US holidays"
CHRISTIAN = "Christian"
JEWISH = "Jewish"
ISLAMIC = "Islamic (estimated dates)"
SOUTH_ASIAN = "Hindu, Sikh and Buddhist"
EAST_ASIAN = "East Asian"
OTHER = "Other days"

HOLIDAYS: dict[str, Holiday] = {
    "new_years_day": Holiday("New Year's Day", US, _fixed(1, 1)),
    "mlk_day": Holiday("Martin Luther King Jr. Day", US, lambda y: nth_weekday(y, 1, MONDAY, 3)),
    "valentines_day": Holiday("Valentine's Day", US, _fixed(2, 14)),
    "presidents_day": Holiday("Presidents' Day", US, lambda y: nth_weekday(y, 2, MONDAY, 3)),
    "mardi_gras": Holiday("Mardi Gras", US, _from_easter(-47)),
    "st_patricks_day": Holiday("St. Patrick's Day", US, _fixed(3, 17)),
    "good_friday": Holiday("Good Friday", US, _from_easter(-2)),
    "easter": Holiday("Easter", US, easter),
    "mothers_day": Holiday("Mother's Day", US, lambda y: nth_weekday(y, 5, SUNDAY, 2)),
    "memorial_day": Holiday("Memorial Day", US, lambda y: nth_weekday(y, 5, MONDAY, -1)),
    "fathers_day": Holiday("Father's Day", US, lambda y: nth_weekday(y, 6, SUNDAY, 3)),
    "juneteenth": Holiday("Juneteenth", US, _fixed(6, 19)),
    "independence_day": Holiday("Independence Day", US, _fixed(7, 4)),
    "labor_day": Holiday("Labor Day", US, lambda y: nth_weekday(y, 9, MONDAY, 1)),
    "grandparents_day": Holiday("Grandparents Day", US, lambda y: nth_weekday(y, 9, MONDAY, 1) + timedelta(days=6)),
    "columbus_day": Holiday("Columbus Day / Indigenous Peoples' Day", US, lambda y: nth_weekday(y, 10, MONDAY, 2)),
    "halloween": Holiday("Halloween", US, _fixed(10, 31)),
    "election_day": Holiday("Election Day", US, lambda y: nth_weekday(y, 11, MONDAY, 1) + timedelta(days=1)),
    "veterans_day": Holiday("Veterans Day", US, _fixed(11, 11)),
    "thanksgiving": Holiday("Thanksgiving", US, lambda y: nth_weekday(y, 11, THURSDAY, 4)),
    "christmas_eve": Holiday("Christmas Eve", US, _fixed(12, 24)),
    "christmas": Holiday("Christmas Day", US, _fixed(12, 25)),
    "new_years_eve": Holiday("New Year's Eve", US, _fixed(12, 31)),
    "ash_wednesday": Holiday("Ash Wednesday", CHRISTIAN, _from_easter(-46)),
    "palm_sunday": Holiday("Palm Sunday", CHRISTIAN, _from_easter(-7)),
    "pentecost": Holiday("Pentecost", CHRISTIAN, _from_easter(49)),
    "orthodox_christmas": Holiday("Orthodox Christmas", CHRISTIAN, _fixed(1, 7)),
    "orthodox_easter": Holiday("Orthodox Easter", CHRISTIAN, orthodox_easter),
    "purim": Holiday("Purim", JEWISH, lookup=("IL", "Purim")),
    "passover": Holiday("Passover", JEWISH, lookup=("IL", "Pesach")),
    "shavuot": Holiday("Shavuot", JEWISH, lookup=("IL", "Shavuot")),
    "rosh_hashanah": Holiday("Rosh Hashanah", JEWISH, lookup=("IL", "Rosh Hashanah")),
    "yom_kippur": Holiday("Yom Kippur", JEWISH, lookup=("IL", "Yom Kippur")),
    "sukkot": Holiday("Sukkot", JEWISH, lookup=("IL", "Sukkot")),
    "hanukkah": Holiday("Hanukkah", JEWISH, lookup=("IL", "Hanukkah")),
    "eid_al_fitr": Holiday("Eid al-Fitr", ISLAMIC, lookup=("AE", "Eid al-Fitr")),
    "eid_al_adha": Holiday("Eid al-Adha", ISLAMIC, lookup=("AE", "Eid al-Adha")),
    "islamic_new_year": Holiday("Islamic New Year", ISLAMIC, lookup=("AE", "Islamic New Year")),
    "mawlid": Holiday("Prophet's Birthday (Mawlid)", ISLAMIC, lookup=("AE", "Prophet's Birthday")),
    "holi": Holiday("Holi", SOUTH_ASIAN, lookup=("IN", "Holi")),
    "buddha_purnima": Holiday("Vesak / Buddha Purnima", SOUTH_ASIAN, lookup=("IN", "Buddha Purnima")),
    "raksha_bandhan": Holiday("Raksha Bandhan", SOUTH_ASIAN, lookup=("IN", "Raksha Bandhan")),
    "janmashtami": Holiday("Janmashtami", SOUTH_ASIAN, lookup=("IN", "Janmashtami (Vaishnava)")),
    "ganesh_chaturthi": Holiday("Ganesh Chaturthi", SOUTH_ASIAN, lookup=("IN", "Ganesh Chaturthi / Vinayak Chaturthi")),
    "dussehra": Holiday("Dussehra", SOUTH_ASIAN, lookup=("IN", "Dussehra")),
    "diwali": Holiday("Diwali", SOUTH_ASIAN, lookup=("IN", "Diwali")),
    "guru_nanak_jayanti": Holiday("Guru Nanak Jayanti", SOUTH_ASIAN, lookup=("IN", "Guru Nanak's Birthday")),
    "lunar_new_year": Holiday("Lunar New Year", EAST_ASIAN, lookup=("CN", "Chinese New Year (Spring Festival)")),
    "dragon_boat": Holiday("Dragon Boat Festival", EAST_ASIAN, lookup=("CN", "Dragon Boat Festival")),
    "mid_autumn": Holiday("Mid-Autumn Festival", EAST_ASIAN, lookup=("CN", "Mid-Autumn Festival")),
    "nowruz": Holiday("Nowruz", OTHER, lookup=("IR", "Nowruz")),
    "cinco_de_mayo": Holiday("Cinco de Mayo", OTHER, _fixed(5, 5)),
    "canada_day": Holiday("Canada Day", OTHER, _fixed(7, 1)),
    "nigeria_independence_day": Holiday("Nigerian Independence Day", OTHER, _fixed(10, 1)),
    "day_of_the_dead": Holiday("Day of the Dead", OTHER, _fixed(11, 2)),
    "kwanzaa": Holiday("Kwanzaa", OTHER, _fixed(12, 26)),
}


def holiday_name(key: str) -> str:
    return HOLIDAYS[key].name if key in HOLIDAYS else key


def is_us_holiday(key: str) -> bool:
    return key in HOLIDAYS and HOLIDAYS[key].group == US


def _library_dates(years: range) -> dict[str, dict[int, date]]:
    """First days of the looked-up holidays in `years`. Blocking: the library reads its translations from disk."""
    try:
        import holidays as library
    except ImportError:
        _LOGGER.warning("The holidays library is missing, so only holidays worked out by rule can be scheduled")
        return {}
    calendars: dict[str, Any] = {}
    found: dict[str, dict[int, date]] = {}
    for key, holiday in HOLIDAYS.items():
        if holiday.lookup is None:
            continue
        country, name = holiday.lookup
        if country not in calendars:
            with warnings.catch_warnings():
                # The library warns about years beyond its lunar tables and leaves them out; a missing date is handled.
                warnings.simplefilter("ignore")
                calendars[country] = library.country_holidays(
                    country, years=list(range(years.start - 1, years.stop)), language="en_US", categories=getattr(library, country).supported_categories
                )
        calendar = calendars[country]

        def named(day: date, calendar: Any = calendar, name: str = name) -> bool:
            return any(part.removesuffix(" (estimated)") == name for part in calendar.get(day, "").split("; "))

        # Hanukkah and other multi-day holidays count from their first day, which can fall in the previous December.
        for day in sorted(day for day in calendar if day.year in years and named(day)):
            if not named(day - timedelta(days=1)):
                found.setdefault(key, {}).setdefault(day.year, day)
    return found


@dataclass
class HolidayRule:
    """A calendar schedule's dates as days around a holiday, and the dates Home Assistant last wrote for it."""

    holiday: str
    start_offset: int = 0
    end_offset: int = 0
    written: tuple[tuple[int, int], tuple[int, int]] | None = None
    # Why the schedule could not be moved to its coming dates; kept in memory only.
    problem: str | None = field(default=None, compare=False)


class HolidayDates:
    """Every holiday's date in the years schedules can need."""

    def __init__(self) -> None:
        self._looked_up: dict[str, dict[int, date]] = {}
        self._years = range(0)

    async def async_load(self, hass: HomeAssistant, today: date) -> None:
        years = range(today.year - 1, today.year + LOOKUP_YEARS_AHEAD)
        if years != self._years:
            self._looked_up = await hass.async_add_executor_job(_library_dates, years)
            self._years = years

    def date(self, key: str, year: int) -> date | None:
        holiday = HOLIDAYS.get(key)
        if holiday is None:
            return None
        if holiday.rule is not None:
            return holiday.rule(year)
        return self._looked_up.get(key, {}).get(year)

    def available(self, key: str) -> bool:
        holiday = HOLIDAYS.get(key)
        return holiday is not None and (holiday.rule is not None or bool(self._looked_up.get(key)))

    def window(self, rule: HolidayRule, year: int) -> tuple[date, date] | None:
        """The first and last night the schedule runs around the holiday in `year`."""
        day = self.date(rule.holiday, year)
        if day is None:
            return None
        return day + timedelta(days=rule.start_offset), day + timedelta(days=rule.end_offset)

    def windows(self, rule: HolidayRule, today: date, count: int = 6) -> list[tuple[date, date]]:
        """The schedule's nights around each occurrence that hasn't ended yet, today included, soonest first."""
        found: list[tuple[date, date]] = []
        for year in range(today.year - 1, today.year + count + 1):
            window = self.window(rule, year)
            if window is not None and window[1] >= today:
                found.append(window)
                if len(found) == count:
                    break
        return found


@dataclass
class WaitingSchedule:
    """A holiday schedule held off the controller, because any dates written now would light it before its next nights."""

    rule: HolidayRule
    effect_id: int
    on: TimeSetting
    off: TimeSetting


def _dump_time(value: TimeSetting) -> dict[str, Any]:
    return value.as_dict() if isinstance(value, SunTime) else {"time": value.strftime("%H:%M")}


def _load_time(value: dict[str, Any]) -> TimeSetting:
    return time.fromisoformat(value["time"]) if "time" in value else SunTime(value["event"], int(value.get("offset", 0)))


def _dump_rule(rule: HolidayRule) -> dict[str, Any]:
    return {
        "holiday": rule.holiday,
        "start_offset": rule.start_offset,
        "end_offset": rule.end_offset,
        "written": None if rule.written is None else [list(rule.written[0]), list(rule.written[1])],
    }


def _load_rule(value: dict[str, Any]) -> HolidayRule:
    written = value.get("written")
    return HolidayRule(value["holiday"], int(value.get("start_offset", 0)), int(value.get("end_offset", 0)), None if written is None else (tuple(written[0]), tuple(written[1])))


@dataclass
class HolidayRules:
    """The holiday-following calendar schedules of one controller, kept in Home Assistant's storage."""

    store: Store
    calendar: dict[int, HolidayRule] = field(default_factory=dict)
    waiting: dict[str, WaitingSchedule] = field(default_factory=dict)

    @classmethod
    def for_entry(cls, hass: HomeAssistant, entry_id: str) -> HolidayRules:
        return cls(Store(hass, 1, f"{DOMAIN}.{entry_id}.holidays"))

    def new_waiting_id(self) -> str:
        return f"w{max((int(k[1:]) for k in self.waiting), default=0) + 1}"

    async def async_load(self) -> None:
        stored = await self.store.async_load() or {}
        self.calendar = {int(k): _load_rule(v) for k, v in stored.get("calendar", {}).items()}
        self.waiting = {
            k: WaitingSchedule(_load_rule(v), int(v["effect_id"]), _load_time(v["on"]), _load_time(v["off"])) for k, v in stored.get("waiting", {}).items()
        }

    async def async_save(self) -> None:
        await self.store.async_save(
            {
                "calendar": {str(k): _dump_rule(r) for k, r in self.calendar.items()},
                "waiting": {k: {**_dump_rule(w.rule), "effect_id": w.effect_id, "on": _dump_time(w.on), "off": _dump_time(w.off)} for k, w in self.waiting.items()},
            }
        )
