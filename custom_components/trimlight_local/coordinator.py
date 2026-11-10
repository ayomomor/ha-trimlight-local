from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import COMBINED_EFFECT_NAME, CONF_DEVICE_CODE, DEFAULT_PIXEL_LEN, DEFAULT_SPEED, DOMAIN, ROOFLINES, SCAN_INTERVAL
from .control import BY_APP, BY_SCHEDULE, BY_YOU, DEFAULT_HOLD_MINUTES, HOLD_CHOICES, Hold, Window, lit_window, next_change
from .effects import BUILTIN_EFFECTS
from .music import FollowSettings, MusicFollower
from .holiday_rules import HOLIDAYS, HolidayDates, HolidayRule, HolidayRules, WaitingSchedule, holiday_name, is_us_holiday
from .sun import SunRule, SunRules, SunTime, TimeSetting, now, rule_for, window_times
from .trimlight import (
    CATEGORY_BUILTIN,
    CATEGORY_CUSTOM,
    CATEGORY_MUSIC,
    COMBINED_EFFECT_ID,
    MAX_CALENDAR_SCHEDULES,
    MAX_LIBRARY_EFFECTS,
    MAX_PORT_DOT,
    NAME_PATTERN,
    OVERLAY_MCU_VERSION,
    SOLID_COLOR_MODE,
    CalendarSchedule,
    CombinedEffect,
    CurrentEffect,
    DailySchedule,
    LibraryEffect,
    Overlay,
    OverlayKind,
    Repeat,
    SwitchState,
    SyncState,
    ControllerBusy,
    MusicSource,
    TrimlightClient,
    TrimlightError,
    discover,
    encode_segments,
    next_free_id,
)

_LOGGER = logging.getLogger(__name__)

# The controller never confirms a change to its memory, so each one is read back: this many answers, this far apart.
WRITE_CHECKS = 2
# The controller now and then drops a command without a trace; a change still missing after the checks is sent again.
WRITE_ATTEMPTS = 3
WRITE_CHECK_DELAY = 1.0
# Measured: after a calendar-schedule change the controller answers nothing for about 11-16 s, after a daily-schedule change about 8 s.
WRITE_READ_BACK_SECONDS = 45.0
WRITE_RETRY_DELAY = 2.0
# A tried LED setup is undone after this long unless kept, so a setting that garbles the lights fixes itself.
LED_TEST_SECONDS = 60
LED_TEST_COLOURS = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255), "white": (255, 255, 255)}
LED_TEST_PATTERN = encode_segments([(10, (255, 0, 0)), (10, (0, 255, 0)), (10, (0, 0, 255)), (10, (255, 255, 255))])
# A leap year, so that Feb 29 schedules have a day too.
_SCHEDULE_YEAR = 2016
# The daily schedule update is tried again this much later when another app had the controller.
BUSY_REFRESH_RETRY = timedelta(minutes=10)
# Patterns tried on the lights are taken off after this long without a change, in case the page was closed while trying.
TRIAL_SECONDS = 600
HOLD_RETRY = timedelta(minutes=1)
# While the controller can't be reached, it is looked for at a new address at most this often.
FIND_MOVED_EVERY = timedelta(minutes=5)


@dataclass(frozen=True)
class LedSetup:
    """The installer settings: LED chip type, colour order and the four ports' dot ranges."""

    ic_type: int | None
    channel_order: int | None
    ports: tuple[tuple[int, int, bool], ...]


@dataclass
class LedSession:
    """A spell of testing LED settings: what to put back afterwards, and when it ends by itself."""

    switch_state: int
    effect: CurrentEffect | None
    setup: LedSetup
    ends_at: datetime
    hold: Hold | None = None
    cancel: CALLBACK_TYPE | None = None


@dataclass
class Trial:
    """Patterns tried on the lights from the page, or music followed from a speaker: what to put back when it ends."""

    switch_state: int
    effect: CurrentEffect | None
    hold: Hold | None
    kind: str = "try"
    cancel: CALLBACK_TYPE | None = None


@dataclass(frozen=True)
class EffectSettings:
    """Speed, length and direction used for the next effect; they follow whatever the controller shows."""

    speed: int = DEFAULT_SPEED
    pixel_len: int = DEFAULT_PIXEL_LEN
    reverse: bool = False


class TrimlightCoordinator(DataUpdateCoordinator[SyncState]):
    """Polls the controller and sends every command, keeping `data` as the best knowledge of what is showing.

    The controller never acknowledges a command, so after one the state is assumed until the next poll.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TrimlightClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"Trimlight {client.host}", update_interval=SCAN_INTERVAL)
        self.client = client
        client.on_busy_change = self.async_update_listeners
        self._refresh_retry: CALLBACK_TYPE | None = None
        self.effect_settings = EffectSettings()
        self._write_lock = asyncio.Lock()
        self.sun = SunRules.for_entry(hass, entry.entry_id)
        self.holidays = HolidayRules.for_entry(hass, entry.entry_id)
        self.holiday_dates = HolidayDates()
        self._page_store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.page")
        self.favourites: list[int] = []
        self.roofline = ROOFLINES[0]
        self.led_session: LedSession | None = None
        self.trial: Trial | None = None
        self.hold: Hold | None = None
        self.hold_minutes: int | None = DEFAULT_HOLD_MINUTES
        self._hold_timer: CALLBACK_TYPE | None = None
        self._last_find: datetime | None = None
        self._learnt_device_code = False
        self.music = MusicFollower(self)

    async def async_load_stored(self) -> None:
        await self.sun.async_load()
        await self.holidays.async_load()
        await self.holiday_dates.async_load(self.hass, now().date())
        page = await self._page_store.async_load() or {}
        self.favourites = list(page.get("favourites", []))
        if page.get("roofline") in ROOFLINES:
            self.roofline = page["roofline"]
        if page.get("hold_minutes", DEFAULT_HOLD_MINUTES) in HOLD_CHOICES:
            self.hold_minutes = page.get("hold_minutes", DEFAULT_HOLD_MINUTES)
        self.hold = Hold.from_dict(page.get("hold"))
        self.music.settings = FollowSettings.from_dict(page.get("music"))

    async def async_set_favourites(self, favourites: list[int]) -> None:
        """Built-in effects starred on the Trimlight page; kept in Home Assistant so every device sees the same ones."""
        self.favourites = list(dict.fromkeys(favourites))
        await self._save_page()

    async def async_set_roofline(self, roofline: str) -> None:
        """The house drawing the Trimlight page previews the lights on."""
        self.roofline = roofline
        await self._save_page()

    async def _save_page(self) -> None:
        await self._page_store.async_save(self._page_data())

    def _page_data(self) -> dict:
        return {
            "favourites": self.favourites,
            "roofline": self.roofline,
            "hold_minutes": self.hold_minutes,
            "hold": None if self.hold is None else self.hold.as_dict(),
            "music": {**dataclasses.asdict(self.music.settings), "color": list(self.music.settings.color)},
        }

    def save_settings(self) -> None:
        self._page_store.async_delay_save(self._page_data, 1)

    def ffmpeg_binary(self) -> str:
        try:
            from homeassistant.components.ffmpeg import get_ffmpeg_manager  # noqa: PLC0415

            return get_ffmpeg_manager(self.hass).binary
        except (ImportError, KeyError):
            return "ffmpeg"

    async def _async_update_data(self) -> SyncState:
        if self.data is not None and (self._write_lock.locked() or self.music.following):
            # A change is being read back, and the controller may be silent for a while after one.
            return self.data
        try:
            state = await self._async_poll()
        except ControllerBusy as err:
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            return self.data
        except TrimlightError as err:
            raise UpdateFailed(str(err)) from err
        self._follow_effect_settings(state.current_effect)
        await self._forget_removed_schedules(state)
        self._check_hold(state)
        if not self._learnt_device_code:
            self._learnt_device_code = True
            if CONF_DEVICE_CODE not in self.config_entry.data:
                self.config_entry.async_create_background_task(self.hass, self._async_learn_device_code(), "trimlight_local device code")
        return state

    async def _async_poll(self) -> SyncState:
        try:
            return await self.client.sync(wait=0, linger=0)
        except ControllerBusy:
            raise
        except TrimlightError:
            if not await self._async_find_moved():
                raise
            return await self.client.sync(wait=0, linger=0)

    async def _async_find_moved(self) -> bool:
        """Look for the controller at a new address by its device code; True when it was found and is used from now on."""
        code = self.config_entry.data.get(CONF_DEVICE_CODE)
        utcnow = dt_util.utcnow()
        if not code or (self._last_find is not None and utcnow - self._last_find < FIND_MOVED_EVERY):
            return False
        self._last_find = utcnow
        try:
            found = await discover(timeout=2.0)
        except OSError as err:
            _LOGGER.debug("Discovery failed: %r", err)
            return False
        moved = next((d for d in found if d.device_code == code and d.host != self.client.host), None)
        if moved is None:
            return False
        old = self.client.host
        await self.client.close()
        self.client.host = moved.host
        self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, CONF_HOST: moved.host})
        _LOGGER.warning("The Trimlight controller moved from %s to %s", old, moved.host)
        persistent_notification.async_create(
            self.hass,
            f"The Trimlight controller {self.config_entry.title} stopped answering at {old} and was found at {moved.host}. "
            "Home Assistant uses the new address from now on. A DHCP reservation in your router keeps the address from changing.",
            title="Trimlight controller moved",
            notification_id=f"{DOMAIN}_moved_{self.config_entry.entry_id}",
        )
        return True

    async def _async_learn_device_code(self) -> None:
        """Note the controller's device code, which stays the same when its address changes."""
        try:
            found = await discover(timeout=2.0)
        except OSError as err:
            _LOGGER.debug("Discovery failed: %r", err)
            return
        if match := next((d for d in found if d.host == self.client.host and d.device_code), None):
            self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, CONF_DEVICE_CODE: match.device_code})

    async def _forget_removed_schedules(self, state: SyncState) -> None:
        # A schedule deleted in the phone app takes its sun setting with it. A short reply that lacks the calendar says nothing either way.
        if not state.daily:
            return
        ids = {s.id for s in state.calendar}
        for rules in (self.sun, self.holidays):
            if gone := [i for i in rules.calendar if i not in ids]:
                for schedule_id in gone:
                    del rules.calendar[schedule_id]
                await rules.async_save()

    @property
    def busy(self) -> bool:
        """Another app, normally the phone app, had the controller at the last attempt to reach it."""
        return self.client.busy

    def schedule_windows(self, state: SyncState, first: date, days: int) -> list[Window]:
        """The nights the controller's schedules light, starting on `first`, for `days` days."""
        zone = dt_util.get_default_time_zone()
        windows = []
        for day in (first + timedelta(days=n) for n in range(days)):
            nights = [("calendar", s.id, s.effect_id, self.calendar_window(s, day)) for s in state.calendar if self.calendar_covers(s, day)]
            nights += [("daily", i, d.effect_id, self.daily_window(i, d, day)) for i, d in enumerate(state.daily) if d.enabled and daily_runs_on(d.repeat, day)]
            for kind, key, effect_id, (on, off) in nights:
                end_day = day + timedelta(days=1) if off <= on else day
                windows.append(Window(datetime.combine(day, on, zone), datetime.combine(end_day, off, zone), effect_id, kind, key))
        return windows

    def _windows_around(self, state: SyncState, moment: datetime) -> list[Window]:
        return self.schedule_windows(state, moment.date() - timedelta(days=1), 3)

    @property
    def lit_by_schedule(self) -> Window | None:
        """The schedule lighting the lights now, in timer mode."""
        data = self.data
        if data is None or data.switch_state != SwitchState.TIMER:
            return None
        moment = now()
        return lit_window(self._windows_around(data, moment), moment)

    @property
    def lights_lit(self) -> bool | None:
        """Whether the lights are lit: in manual mode, or in timer mode while a schedule runs."""
        data = self.data
        if data is None:
            return None
        if data.switch_state == SwitchState.TIMER:
            return self.lit_by_schedule is not None
        return data.switch_state == SwitchState.MANUAL

    @property
    def controlled_by(self) -> str | None:
        data = self.data
        if data is None:
            return None
        if data.switch_state == SwitchState.TIMER:
            return BY_SCHEDULE
        if self.trial is not None:
            return "music" if self.trial.kind == "music" else BY_YOU
        return self.hold.by if self.hold is not None else BY_APP

    @property
    def back_to_schedule_at(self) -> datetime | None:
        data = self.data
        if data is None or data.switch_state == SwitchState.TIMER or self.hold is None:
            return None
        return self.hold.until

    def control_summary(self) -> dict:
        """Who runs the lights and until when, for the Trimlight page."""
        lit = self.lit_by_schedule
        until = self.back_to_schedule_at
        return {
            "lit": self.lights_lit,
            "by": self.controlled_by,
            "until": None if until is None else until.isoformat(),
            "window": None if lit is None else {"effect": self.saved_effect_name(lit.effect_id), "effect_id": lit.effect_id, "end": lit.end.isoformat()},
            "hold_minutes": self.hold_minutes,
            "hold_choices": list(HOLD_CHOICES),
            "trying": self.trial is not None and self.trial.kind == "try",
            "music": self.music.summary(),
        }

    def _suspended(self) -> bool:
        """An LED test or a pattern being tried on the lights puts things back itself, so it starts no hold."""
        return self.trial is not None or self.led_session is not None

    def _new_hold(self, by: str, state: SyncState) -> Hold:
        """A change lasts `hold_minutes`, or until the schedules next switch the lights on or off if that is sooner."""
        if self.hold_minutes is None:
            return Hold(by, None)
        moment = now()
        until = moment + timedelta(minutes=self.hold_minutes)
        change = next_change(self._windows_around(state, moment), moment)
        return Hold(by, until if change is None else min(until, change))

    def _set_hold(self, hold: Hold | None) -> None:
        if self._hold_timer is not None:
            self._hold_timer()
            self._hold_timer = None
        changed = hold != self.hold
        self.hold = hold
        if hold is not None and hold.until is not None:
            self._hold_timer = async_track_point_in_utc_time(self.hass, HassJob(self._hold_ended, cancel_on_shutdown=True), dt_util.as_utc(hold.until))
        if changed:
            self._page_store.async_delay_save(self._page_data, 1)

    def _note_change(self, switch_state: int) -> None:
        if self.trial is not None and self.trial.kind == "music":
            self.trial = None
            self.music.user_took_over()
        if self._suspended():
            if self.trial is not None:
                self._restart_trial_timer()
            return
        if switch_state == SwitchState.TIMER:
            self._set_hold(None)
        elif self.data is not None:
            self._set_hold(self._new_hold(BY_YOU, self.data))

    def _check_hold(self, state: SyncState) -> None:
        """Follow mode changes made elsewhere: leaving timer mode starts a hold, and an ended hold returns to it."""
        if self._suspended():
            return
        if state.switch_state == SwitchState.TIMER:
            if self.hold is not None:
                self._set_hold(None)
        elif self.hold is None:
            self._set_hold(self._new_hold(BY_APP, state))
        elif self.hold.until is not None and self._hold_timer is None:
            if self.hold.until <= now():
                self._hold_ended(dt_util.utcnow())
            else:
                self._set_hold(self.hold)

    @callback
    def _hold_ended(self, _: datetime) -> None:
        self._hold_timer = None
        self.config_entry.async_create_background_task(self.hass, self._async_end_hold(), "trimlight_local back to schedule")

    async def _async_end_hold(self) -> None:
        if not self._suspended():
            try:
                await self.async_resume_schedule()
                return
            except HomeAssistantError as err:
                _LOGGER.info("Could not put the lights back on their schedules yet: %s", err)
        if self._hold_timer is None:
            self._hold_timer = async_call_later(self.hass, HOLD_RETRY, HassJob(self._hold_ended, cancel_on_shutdown=True))

    async def async_resume_schedule(self) -> None:
        """Hand the lights back to the schedules (timer mode)."""
        await self.async_set_switch_state(SwitchState.TIMER)

    async def async_keep_change(self) -> None:
        """Make the change showing now last until something else is changed."""
        data = self._require_data()
        if data.switch_state != SwitchState.TIMER:
            self._set_hold(Hold(self.hold.by if self.hold is not None else BY_APP, None))
            self.async_update_listeners()

    async def async_set_hold_minutes(self, minutes: int | None) -> None:
        if minutes not in HOLD_CHOICES:
            raise ServiceValidationError(f"A change can last {', '.join(str(m) for m in HOLD_CHOICES if m)} minutes, or until it is changed")
        self.hold_minutes = minutes
        await self._save_page()
        self.async_update_listeners()

    async def async_try(self, on: bool) -> None:
        """Start or end trying patterns on the lights; at the end, what showed before is put back."""
        if not on:
            await self._async_end_trial()
            return
        if self.trial is None:
            data = self._require_data()
            self.trial = Trial(data.switch_state, data.current_effect, self.hold)
        elif self.trial.kind == "music":
            self.trial.kind = "try"
            self.music.user_took_over()
        self._restart_trial_timer()
        self.async_update_listeners()

    def _restart_trial_timer(self) -> None:
        if (trial := self.trial) is None or trial.kind != "try":
            return
        if trial.cancel:
            trial.cancel()
        trial.cancel = async_call_later(self.hass, TRIAL_SECONDS, HassJob(self._trial_expired, cancel_on_shutdown=True))

    async def async_begin_music(self, settings: FollowSettings) -> None:
        """Put the lights on the music effect for streamed bands, remembering what they showed so the end of the music puts it back."""
        data = self._require_data()
        if self.trial is None:
            self.trial = Trial(data.switch_state, data.current_effect, self.hold, kind="music")
        elif self.trial.kind != "music":
            if self.trial.cancel:
                self.trial.cancel()
            self.trial.cancel = None
            self.trial.kind = "music"
        if data.switch_state != SwitchState.MANUAL:
            await _run(self.client.set_switch_state(SwitchState.MANUAL))
        await _run(self.client.set_music(settings.effect, settings.color, settings.length, settings.sensitivity, MusicSource.PHONE_PLAYER))
        self.async_set_updated_data(dataclasses.replace(data, switch_state=SwitchState.MANUAL, current_effect=CurrentEffect(CATEGORY_MUSIC, settings.effect, 0, 0, 0, False, b"")))

    async def async_end_music(self, *, restore: bool) -> None:
        if self.trial is None or self.trial.kind != "music":
            return
        if restore:
            await self._async_end_trial()
        else:
            self.trial = None

    @callback
    def _trial_expired(self, _: datetime) -> None:
        if self.trial is not None:
            self.trial.cancel = None
            self.config_entry.async_create_background_task(self.hass, self._async_end_trial(), "trimlight_local end trying")

    async def _async_end_trial(self) -> None:
        if (trial := self.trial) is None:
            return
        if trial.cancel:
            trial.cancel()
        self.trial = None
        try:
            data = self.data
            if trial.switch_state == SwitchState.MANUAL:
                if data is None or (data.switch_state, data.current_effect) != (SwitchState.MANUAL, trial.effect):
                    await self._async_restore_effect(trial.effect)
            elif data is None or data.switch_state != trial.switch_state:
                await self.async_set_switch_state(SwitchState(trial.switch_state))
        finally:
            self._set_hold(None if trial.switch_state == SwitchState.TIMER else trial.hold)
            self.async_update_listeners()

    def _follow_effect_settings(self, current: CurrentEffect | None) -> None:
        if current is None:
            return
        if current.category == CATEGORY_BUILTIN and current.mode < len(BUILTIN_EFFECTS):
            self.effect_settings = EffectSettings(current.speed, current.pixel_len or self.effect_settings.pixel_len, current.reverse)
        elif current.category == CATEGORY_CUSTOM:
            self.effect_settings = dataclasses.replace(self.effect_settings, speed=current.speed)

    @property
    def showing_effect(self) -> CurrentEffect | None:
        """The effect shown in manual mode, or None when off, on a timer, or showing a solid colour."""
        data = self.data
        if data is None or data.switch_state != SwitchState.MANUAL or (current := data.current_effect) is None:
            return None
        if current.category == CATEGORY_CUSTOM or (current.category == CATEGORY_BUILTIN and current.mode < len(BUILTIN_EFFECTS)):
            return current
        return None

    def saved_effects(self) -> dict[str, LibraryEffect]:
        """Saved effects by the name shown in Home Assistant; a name that is empty, repeated or also a built-in effect gets its id added."""
        saved: dict[str, LibraryEffect] = {}
        for effect in self.data.library if self.data is not None else ():
            name = effect.name or f"Saved {effect.id}"
            if name in BUILTIN_EFFECTS or name in saved or name == COMBINED_EFFECT_NAME:
                name = f"{name} (saved {effect.id})"
            saved[name] = effect
        return saved

    def saved_effect_name(self, effect_id: int) -> str | None:
        if effect_id == COMBINED_EFFECT_ID:
            return COMBINED_EFFECT_NAME
        return next((name for name, e in self.saved_effects().items() if e.id == effect_id), None)

    def schedulable_effect_id(self, name: str) -> int:
        """The effect id for a saved effect's name or the combined effect; schedules can only use these."""
        if name == COMBINED_EFFECT_NAME:
            return COMBINED_EFFECT_ID
        return self.saved_effect_by_name(name).id

    def saved_effect_by_name(self, name: str) -> LibraryEffect:
        if (effect := self.saved_effects().get(name)) is None:
            raise ServiceValidationError(f"No saved effect is called {name!r}")
        return effect

    async def async_set_switch_state(self, state: SwitchState) -> None:
        await _run(self.client.set_switch_state(state))
        self._assume(switch_state=state)

    async def async_turn_on(self) -> None:
        await self._ensure_manual()
        self._assume(switch_state=SwitchState.MANUAL)

    async def async_show_builtin(self, mode: int, brightness: int, *, speed: int | None = None, pixel_len: int | None = None, reverse: bool | None = None) -> None:
        """Show a built-in effect; settings not given come from `effect_settings`."""
        settings = self.effect_settings
        speed = settings.speed if speed is None else speed
        pixel_len = settings.pixel_len if pixel_len is None else pixel_len
        reverse = settings.reverse if reverse is None else reverse
        await self._ensure_manual()
        await _run(self.client.preview_builtin(mode, brightness=brightness, speed=speed, pixel_len=pixel_len, reverse=reverse))
        self._assume(CurrentEffect(CATEGORY_BUILTIN, mode, speed, brightness, pixel_len, reverse, b""))

    async def async_show_pattern(self, mode: int, speed: int, brightness: int, segments: bytes) -> None:
        await self._ensure_manual()
        await _run(self.client.preview_custom(mode, speed, brightness, segments))
        self._assume(CurrentEffect(CATEGORY_CUSTOM, mode, speed, brightness, 0, False, segments))

    async def async_show_saved(self, effect_id: int) -> None:
        await self._ensure_manual()
        await _run(self.client.show_library_effect(effect_id))
        saved = self.data.effect(effect_id) if self.data is not None else None
        self._assume(None if saved is None else CurrentEffect(saved.category, saved.mode, saved.speed, saved.brightness, saved.pixel_len, saved.reverse, saved.segments))

    async def async_start_music(self, effect: int, color: tuple[int, int, int], length: int, sensitivity: int) -> None:
        await self._ensure_manual()
        await _run(self.client.set_music(effect, color, length, sensitivity))
        current = self.data.current_effect if self.data is not None else None
        self._assume(None if current is None else dataclasses.replace(current, category=CATEGORY_MUSIC, mode=effect, segments=b""))

    async def async_show_color(self, red: int, green: int, blue: int) -> None:
        await self._ensure_manual()
        await _run(self.client.preview_color(red, green, blue))
        current = self.data.current_effect if self.data is not None else None
        # The controller records a solid colour as built-in SOLID_COLOR_MODE and keeps the other fields of the previous effect.
        self._assume(None if current is None else dataclasses.replace(current, category=CATEGORY_BUILTIN, mode=SOLID_COLOR_MODE, segments=b""))

    async def async_set_effect_settings(self, **changes: int | bool) -> None:
        """Change speed, length or direction, and apply it to the effect showing now if it uses that setting."""
        self.effect_settings = dataclasses.replace(self.effect_settings, **changes)
        if (current := self.showing_effect) is not None:
            if current.category == CATEGORY_BUILTIN:
                await self.async_show_builtin(current.mode, current.brightness)
            elif "speed" in changes:
                await self.async_show_pattern(current.mode, self.effect_settings.speed, current.brightness, current.segments)
        self.async_update_listeners()

    async def async_save_effect(self, name: str, effect: LibraryEffect) -> None:
        """Save `effect` in the controller's library as `name`, replacing a saved effect of that name."""
        data = self._require_data()
        existing = next((e for e in data.library if e.name == name), None)
        if existing is None and len(data.library) >= MAX_LIBRARY_EFFECTS:
            raise ServiceValidationError(f"The controller holds at most {MAX_LIBRARY_EFFECTS} saved effects")
        effect_id = existing.id if existing is not None else next_free_id((e.id for e in data.library), skip=COMBINED_EFFECT_ID)
        saved = dataclasses.replace(effect, id=effect_id, name=name)
        send = (lambda: self.client.modify_library_effect(saved)) if existing is not None else (lambda: self.client.add_library_effect(saved))
        await self._async_write(send, lambda s: (e := s.effect(effect_id)) is not None and e.encode() == saved.encode(), f"save {name!r}")

    async def async_rename_saved_effect(self, name: str, new_name: str) -> None:
        effect = self.saved_effect_by_name(name)
        if any(e.name == new_name and e.id != effect.id for e in self._require_data().library):
            raise ServiceValidationError(f"Another saved effect is already called {new_name!r}")
        renamed = dataclasses.replace(effect, name=new_name)
        await self._async_write(lambda: self.client.modify_library_effect(renamed), lambda s: (e := s.effect(effect.id)) is not None and e.name == renamed.name, f"rename {name!r}")

    def effect_users(self, effect_id: int) -> list[str]:
        """What uses a saved effect, which stops it being deleted."""
        data = self._require_data()
        users = [f"calendar schedule {s.id}" for s in data.calendar if s.effect_id == effect_id]
        users += [f"daily schedule {i + 1}" for i, s in enumerate(data.daily) if s.effect_id == effect_id]
        if data.combined is not None and effect_id in data.combined.effect_ids:
            users.append("the combined effect")
        users += [f"the waiting {holiday_name(w.rule.holiday)} schedule" for w in self.holidays.waiting.values() if w.effect_id == effect_id]
        return users

    async def async_delete_saved_effect(self, name: str) -> None:
        data = self._require_data()
        effect = self.saved_effect_by_name(name)
        if users := self.effect_users(effect.id):
            raise ServiceValidationError(f"{name!r} is used by {', '.join(users)}; change those first")
        if effect.category == CATEGORY_CUSTOM and sum(e.category == CATEGORY_CUSTOM for e in data.library) <= 1:
            raise ServiceValidationError("The controller must keep at least one saved pattern")
        await self._async_write(lambda: self.client.remove_library_effect(effect.id), lambda s: s.effect(effect.id) is None, f"delete {name!r}")

    async def async_set_overlay(self, name: str, kind: OverlayKind | None) -> None:
        """Put lightning or snow over a saved effect, or remove it (kind None)."""
        data = self._require_data()
        effect = self.saved_effect_by_name(name)
        if kind == OverlayKind.SNOW and data.mcu_version is not None and data.mcu_version & 0xFFF < OVERLAY_MCU_VERSION:
            raise ServiceValidationError("This controller's firmware only supports lightning")
        overlays = [o for o in data.overlays if o.effect_id != effect.id]
        if kind is not None:
            overlays.append(Overlay(kind, effect.id))
        await self._async_write(lambda: self.client.set_overlays(overlays), lambda s: set(s.overlays) == set(overlays), f"change the overlay on {name!r}")

    async def async_set_combined_effect(self, names: list[str], interval: int) -> None:
        combined = CombinedEffect(tuple(self.saved_effect_by_name(name).id for name in names), interval)
        await self._async_write(lambda: self.client.set_combined_effect(combined), lambda s: s.combined == combined, "change the combined effect")

    async def async_set_calendar_schedule(
        self,
        schedule_id: int | None,
        effect: str,
        start: date | None,
        end: date | None,
        on: TimeSetting,
        off: TimeSetting,
        holiday: HolidayRule | None = None,
        replaces: str | None = None,
    ) -> int | None:
        """Add a schedule (schedule_id None) or change one; returns its id.

        Times may follow sunrise or sunset. With `holiday`, the dates are the nights around the holiday's next occurrence
        that doesn't overlap another schedule, and they move on each year. When those dates can't be written yet without
        lighting the schedule early, Home Assistant holds the schedule instead, and None is returned. `replaces` is a
        waiting schedule this one takes the place of.
        """
        replaced = None
        if replaces is not None and (replaced := self.holidays.waiting.pop(replaces, None)) is None:
            raise ServiceValidationError(f"There is no waiting schedule {replaces}")
        try:
            result = await self._async_set_calendar_schedule(schedule_id, effect, start, end, on, off, holiday)
        except Exception:
            if replaced is not None:
                self.holidays.waiting[replaces] = replaced
            raise
        if replaced is not None:
            await self.holidays.async_save()
            self.async_update_listeners()
        return result

    async def _async_set_calendar_schedule(
        self, schedule_id: int | None, effect: str, start: date | None, end: date | None, on: TimeSetting, off: TimeSetting, holiday: HolidayRule | None
    ) -> int | None:
        data = self._require_data()
        others = [s for s in data.calendar if s.id != schedule_id]
        if schedule_id is not None and len(others) == len(data.calendar):
            raise ServiceValidationError(f"There is no schedule {schedule_id}")
        if schedule_id is None and len(data.calendar) >= MAX_CALENDAR_SCHEDULES:
            raise ServiceValidationError(f"The controller holds at most {MAX_CALENDAR_SCHEDULES} schedules")
        on, off = _whole_minutes(on), _whole_minutes(off)
        if holiday is not None:
            today = now().date()
            ident = "new" if schedule_id is None else f"c{schedule_id}"
            start, end = self._holiday_target(holiday, today, ident)
            displaced = self._displaced(ident, (start, end))
            if writable_from((start, end), today) > today or displaced is None:
                return await self._async_hold_new(schedule_id, effect, on, off, holiday)
            for other in displaced:
                await self._async_hold(other)
            if displaced:
                await self.holidays.async_save()
                data = self._require_data()
                others = [s for s in data.calendar if s.id != schedule_id]
        if start is None or end is None:
            raise ServiceValidationError("Give the schedule's dates, or a holiday")
        days = schedule_days((start.month, start.day), (end.month, end.day))
        if clash := next((s for s in others if days & schedule_days(s.start, s.end)), None):
            raise ServiceValidationError(f"Schedule {clash.id} already covers some of those dates; the controller allows one schedule per date")
        times = window_times(self.hass, on, off, next_schedule_day(days, now().date()))
        if times is None:
            raise ServiceValidationError("The sun does not rise or set at Home Assistant's location on those dates")
        if (start.month, start.day) == (end.month, end.day) and times[0] == times[1]:
            raise ServiceValidationError("A one-day schedule needs different on and off times")
        new = schedule_id is None
        schedule = CalendarSchedule(
            next_free_id(s.id for s in data.calendar) if new else schedule_id,
            self.schedulable_effect_id(effect),
            (start.month, start.day),
            (end.month, end.day),
            *times,
        )
        await self._async_write_calendar(schedule, new)
        _set_rule(self.sun.calendar, schedule.id, rule_for(on, off, times))
        await self.sun.async_save()
        if holiday is not None:
            holiday.written = (schedule.start, schedule.end)
            self.holidays.calendar[schedule.id] = holiday
            await self.holidays.async_save()
        elif self.holidays.calendar.pop(schedule.id, None) is not None:
            await self.holidays.async_save()
        return schedule.id

    def _holiday_target(self, rule: HolidayRule, today: date, ident: str) -> tuple[date, date]:
        if rule.holiday not in HOLIDAYS or not self.holiday_dates.available(rule.holiday):
            raise ServiceValidationError(f"Home Assistant can't work out the dates of {holiday_name(rule.holiday)}")
        if rule.start_offset > rule.end_offset:
            raise ServiceValidationError("The schedule can't end before it starts")
        window, skipped = self.holiday_plan(ident, rule, today)
        if window is None:
            raise ServiceValidationError(
                f"Every coming {holiday_name(rule.holiday)} overlaps another schedule" if skipped else f"Home Assistant doesn't know when {holiday_name(rule.holiday)} is next"
            )
        return window

    async def _async_hold_new(self, schedule_id: int | None, effect: str, on: TimeSetting, off: TimeSetting, rule: HolidayRule) -> None:
        effect_id = self.schedulable_effect_id(effect)
        if schedule_id is not None:
            await self._async_take_off(schedule_id)
        self.holidays.waiting[self.holidays.new_waiting_id()] = WaitingSchedule(rule, effect_id, on, off)
        await self.holidays.async_save()
        self.async_update_listeners()

    async def _async_hold(self, schedule: CalendarSchedule) -> None:
        """Take a holiday schedule off the controller and keep it waiting in Home Assistant."""
        rule = self.holidays.calendar[schedule.id]
        on, off = self._time_settings(schedule)
        await self._async_take_off(schedule.id)
        rule.written = None
        self.holidays.waiting[self.holidays.new_waiting_id()] = WaitingSchedule(rule, schedule.effect_id, on, off)

    async def _async_take_off(self, schedule_id: int) -> None:
        await self._async_write(lambda: self.client.delete_calendar_schedule(schedule_id), lambda s: all(c.id != schedule_id for c in s.calendar), f"take schedule {schedule_id} off the controller")
        self.holidays.calendar.pop(schedule_id, None)
        if self.sun.calendar.pop(schedule_id, None) is not None:
            await self.sun.async_save()

    def _contenders(self) -> list[tuple[str, CalendarSchedule | None, HolidayRule | None]]:
        """Every schedule that can claim nights: those on the controller ("c" and the id), with any holiday rule, and the waiting ones."""
        data = self._require_data()
        on_controller = [(f"c{s.id}", s, self.holidays.calendar.get(s.id)) for s in data.calendar]
        return on_controller + [(key, None, w.rule) for key, w in self.holidays.waiting.items()]

    @staticmethod
    def _rank(ident: str, rule: HolidayRule | None, window: tuple[date, date] | None) -> tuple[int, date, str]:
        """Who gets nights that two schedules want, lowest first: fixed dates, then US holidays, then other holidays; within those, the earlier start."""
        if rule is None or window is None:
            return (0, date.min, ident)
        return (1 if is_us_holiday(rule.holiday) else 2, window[0], ident)

    def _blocker(self, ident: str, rule: HolidayRule, window: tuple[date, date]) -> tuple[str, CalendarSchedule | None, HolidayRule | None] | None:
        """The schedule that gets some of these nights instead of the holiday schedule `ident`, if one does."""
        mine = self._rank(ident, rule, window)
        days = schedule_days(*month_days(window))
        for other, schedule, other_rule in self._contenders():
            if other == ident:
                continue
            if other_rule is None:
                if schedule is not None and days & schedule_days(schedule.start, schedule.end):
                    return other, schedule, None
                continue
            for year in (window[0].year - 1, window[0].year, window[0].year + 1):
                theirs = self.holiday_dates.window(other_rule, year)
                if theirs is None or theirs[1] < window[0] or theirs[0] > window[1]:
                    continue
                # Ranks fall strictly at each step, so this ends.
                if self._rank(other, other_rule, theirs) < mine and self._blocker(other, other_rule, theirs) is None:
                    return other, schedule, other_rule
        return None

    def _label(self, contender: tuple[str, CalendarSchedule | None, HolidayRule | None]) -> str:
        _, schedule, rule = contender
        if schedule is not None:
            effect = self.saved_effect_name(schedule.effect_id)
            return f"schedule {schedule.id}" + (f" ({effect})" if effect else "")
        return f"the waiting {holiday_name(rule.holiday) if rule else ''} schedule"

    def _displaced(self, ident: str, window: tuple[date, date]) -> list[CalendarSchedule] | None:
        """Holiday schedules whose dates on the controller overlap these nights and must make way; None if a fixed-date one does."""
        days = schedule_days(*month_days(window))
        displaced = []
        for schedule in self._require_data().calendar:
            if f"c{schedule.id}" == ident or not days & schedule_days(schedule.start, schedule.end):
                continue
            if schedule.id not in self.holidays.calendar:
                return None
            displaced.append(schedule)
        return displaced

    def holiday_plan(self, ident: str, rule: HolidayRule, today: date) -> tuple[tuple[date, date] | None, list[tuple[int, str]]]:
        """The next nights the holiday schedule `ident` gets, and the years it gives way, with what to."""
        skipped: list[tuple[int, str]] = []
        for window in self.holiday_dates.windows(rule, today):
            if (blocker := self._blocker(ident, rule, window)) is None:
                return window, skipped
            skipped.append((window[0].year, self._label(blocker)))
        return None, skipped

    def holiday_rule(self, schedule_id: int) -> HolidayRule | None:
        return self.holidays.calendar.get(schedule_id)

    def holiday_runs_on(self, ident: str, rule: HolidayRule, day: date) -> bool | None:
        """Whether a holiday schedule lights on `day`, leaving out years it gives way; None if that year's date isn't known."""
        known = False
        for year in (day.year - 1, day.year, day.year + 1):
            window = self.holiday_dates.window(rule, year)
            if window is None:
                continue
            known = known or year == day.year
            if window[0] <= day <= window[1]:
                return self._blocker(ident, rule, window) is None
        return False if known else None

    def calendar_covers(self, schedule: CalendarSchedule, day: date) -> bool:
        """Whether a calendar schedule runs on `day`, following its holiday from year to year where it has one."""
        rule = self.holidays.calendar.get(schedule.id)
        if rule is not None and (runs := self.holiday_runs_on(f"c{schedule.id}", rule, day)) is not None:
            return runs
        return schedule_day(day) in schedule_days(schedule.start, schedule.end)

    async def async_delete_waiting_schedule(self, waiting_id: str) -> None:
        if self.holidays.waiting.pop(waiting_id, None) is None:
            raise ServiceValidationError(f"There is no waiting schedule {waiting_id}")
        await self.holidays.async_save()
        self.async_update_listeners()

    async def async_refresh_schedules(self, _: datetime | None = None) -> None:
        """The daily update: holiday schedules move on to their coming dates, then sun times follow the sun."""
        if not self.client.busy:
            await self.holiday_dates.async_load(self.hass, now().date())
            await self.async_refresh_holiday_dates()
            await self.async_refresh_sun_times()
        if self.client.busy and self._refresh_retry is None:
            self._refresh_retry = async_call_later(self.hass, BUSY_REFRESH_RETRY, self._async_retry_refresh)

    async def _async_retry_refresh(self, _: datetime) -> None:
        self._refresh_retry = None
        await self.async_refresh_schedules()

    @callback
    def async_cancel_timers(self) -> None:
        if self._refresh_retry is not None:
            self._refresh_retry()
            self._refresh_retry = None
        if self._hold_timer is not None:
            self._hold_timer()
            self._hold_timer = None
        if self.trial is not None and self.trial.cancel is not None:
            self.trial.cancel()
            self.trial.cancel = None
        self.music.async_shutdown()

    async def async_refresh_holiday_dates(self) -> None:
        """Put each holiday schedule on the controller for the next nights it gets, once the last ones have passed.

        Fixed-date schedules keep their nights; US holidays win nights over other holidays, and otherwise the earlier one
        does. A schedule that gives way skips that year. Dates are only written once they can't come round early, since
        the controller repeats them every year; until then a schedule keeps its old dates if those can't come round
        either, and otherwise waits off the controller.
        """
        if self.data is None or not self.data.daily:
            return
        today = now().date()
        changed = False
        for schedule in self.data.calendar:
            rule = self.holidays.calendar.get(schedule.id)
            if rule is not None and rule.written is not None and rule.written != (schedule.start, schedule.end):
                _LOGGER.info("Schedule %d's dates were changed outside Home Assistant, so they no longer follow %s", schedule.id, holiday_name(rule.holiday))
                del self.holidays.calendar[schedule.id]
                changed = True
        # A schedule made to give way is waiting by the end of a pass, and may be placed in the next one.
        for _ in range(3):
            if not await self._async_place_holidays(today):
                break
            changed = True
        if changed:
            await self.holidays.async_save()
            await self.sun.async_save()

    async def _async_place_holidays(self, today: date) -> bool:
        plans = []
        for ident, _, rule in self._contenders():
            if rule is None:
                continue
            window, _ = self.holiday_plan(ident, rule, today)
            rule.problem = None if window is not None else f"Home Assistant can't find a coming {holiday_name(rule.holiday)} for it."
            if window is not None:
                plans.append((self._rank(ident, rule, window), ident, window))
        changed = False
        for _, ident, window in sorted(plans):
            data = self._require_data()
            schedule = next((s for s in data.calendar if f"c{s.id}" == ident), None)
            rule = self.holidays.calendar.get(schedule.id) if schedule is not None else self.holidays.waiting[ident].rule if ident in self.holidays.waiting else None
            if rule is None:
                continue
            try:
                if schedule is not None:
                    changed |= await self._async_place(schedule, rule, window, today)
                else:
                    changed |= await self._async_release(ident, self.holidays.waiting[ident], window, today)
            except HomeAssistantError as err:
                rule.problem = f"Couldn't move it on to {holiday_name(rule.holiday)} {window[0].year}: {err}"
                _LOGGER.warning("Could not move the %s schedule to %d: %s", holiday_name(rule.holiday), window[0].year, err)
        return changed

    async def _async_place(self, schedule: CalendarSchedule, rule: HolidayRule, window: tuple[date, date], today: date) -> bool:
        dates = month_days(window)
        if dates == (schedule.start, schedule.end):
            return False
        opens = writable_from(window, today)
        if opens > today:
            if _dark(schedule.start, schedule.end, today, opens):
                return False
            await self._async_hold(schedule)
            return True
        if (displaced := self._displaced(f"c{schedule.id}", window)) is None:
            return False
        for other in displaced:
            await self._async_hold(other)
        await self._async_write_calendar(dataclasses.replace(schedule, start=dates[0], end=dates[1]), new=False)
        rule.written = dates
        return True

    async def _async_release(self, key: str, waiting: WaitingSchedule, window: tuple[date, date], today: date) -> bool:
        """Put a waiting schedule back on the controller once its next nights can be written; whether it was."""
        rule = waiting.rule
        if writable_from(window, today) > today or (displaced := self._displaced(key, window)) is None:
            return False
        times = window_times(self.hass, waiting.on, waiting.off, window[0])
        if self.saved_effect_name(waiting.effect_id) is None:
            rule.problem = "Its saved effect has been deleted, so it can't go back on the controller."
        elif len(self._require_data().calendar) - len(displaced) >= MAX_CALENDAR_SCHEDULES:
            rule.problem = f"The controller already holds {MAX_CALENDAR_SCHEDULES} schedules, so it can't go back on."
        elif times is None:
            rule.problem = "The sun does not rise or set at Home Assistant's location on its dates."
        if rule.problem is not None:
            return False
        for other in displaced:
            await self._async_hold(other)
        dates = month_days(window)
        schedule = CalendarSchedule(next_free_id(s.id for s in self._require_data().calendar), waiting.effect_id, dates[0], dates[1], *times)
        await self._async_write_calendar(schedule, new=True)
        rule.written = dates
        self.holidays.calendar[schedule.id] = rule
        del self.holidays.waiting[key]
        _set_rule(self.sun.calendar, schedule.id, rule_for(waiting.on, waiting.off, times))
        return True

    def _time_settings(self, schedule: CalendarSchedule) -> tuple[TimeSetting, TimeSetting]:
        rule = self.sun.calendar.get(schedule.id)
        return (rule.on if rule and rule.on else schedule.on_time), (rule.off if rule and rule.off else schedule.off_time)

    async def _async_write_calendar(self, schedule: CalendarSchedule, new: bool) -> None:
        await self._async_write(lambda: self.client.set_calendar_schedule(schedule, new=new), lambda s: schedule in s.calendar, f"save schedule {schedule.id}")

    async def async_delete_calendar_schedule(self, schedule_id: int) -> None:
        if all(s.id != schedule_id for s in self._require_data().calendar):
            raise ServiceValidationError(f"There is no schedule {schedule_id}")
        await self._async_write(lambda: self.client.delete_calendar_schedule(schedule_id), lambda s: all(c.id != schedule_id for c in s.calendar), f"delete schedule {schedule_id}")
        if self.sun.calendar.pop(schedule_id, None) is not None:
            await self.sun.async_save()
        if self.holidays.calendar.pop(schedule_id, None) is not None:
            await self.holidays.async_save()

    async def async_set_daily_schedule(self, index: int, *, on_time: TimeSetting | None = None, off_time: TimeSetting | None = None, **changes: object) -> None:
        """Change a daily schedule. A time left out keeps following the sun if it did; a clock time given stops that."""
        data = self._require_data()
        if index >= len(data.daily):
            raise HomeAssistantError("The controller did not report its daily schedules")
        current = data.daily[index]
        rule = self.sun.daily.get(index)
        on = _whole_minutes(on_time if on_time is not None else (rule.on if rule and rule.on else current.on_time))
        off = _whole_minutes(off_time if off_time is not None else (rule.off if rule and rule.off else current.off_time))
        schedule = dataclasses.replace(current, **changes)
        times = window_times(self.hass, on, off, next_daily_day(schedule.repeat, now().date()))
        if times is None:
            raise ServiceValidationError("The sun does not rise or set at Home Assistant's location today")
        schedule = dataclasses.replace(schedule, on_time=times[0], off_time=times[1])
        if schedule.on_time == schedule.off_time:
            raise ServiceValidationError("The on and off times must differ")
        other = data.daily[1 - index]
        if schedule.enabled and other.enabled and _same_days(schedule.repeat, other.repeat) and _windows_overlap(schedule, other):
            raise ServiceValidationError("The two daily schedules cannot overlap in time")
        await self._async_write_daily(index, schedule)
        _set_rule(self.sun.daily, index, rule_for(on, off, times))
        await self.sun.async_save()

    async def _async_write_daily(self, index: int, schedule: DailySchedule) -> None:
        await self._async_write(
            lambda: self.client.set_daily_schedule(index, schedule, now().date()),
            lambda s: len(s.daily) > index and s.daily[index] == schedule,
            f"change daily schedule {index + 1}",
        )

    def sun_setting(self, kind: str, key: int, which: str) -> SunTime | None:
        """The sun time behind a calendar or daily schedule's "on" or "off" time, if it follows the sun."""
        rule = (self.sun.calendar if kind == "calendar" else self.sun.daily).get(key)
        return None if rule is None else getattr(rule, which)

    def calendar_window(self, schedule: CalendarSchedule, day: date) -> tuple[time, time]:
        """The times a calendar schedule uses on `day`, following the sun where it does."""
        return self._window(self.sun.calendar.get(schedule.id), schedule.on_time, schedule.off_time, day)

    def daily_window(self, index: int, schedule: DailySchedule, day: date) -> tuple[time, time]:
        return self._window(self.sun.daily.get(index), schedule.on_time, schedule.off_time, day)

    def _window(self, rule: SunRule | None, on: time, off: time, day: date) -> tuple[time, time]:
        if rule is None:
            return on, off
        return window_times(self.hass, rule.on or on, rule.off or off, day) or (on, off)

    async def async_refresh_sun_times(self, _: datetime | None = None) -> None:
        """Rewrite the clock times of sun-following schedules that run today, as sunrise and sunset move."""
        if self.data is None:
            return
        today = now().date()
        changed = False
        for schedule in self.data.calendar:
            if (rule := self.sun.calendar.get(schedule.id)) is None:
                continue
            if rule.written is not None and rule.written != (schedule.on_time, schedule.off_time):
                _LOGGER.info("Schedule %d's times were changed outside Home Assistant, so they no longer follow the sun", schedule.id)
                del self.sun.calendar[schedule.id]
                changed = True
                continue
            if schedule_day(today) not in schedule_days(schedule.start, schedule.end):
                continue
            times = self.calendar_window(schedule, today)
            if times == (schedule.on_time, schedule.off_time):
                continue
            try:
                await self._async_write_calendar(dataclasses.replace(schedule, on_time=times[0], off_time=times[1]), new=False)
            except HomeAssistantError as err:
                _LOGGER.warning("Could not move schedule %d to today's sun times: %s", schedule.id, err)
                continue
            rule.written = times
            changed = True
        for index, schedule in enumerate(self.data.daily):
            if (rule := self.sun.daily.get(index)) is None:
                continue
            if rule.written is not None and rule.written != (schedule.on_time, schedule.off_time):
                _LOGGER.info("Daily schedule %d's times were changed outside Home Assistant, so they no longer follow the sun", index + 1)
                del self.sun.daily[index]
                changed = True
                continue
            # Rewriting a "today only" schedule would move it to today, because the command carries today's date.
            if not schedule.enabled or schedule.repeat == Repeat.TODAY_ONLY or next_daily_day(schedule.repeat, today) != today:
                continue
            times = self.daily_window(index, schedule, today)
            if times == (schedule.on_time, schedule.off_time):
                continue
            try:
                await self._async_write_daily(index, dataclasses.replace(schedule, on_time=times[0], off_time=times[1]))
            except HomeAssistantError as err:
                _LOGGER.warning("Could not move daily schedule %d to today's sun times: %s", index + 1, err)
                continue
            rule.written = times
            changed = True
        if changed:
            await self.sun.async_save()

    def led_setup(self) -> LedSetup:
        data = self._require_data()
        mask = data.port_mask
        return LedSetup(data.ic_type, data.channel_order, tuple((start, end, True if mask is None else bool(mask >> i & 1)) for i, (start, end) in enumerate(data.ports)))

    async def async_rename_controller(self, name: str) -> None:
        name = name.strip()
        if not re.fullmatch(NAME_PATTERN, name):
            raise ServiceValidationError("A controller name is 1 to 25 letters, digits, spaces, - or _")
        await _run(self.client.rename(name))
        self.hass.config_entries.async_update_entry(self.config_entry, title=name)

    async def async_led_test(self, show: str) -> None:
        """Show a test colour, or a red-green-blue-white pattern, during an LED test session."""
        await self._async_begin_led_session()
        if show == "pattern":
            await self.async_show_pattern(0, 0, 255, LED_TEST_PATTERN)
        else:
            await self.async_show_color(*LED_TEST_COLOURS[show])
        self._restart_led_timer()

    async def async_try_led_setup(self, *, ic_type: int | None = None, channel_order: int | None = None, ports: list[tuple[int, int, bool]] | None = None) -> None:
        """Change LED settings for a trial; they are undone unless kept before the session ends."""
        for number, (start, end, _) in enumerate(ports or (), 1):
            if not 1 <= start <= end <= MAX_PORT_DOT:
                raise ServiceValidationError(f"Port {number}: dots run from 1 to {MAX_PORT_DOT}, and the last dot can't come before the first")
        await self._async_begin_led_session()
        current = self.led_setup()
        changes = {"ic_type": ic_type, "channel_order": channel_order, "ports": None if ports is None else tuple(tuple(p) for p in ports)}
        await self._async_write_led_setup(current, dataclasses.replace(current, **{k: v for k, v in changes.items() if v is not None}))
        self._restart_led_timer()

    async def async_end_led_session(self, keep: bool) -> None:
        """Keep or undo the tried LED settings, then put back the effect and mode from before the test."""
        if (session := self.led_session) is None:
            return
        if session.cancel:
            session.cancel()
        self.led_session = None
        try:
            if not keep:
                await self._async_write_led_setup(self.led_setup(), session.setup)
            await self._async_restore_effect(session.effect)
            if session.switch_state != SwitchState.MANUAL:
                await self.async_set_switch_state(SwitchState(session.switch_state))
        finally:
            self._set_hold(None if session.switch_state == SwitchState.TIMER else session.hold)
            self.async_update_listeners()

    async def _async_begin_led_session(self) -> None:
        if self.led_session is not None:
            return
        data = self._require_data()
        self.led_session = LedSession(data.switch_state, data.current_effect, self.led_setup(), dt_util.utcnow() + timedelta(seconds=LED_TEST_SECONDS), self.hold)
        await self._ensure_manual()

    def _restart_led_timer(self) -> None:
        session = self.led_session
        if session is None:
            return
        if session.cancel:
            session.cancel()
        session.ends_at = dt_util.utcnow() + timedelta(seconds=LED_TEST_SECONDS)
        session.cancel = async_call_later(self.hass, LED_TEST_SECONDS, HassJob(self._led_session_expired, cancel_on_shutdown=True))
        self.async_update_listeners()

    @callback
    def _led_session_expired(self, _: datetime) -> None:
        if self.led_session is not None:
            self.led_session.cancel = None
            self.config_entry.async_create_background_task(self.hass, self.async_end_led_session(keep=False), "trimlight_local undo LED test")

    async def _async_write_led_setup(self, old: LedSetup, new: LedSetup) -> None:
        if new.ic_type is not None and new.ic_type != old.ic_type:
            await self._async_write(lambda: self.client.set_ic_type(new.ic_type), lambda s: s.ic_type == new.ic_type, "change the LED chip type")
        if new.channel_order is not None and new.channel_order != old.channel_order:
            await self._async_write(lambda: self.client.set_channel_order(new.channel_order), lambda s: s.channel_order == new.channel_order, "change the colour order")
        for index, (port, before) in enumerate(zip(new.ports, old.ports)):
            if port != before:
                start, end, enabled = port
                await self._async_write(
                    lambda: self.client.set_port(index, start, end, enabled),
                    lambda s, i=index, p=port: len(s.ports) > i and s.ports[i] == p[:2] and (s.port_mask is None or bool(s.port_mask >> i & 1) == p[2]),
                    f"change port {index + 1}",
                )

    async def _async_restore_effect(self, effect: CurrentEffect | None) -> None:
        if effect is None:
            return
        if effect.category == CATEGORY_CUSTOM:
            await self.async_show_pattern(effect.mode, effect.speed, effect.brightness, effect.segments)
        elif effect.category == CATEGORY_BUILTIN and effect.mode < len(BUILTIN_EFFECTS):
            await self.async_show_builtin(effect.mode, effect.brightness, speed=effect.speed, pixel_len=effect.pixel_len or None, reverse=effect.reverse)
        elif effect.category == CATEGORY_MUSIC and self.data is not None and self.data.music is not None:
            music = self.data.music
            await self.async_start_music(effect.mode, music.color, music.length, music.sensitivity or 13)

    async def _async_write(self, send: Callable[[], Awaitable[None]], applied: Callable[[SyncState], bool], what: str) -> None:
        """Change the controller's memory and read the change back, sending it again if the controller dropped it."""
        async with self._write_lock:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + WRITE_READ_BACK_SECONDS
            for attempt in range(1, WRITE_ATTEMPTS + 1):
                await _run(send())
                answers = 0
                while answers < WRITE_CHECKS:
                    try:
                        state = await self.client.sync(fresh=True)
                    except TrimlightError as err:
                        if loop.time() > deadline:
                            raise HomeAssistantError(f"Sent the change, but the controller has not answered since: {err}") from err
                        await asyncio.sleep(WRITE_RETRY_DELAY)
                        continue
                    self._follow_effect_settings(state.current_effect)
                    self.async_set_updated_data(state)
                    if applied(state):
                        return
                    answers += 1
                    if answers < WRITE_CHECKS:
                        await asyncio.sleep(WRITE_CHECK_DELAY)
                if loop.time() > deadline:
                    break
                _LOGGER.debug("The controller did not %s on attempt %d; sending it again", what, attempt)
            raise HomeAssistantError(f"The controller did not {what}; it answered, but without the change")

    def _require_data(self) -> SyncState:
        if self.data is None:
            raise HomeAssistantError("The controller's state is not known yet")
        return self.data

    async def _ensure_manual(self) -> None:
        # The app refuses to send effects unless the controller is in manual mode, so do the same.
        if self.data is None or self.data.switch_state != SwitchState.MANUAL:
            await _run(self.client.set_switch_state(SwitchState.MANUAL))

    def _assume(self, current_effect: CurrentEffect | None = None, *, switch_state: SwitchState = SwitchState.MANUAL) -> None:
        if (data := self.data) is None:
            return
        self._follow_effect_settings(current_effect)
        self._note_change(switch_state)
        # `raw` is kept, which is how entities tell this assumed state from a real poll.
        self.async_set_updated_data(dataclasses.replace(data, switch_state=switch_state, current_effect=current_effect or data.current_effect))


def schedule_day(day: date) -> int:
    """`day` as a day of the schedule year, the numbering schedule_days uses."""
    return date(_SCHEDULE_YEAR, day.month, day.day).timetuple().tm_yday - 1


def next_schedule_day(days: set[int], today: date) -> date:
    """The next date, from today on, that a calendar schedule covering `days` runs."""
    return next((d for d in (today + timedelta(days=n) for n in range(367)) if schedule_day(d) in days), today)


def next_daily_day(repeat: int, today: date) -> date:
    """The next date, from today on, that a daily schedule with this repeat setting runs."""
    return next((day for day in (today + timedelta(days=n) for n in range(7)) if daily_runs_on(repeat, day)), today)


def daily_runs_on(repeat: int, day: date) -> bool:
    # The controller doesn't report which day a "today only" schedule was set for, so it counts as every day.
    weekend = day.weekday() >= 5
    return repeat in (Repeat.TODAY_ONLY, Repeat.EVERY_DAY) or (repeat == Repeat.WEEKDAYS and not weekend) or (repeat == Repeat.WEEKEND and weekend)


def month_days(window: tuple[date, date]) -> tuple[tuple[int, int], tuple[int, int]]:
    return (window[0].month, window[0].day), (window[1].month, window[1].day)


def writable_from(window: tuple[date, date], today: date) -> date:
    """The first day, from today, on which writing the window's month and day can't light the schedule before its first night.

    The controller repeats a schedule's month and day every year, so they can only be written once they won't come round before then.
    """
    days = schedule_days(*month_days(window))
    opens = today
    for n in range((window[0] - today).days):
        if schedule_day(today + timedelta(days=n)) in days:
            opens = today + timedelta(days=n + 1)
    return opens


def _dark(start: tuple[int, int], end: tuple[int, int], today: date, until: date) -> bool:
    """Whether a schedule on these month-day dates stays dark from today until `until`."""
    days = schedule_days(start, end)
    return not any(schedule_day(today + timedelta(days=n)) in days for n in range((until - today).days))


def _whole_minutes(value: TimeSetting) -> TimeSetting:
    return value.replace(second=0, microsecond=0) if isinstance(value, time) else value


def _set_rule(rules: dict[int, SunRule], key: int, rule: SunRule | None) -> None:
    if rule is None:
        rules.pop(key, None)
    else:
        rules[key] = rule


def schedule_days(start: tuple[int, int], end: tuple[int, int]) -> set[int]:
    """Days of the year (0-365) a calendar schedule covers; an end before the start wraps past New Year."""
    try:
        first = date(_SCHEDULE_YEAR, *start).timetuple().tm_yday - 1
        last = date(_SCHEDULE_YEAR, *end).timetuple().tm_yday - 1
    except ValueError as err:
        raise ServiceValidationError(f"Not a calendar date: {err}") from err
    return set(range(first, last + 1)) if first <= last else set(range(first, 366)) | set(range(last + 1))


def _same_days(a: int, b: int) -> bool:
    return {a, b} != {Repeat.WEEKDAYS, Repeat.WEEKEND}


def _windows_overlap(a: DailySchedule, b: DailySchedule) -> bool:
    def window(s: DailySchedule) -> tuple[int, int]:
        on, off = s.on_time.hour * 60 + s.on_time.minute, s.off_time.hour * 60 + s.off_time.minute
        return on, off + 1440 if off <= on else off

    (a_on, a_off), (b_on, b_off) = window(a), window(b)
    # A window that runs past midnight also meets the other schedule's window on the following day.
    return any(a_on < b_off + shift and b_on + shift < a_off for shift in (-1440, 0, 1440))


async def _run(operation: Awaitable[None]) -> None:
    try:
        await operation
    except TrimlightError as err:
        raise HomeAssistantError(str(err)) from err
