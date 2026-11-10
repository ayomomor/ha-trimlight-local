from __future__ import annotations

import string
from typing import Any

import voluptuous as vol
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_EFFECT, ATTR_RGB_COLOR, ColorMode, LightEntity, LightEntityFeature
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.color import color_name_to_rgb

from . import TrimlightConfigEntry
from .const import (
    ATTR_COLOR,
    ATTR_COLORS,
    ATTR_DOTS,
    ATTR_EFFECT_ID,
    ATTR_EFFECTS,
    ATTR_ENABLED,
    ATTR_INTERVAL,
    ATTR_LENGTH,
    ATTR_MODE,
    ATTR_NAME,
    ATTR_NEW_NAME,
    ATTR_OFF_OFFSET,
    ATTR_OFF_SUN,
    ATTR_OFF_TIME,
    ATTR_ON_OFFSET,
    ATTR_ON_SUN,
    ATTR_ON_TIME,
    ATTR_OVERLAY,
    ATTR_REPEAT,
    ATTR_REVERSE,
    ATTR_SCHEDULE,
    ATTR_SENSITIVITY,
    ATTR_SPEED,
    DEFAULT_PATTERN_DOTS,
    MUSIC_EFFECTS,
    PATTERN_MODES,
    REPEATS,
    SERVICE_DELETE_SAVED_EFFECT,
    SERVICE_RENAME_SAVED_EFFECT,
    SERVICE_SAVE_EFFECT,
    SERVICE_SET_COMBINED_EFFECT,
    SERVICE_SET_DAILY_SCHEDULE,
    SERVICE_SET_OVERLAY,
    SERVICE_SHOW_EFFECT,
    SERVICE_SHOW_PATTERN,
    SERVICE_SHOW_SAVED_EFFECT,
    SERVICE_START_MUSIC,
)
from .coordinator import TrimlightCoordinator
from .effects import BUILTIN_EFFECTS
from .entity import TrimlightEntity
from .sun import SUN_EVENTS, SUN_OFFSET, time_setting
from .trimlight import (
    CATEGORY_BUILTIN,
    CATEGORY_CUSTOM,
    LIBRARY_NAME_LEN,
    MAX_SEGMENT_DOTS,
    SEGMENT_COUNT,
    CurrentEffect,
    LibraryEffect,
    OverlayKind,
    SwitchState,
    encode_segments,
)

OVERLAYS = {"none": None, "lightning": OverlayKind.LIGHTNING, "snow": OverlayKind.SNOW}


def _color(value: Any) -> tuple[int, int, int] | None:
    """A colour name, hex code or [R, G, B]; None or "none" is the app's "no colour", which leaves those dots unlit."""
    if value is None or (isinstance(value, str) and value.strip().lower() == "none"):
        return None
    if isinstance(value, str):
        text = value.strip().removeprefix("#")
        if len(text) == 6 and all(c in string.hexdigits for c in text):
            return tuple(bytes.fromhex(text))  # type: ignore[return-value]
        try:
            return tuple(color_name_to_rgb(value.strip()))  # type: ignore[return-value]
        except ValueError as err:
            raise vol.Invalid(f"Unknown colour {value!r}") from err
    return tuple(vol.ExactSequence([vol.All(vol.Coerce(int), vol.Range(min=0, max=255))] * 3)(list(value)))  # type: ignore[return-value]


def _effect_name(value: Any) -> str:
    name = cv.string(value).strip()
    if not 1 <= len(name) <= LIBRARY_NAME_LEN:
        raise vol.Invalid(f"A saved effect's name is 1 to {LIBRARY_NAME_LEN} characters")
    try:
        name.encode("latin-1")
    except UnicodeEncodeError as err:
        raise vol.Invalid("The controller stores names in Latin-1; use letters, digits and common punctuation") from err
    return name


_DOTS = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_SEGMENT_DOTS))
_BYTE = vol.All(vol.Coerce(int), vol.Range(min=0, max=255))
_COLORS = vol.All(cv.ensure_list, vol.Length(min=1, max=SEGMENT_COUNT), [_color])
_PATTERN_OPTIONS = {
    vol.Optional(ATTR_DOTS, default=DEFAULT_PATTERN_DOTS): vol.Any(_DOTS, vol.All(cv.ensure_list, [_DOTS])),
    vol.Optional(ATTR_SPEED): _BYTE,
    vol.Optional(ATTR_BRIGHTNESS): _BYTE,
}


async def async_setup_entry(hass: HomeAssistant, entry: TrimlightConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([TrimlightLight(entry.runtime_data, entry)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_SHOW_SAVED_EFFECT, {vol.Required(ATTR_EFFECT_ID): _BYTE}, "async_show_saved_effect")
    platform.async_register_entity_service(
        SERVICE_SHOW_PATTERN, {vol.Required(ATTR_MODE): vol.In(PATTERN_MODES), vol.Required(ATTR_COLORS): _COLORS, **_PATTERN_OPTIONS}, "async_show_pattern"
    )
    platform.async_register_entity_service(
        SERVICE_SAVE_EFFECT,
        {vol.Required(ATTR_NAME): _effect_name, vol.Inclusive(ATTR_MODE, "pattern"): vol.In(PATTERN_MODES), vol.Inclusive(ATTR_COLORS, "pattern"): _COLORS, **_PATTERN_OPTIONS},
        "async_save_effect",
    )
    platform.async_register_entity_service(SERVICE_RENAME_SAVED_EFFECT, {vol.Required(ATTR_EFFECT): cv.string, vol.Required(ATTR_NEW_NAME): _effect_name}, "async_rename_saved_effect")
    platform.async_register_entity_service(SERVICE_DELETE_SAVED_EFFECT, {vol.Required(ATTR_EFFECT): cv.string}, "async_delete_saved_effect")
    platform.async_register_entity_service(SERVICE_SET_OVERLAY, {vol.Required(ATTR_EFFECT): cv.string, vol.Required(ATTR_OVERLAY): vol.In(OVERLAYS)}, "async_set_overlay")
    platform.async_register_entity_service(
        SERVICE_SET_COMBINED_EFFECT,
        {vol.Required(ATTR_EFFECTS): vol.All(cv.ensure_list, vol.Length(min=1, max=255), [cv.string]), vol.Required(ATTR_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=1, max=10))},
        "async_set_combined_effect",
    )
    platform.async_register_entity_service(
        SERVICE_SET_DAILY_SCHEDULE,
        {
            vol.Required(ATTR_SCHEDULE): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
            vol.Optional(ATTR_ENABLED): cv.boolean,
            vol.Optional(ATTR_EFFECT): cv.string,
            vol.Optional(ATTR_REPEAT): vol.In(REPEATS),
            vol.Exclusive(ATTR_ON_TIME, "on"): cv.time,
            vol.Exclusive(ATTR_ON_SUN, "on"): vol.In(SUN_EVENTS),
            vol.Optional(ATTR_ON_OFFSET, default=0): SUN_OFFSET,
            vol.Exclusive(ATTR_OFF_TIME, "off"): cv.time,
            vol.Exclusive(ATTR_OFF_SUN, "off"): vol.In(SUN_EVENTS),
            vol.Optional(ATTR_OFF_OFFSET, default=0): SUN_OFFSET,
        },
        "async_set_daily_schedule",
    )
    platform.async_register_entity_service(
        SERVICE_SHOW_EFFECT,
        {
            vol.Required(ATTR_EFFECT): vol.In(BUILTIN_EFFECTS),
            vol.Optional(ATTR_SPEED): _BYTE,
            vol.Optional(ATTR_LENGTH): vol.All(vol.Coerce(int), vol.Range(min=1, max=90)),
            vol.Optional(ATTR_REVERSE): cv.boolean,
            vol.Optional(ATTR_BRIGHTNESS): _BYTE,
        },
        "async_show_effect",
    )
    platform.async_register_entity_service(
        SERVICE_START_MUSIC,
        {
            vol.Required(ATTR_EFFECT): vol.In(MUSIC_EFFECTS),
            vol.Optional(ATTR_COLOR): _color,
            vol.Optional(ATTR_LENGTH): vol.All(vol.Coerce(int), vol.Range(min=1, max=150)),
            vol.Optional(ATTR_SENSITIVITY): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
        },
        "async_start_music",
    )


class TrimlightLight(TrimlightEntity, LightEntity, RestoreEntity):
    """The light strip. Effect and brightness follow the controller; a solid colour's RGB is only known if Home Assistant set it."""

    _attr_name = None
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, coordinator: TrimlightCoordinator, entry: TrimlightConfigEntry) -> None:
        super().__init__(coordinator, entry, "light")
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)
        self._attr_effect = None
        self._seen_effect: CurrentEffect | None = None
        self._seen_reply: bytes | None = None
        self._trust_poll = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state == STATE_ON:
            self._attr_brightness = last.attributes.get(ATTR_BRIGHTNESS) or self._attr_brightness
            if rgb := last.attributes.get(ATTR_RGB_COLOR):
                self._attr_rgb_color = tuple(rgb)
            if (effect := last.attributes.get(ATTR_EFFECT)) in self.effect_list:
                self._attr_effect = effect
        self._adopt_current_effect()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._adopt_current_effect()
        super()._handle_coordinator_update()

    def _adopt_current_effect(self) -> None:
        """Follow changes made outside Home Assistant (the app, schedules); what Home Assistant sent itself reads back the same."""
        data = self.coordinator.data
        # An assumed state after a command reuses the last reply; only a new reply is a poll.
        if data is None or data.raw is self._seen_reply:
            return
        self._seen_reply = data.raw
        current = data.current_effect
        if current is None or (current == self._seen_effect and not self._trust_poll):
            return
        self._seen_effect = current
        self._trust_poll = False
        if current.category == CATEGORY_BUILTIN and current.mode >= len(BUILTIN_EFFECTS):
            self._attr_effect = None
            return
        self._attr_effect = self._effect_name(current.category, current.mode, current.speed, current.segments)
        self._attr_brightness = current.brightness or self._attr_brightness

    def _effect_name(self, category: int, mode: int, speed: int, segments: bytes) -> str | None:
        if category == CATEGORY_BUILTIN:
            return BUILTIN_EFFECTS[mode] if mode < len(BUILTIN_EFFECTS) else None
        if category != CATEGORY_CUSTOM:
            return None
        # A saved effect shown at another speed or brightness is still that effect.
        same_pattern = [(name, e) for name, e in self.coordinator.saved_effects().items() if (e.category, e.mode, e.segments) == (category, mode, segments)]
        return next((name for name, e in same_pattern if e.speed == speed), same_pattern[0][0] if same_pattern else None)

    @property
    def effect_list(self) -> list[str]:
        return [*self.coordinator.saved_effects(), *BUILTIN_EFFECTS]

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else data.is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        coordinator = self.coordinator
        state = data.switch_state if data is not None else None
        attributes: dict[str, Any] = {
            "mode": SwitchState(state).name.lower() if state in SwitchState._value2member_map_ else state,
            "verified": coordinator.client.verified,
            "controller_busy": coordinator.busy,
        }
        if data is not None:
            attributes["overlays"] = {coordinator.saved_effect_name(o.effect_id) or str(o.effect_id): OverlayKind(o.kind).name.lower() if o.kind in OverlayKind._value2member_map_ else o.kind for o in data.overlays}
            if data.combined is not None:
                attributes["combined_effect"] = [coordinator.saved_effect_name(i) or str(i) for i in data.combined.effect_ids]
                attributes["combined_effect_interval"] = data.combined.interval
        return attributes

    @property
    def _brightness(self) -> int:
        return self._attr_brightness or 255

    async def async_turn_on(self, **kwargs: Any) -> None:
        effect = kwargs.get(ATTR_EFFECT)
        if effect is not None and effect not in self.effect_list:
            raise ServiceValidationError(f"Unknown effect {effect!r}")
        if not kwargs.keys() & {ATTR_BRIGHTNESS, ATTR_EFFECT, ATTR_RGB_COLOR}:
            await self.coordinator.async_turn_on()
            return
        self._trust_next_poll()
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = kwargs[ATTR_RGB_COLOR]
            self._attr_effect = None
            await self._show_color()
        elif effect is not None:
            self._attr_effect = effect
            if (saved := self.coordinator.saved_effects().get(effect)) is not None:
                if ATTR_BRIGHTNESS not in kwargs:
                    self._attr_brightness = saved.brightness
                await self._show_saved(saved, chosen=True)
            else:
                await self.coordinator.async_show_builtin(BUILTIN_EFFECTS.index(effect), self._brightness)
        else:
            await self._show_at_new_brightness()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_switch_state(SwitchState.OFF)

    async def async_show_saved_effect(self, effect_id: int) -> None:
        self._trust_next_poll()
        name = self.coordinator.saved_effect_name(effect_id)
        self._attr_effect = name
        if name is not None and (saved := self.coordinator.saved_effects().get(name)) is not None:
            self._attr_brightness = saved.brightness
        await self.coordinator.async_show_saved(effect_id)
        self.async_write_ha_state()

    async def async_show_pattern(self, mode: str, colors: list[tuple[int, int, int]], dots: int | list[int], speed: int | None = None, brightness: int | None = None) -> None:
        self._trust_next_poll()
        if brightness is not None:
            self._attr_brightness = brightness
        speed = self.coordinator.effect_settings.speed if speed is None else speed
        segments = _segments(colors, dots)
        self._attr_effect = self._effect_name(CATEGORY_CUSTOM, PATTERN_MODES[mode], speed, segments)
        await self.coordinator.async_show_pattern(PATTERN_MODES[mode], speed, self._brightness, segments)
        self.async_write_ha_state()

    async def async_save_effect(self, name: str, mode: str | None = None, colors: list[tuple[int, int, int]] | None = None, dots: int | list[int] = DEFAULT_PATTERN_DOTS, speed: int | None = None, brightness: int | None = None) -> None:
        """Save the given pattern, or without one, whatever the lights show now."""
        coordinator = self.coordinator
        if mode is not None and colors is not None:
            speed = coordinator.effect_settings.speed if speed is None else speed
            effect = LibraryEffect(0, name, CATEGORY_CUSTOM, PATTERN_MODES[mode], speed, self._brightness if brightness is None else brightness, _segments(colors, dots))
        else:
            current = coordinator.data.current_effect if coordinator.data is not None else None
            if current is None or current.category not in (CATEGORY_BUILTIN, CATEGORY_CUSTOM):
                raise ServiceValidationError("The controller is not showing an effect that can be saved")
            if current.category == CATEGORY_BUILTIN and current.mode >= len(BUILTIN_EFFECTS):
                raise ServiceValidationError("A solid colour cannot be saved; save a Static pattern with that colour instead")
            builtin = current.category == CATEGORY_BUILTIN
            effect = LibraryEffect(0, name, current.category, current.mode, current.speed, current.brightness, current.segments, current.pixel_len if builtin else 0, current.reverse and builtin)
        self._trust_next_poll()
        await coordinator.async_save_effect(name, effect)

    async def async_rename_saved_effect(self, effect: str, new_name: str) -> None:
        self._trust_next_poll()
        await self.coordinator.async_rename_saved_effect(effect, new_name)

    async def async_delete_saved_effect(self, effect: str) -> None:
        self._trust_next_poll()
        await self.coordinator.async_delete_saved_effect(effect)

    async def async_set_overlay(self, effect: str, overlay: str) -> None:
        await self.coordinator.async_set_overlay(effect, OVERLAYS[overlay])

    async def async_set_combined_effect(self, effects: list[str], interval: int) -> None:
        await self.coordinator.async_set_combined_effect(effects, interval)

    async def async_show_effect(self, effect: str, speed: int | None = None, length: int | None = None, reverse: bool | None = None, brightness: int | None = None) -> None:
        """Show a built-in effect with its settings in one command, instead of one command per setting."""
        self._trust_next_poll()
        self._attr_effect = effect
        if brightness is not None:
            self._attr_brightness = brightness
        await self.coordinator.async_show_builtin(BUILTIN_EFFECTS.index(effect), self._brightness, speed=speed, pixel_len=length, reverse=reverse)
        self.async_write_ha_state()

    async def async_start_music(self, effect: str, color: tuple[int, int, int] | None = None, length: int | None = None, sensitivity: int | None = None) -> None:
        """Start a music effect driven by the controller's own audio input; settings left out keep the controller's current ones."""
        music = self.coordinator.data.music if self.coordinator.data is not None else None
        self._trust_next_poll()
        self._attr_effect = None
        await self.coordinator.async_start_music(
            MUSIC_EFFECTS.index(effect),
            color if color is not None else (music.color if music else (255, 0, 0)),
            length if length is not None else (music.length if music else 70),
            sensitivity if sensitivity is not None else (music.sensitivity if music and music.sensitivity else 13),
        )
        self.async_write_ha_state()

    async def async_set_daily_schedule(self, schedule: int, **fields: Any) -> None:
        """Change several settings of a daily schedule in one write; the controller pauses for seconds after each one."""
        changes: dict[str, Any] = {}
        if ATTR_ENABLED in fields:
            changes["enabled"] = fields[ATTR_ENABLED]
        if ATTR_EFFECT in fields:
            changes["effect_id"] = self.coordinator.schedulable_effect_id(fields[ATTR_EFFECT])
        if ATTR_REPEAT in fields:
            changes["repeat"] = REPEATS[fields[ATTR_REPEAT]]
        for which in ("on", "off"):
            if f"{which}_time" in fields or f"{which}_sun" in fields:
                changes[f"{which}_time"] = time_setting(fields.get(f"{which}_time"), fields.get(f"{which}_sun"), fields[f"{which}_offset"], which)
        await self.coordinator.async_set_daily_schedule(schedule - 1, **changes)

    def _trust_next_poll(self) -> None:
        # The controller never acknowledges a command and occasionally ignores one, so the next poll decides what is shown.
        self._trust_poll = True

    async def _show_saved(self, saved: LibraryEffect, *, chosen: bool) -> None:
        """Show a saved effect: as saved when just chosen, otherwise with the current speed, length and direction settings."""
        coordinator = self.coordinator
        settings = coordinator.effect_settings
        speed, pixel_len, reverse = (saved.speed, saved.pixel_len, saved.reverse) if chosen else (settings.speed, settings.pixel_len, settings.reverse)
        builtin = saved.category == CATEGORY_BUILTIN
        if (self._brightness, speed) == (saved.brightness, saved.speed) and (not builtin or (pixel_len, reverse) == (saved.pixel_len, saved.reverse)):
            await coordinator.async_show_saved(saved.id)
        elif builtin:
            await coordinator.async_show_builtin(saved.mode, self._brightness, speed=speed, pixel_len=pixel_len or settings.pixel_len, reverse=reverse)
        else:
            await coordinator.async_show_pattern(saved.mode, speed, self._brightness, saved.segments)

    async def _show_at_new_brightness(self) -> None:
        coordinator = self.coordinator
        current = coordinator.data.current_effect if coordinator.data is not None else None
        if (saved := coordinator.saved_effects().get(self._attr_effect)) is not None:
            await self._show_saved(saved, chosen=False)
        elif self._attr_effect in BUILTIN_EFFECTS:
            await coordinator.async_show_builtin(BUILTIN_EFFECTS.index(self._attr_effect), self._brightness)
        elif current is not None and current.category == CATEGORY_CUSTOM:
            await coordinator.async_show_pattern(current.mode, current.speed, self._brightness, current.segments)
        else:
            await self._show_color()

    async def _show_color(self) -> None:
        # The solid-colour command has no brightness byte, so brightness is applied to the colour itself.
        red, green, blue = (round(c * self._brightness / 255) for c in self._attr_rgb_color or (255, 255, 255))
        await self.coordinator.async_show_color(red, green, blue)


def _segments(colors: list[tuple[int, int, int] | None], dots: int | list[int]) -> bytes:
    counts = dots if isinstance(dots, list) else [dots] * len(colors)
    if len(counts) != len(colors):
        raise ServiceValidationError(f"Got {len(counts)} dots values for {len(colors)} colours; give one per colour, or a single number")
    return encode_segments(list(zip(counts, colors)))
