"""The data the Trimlight page and card read: saved effects with their colours, schedules, the combined effect and the mode."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util

from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET

from .const import COMBINED_EFFECT_NAME, DOMAIN, MUSIC_EFFECTS, PATTERN_MODES, REPEATS, ROOFLINES
from .coordinator import TrimlightCoordinator, writable_from
from .holiday_rules import HOLIDAYS, HolidayRule
from .sun import SunTime, now, window_times
from .effects import BUILTIN_EFFECTS, CUSTOM_MODES_V2
from .trimlight import (
    CATEGORY_BUILTIN,
    CATEGORY_CUSTOM,
    CATEGORY_MUSIC,
    CHANNEL_ORDERS,
    IC_TYPES,
    LIBRARY_NAME_LEN,
    MAX_PORT_DOT,
    MAX_SEGMENT_DOTS,
    PORT_COUNT,
    SEGMENT_COUNT,
    MusicSource,
    OverlayKind,
    SwitchState,
    decode_segments,
)

_MODE_KEYS = list(PATTERN_MODES)
_REPEAT_KEYS = {value: key for key, value in REPEATS.items()}


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_state)
    websocket_api.async_register_command(hass, ws_favourites)
    websocket_api.async_register_command(hass, ws_roofline)
    websocket_api.async_register_command(hass, ws_led_test)
    websocket_api.async_register_command(hass, ws_led_setup)
    websocket_api.async_register_command(hass, ws_led_session_end)
    websocket_api.async_register_command(hass, ws_rename)
    websocket_api.async_register_command(hass, ws_waiting_delete)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/state", vol.Required("entity_id"): cv.entity_id})
@callback
def ws_state(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    found = _entry(hass, msg["entity_id"])
    if found is None or found.runtime_data.data is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, f"{msg['entity_id']} is not a Trimlight light that is set up and reachable")
        return
    connection.send_result(msg["id"], describe(found.runtime_data, _entity_ids(hass, found)))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/favourites",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("favourites"): [vol.All(vol.Coerce(int), vol.Range(min=0, max=len(BUILTIN_EFFECTS) - 1))],
    }
)
@websocket_api.async_response
async def ws_favourites(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    found = _entry(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, f"{msg['entity_id']} is not a Trimlight light that is set up")
        return
    await found.runtime_data.async_set_favourites(msg["favourites"])
    connection.send_result(msg["id"], {"favourites": found.runtime_data.favourites})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/roofline", vol.Required("entity_id"): cv.entity_id, vol.Required("roofline"): vol.In(ROOFLINES)})
@websocket_api.async_response
async def ws_roofline(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    found = _entry(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, f"{msg['entity_id']} is not a Trimlight light that is set up")
        return
    await found.runtime_data.async_set_roofline(msg["roofline"])
    connection.send_result(msg["id"], {"roofline": found.runtime_data.roofline})


async def _run_on_controller(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], action: Any) -> None:
    found = _entry(hass, msg["entity_id"])
    if found is None or found.runtime_data.data is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, f"{msg['entity_id']} is not a Trimlight light that is set up and reachable")
        return
    try:
        await action(found.runtime_data)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/led_test", vol.Required("entity_id"): cv.entity_id, vol.Required("show"): vol.In(["red", "green", "blue", "white", "pattern"])})
@websocket_api.async_response
async def ws_led_test(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run_on_controller(hass, connection, msg, lambda c: c.async_led_test(msg["show"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/led_setup",
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("ic_type"): vol.In(IC_TYPES),
        vol.Optional("channel_order"): vol.In(CHANNEL_ORDERS),
        vol.Optional("ports"): vol.All(
            [{vol.Required("start"): vol.All(int, vol.Range(min=1, max=MAX_PORT_DOT)), vol.Required("end"): vol.All(int, vol.Range(min=1, max=MAX_PORT_DOT)), vol.Required("enabled"): bool}],
            vol.Length(min=PORT_COUNT, max=PORT_COUNT),
        ),
    }
)
@websocket_api.async_response
async def ws_led_setup(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    ports = None if "ports" not in msg else [(p["start"], p["end"], p["enabled"]) for p in msg["ports"]]
    await _run_on_controller(hass, connection, msg, lambda c: c.async_try_led_setup(ic_type=msg.get("ic_type"), channel_order=msg.get("channel_order"), ports=ports))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/waiting_delete", vol.Required("entity_id"): cv.entity_id, vol.Required("waiting_id"): str})
@websocket_api.async_response
async def ws_waiting_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run_on_controller(hass, connection, msg, lambda c: c.async_delete_waiting_schedule(msg["waiting_id"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/rename", vol.Required("entity_id"): cv.entity_id, vol.Required("name"): str})
@websocket_api.async_response
async def ws_rename(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run_on_controller(hass, connection, msg, lambda c: c.async_rename_controller(msg["name"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/led_session_end", vol.Required("entity_id"): cv.entity_id, vol.Required("keep"): bool})
@websocket_api.async_response
async def ws_led_session_end(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run_on_controller(hass, connection, msg, lambda c: c.async_end_led_session(msg["keep"]))


def describe(coordinator: TrimlightCoordinator, entity_ids: dict[str, str]) -> dict[str, Any]:
    data = coordinator.data
    overlays = {o.effect_id: OverlayKind(o.kind).name.lower() for o in data.overlays if o.kind in OverlayKind._value2member_map_}
    effects = []
    for name, effect in coordinator.saved_effects().items():
        item: dict[str, Any] = {
            "id": effect.id,
            "name": name,
            "stored_name": effect.name,
            "speed": effect.speed,
            "brightness": effect.brightness,
            "overlay": overlays.get(effect.id),
            "used_by": coordinator.effect_users(effect.id),
        }
        item.update(_look(effect.category, effect.mode, effect.segments, effect.pixel_len, effect.reverse))
        effects.append(item)
    current = data.current_effect
    return {
        "entities": entity_ids,
        "switch_state": SwitchState(data.switch_state).name.lower() if data.switch_state in SwitchState._value2member_map_ else None,
        "modes": [{"key": key, "name": name.capitalize()} for key, name in zip(_MODE_KEYS, CUSTOM_MODES_V2)],
        "repeats": list(REPEATS),
        "combined_name": COMBINED_EFFECT_NAME,
        "builtins": list(BUILTIN_EFFECTS),
        "favourites": coordinator.favourites,
        "roofline": coordinator.roofline,
        "music": _music(data.music),
        "device": _device(coordinator),
        "limits": {"colors": SEGMENT_COUNT, "dots": MAX_SEGMENT_DOTS, "name": LIBRARY_NAME_LEN},
        "effects": effects,
        "current": None if current is None else {"speed": current.speed, "brightness": current.brightness, **_look(current.category, current.mode, current.segments, current.pixel_len, current.reverse)},
        "calendar": [
            {
                "id": s.id,
                "effect": coordinator.saved_effect_name(s.effect_id),
                "start": list(s.start),
                "end": list(s.end),
                "on": f"{s.on_time:%H:%M}",
                "off": f"{s.off_time:%H:%M}",
                "on_sun": _sun(coordinator.sun_setting("calendar", s.id, "on")),
                "off_sun": _sun(coordinator.sun_setting("calendar", s.id, "off")),
                "holiday": _holiday(coordinator, coordinator.holiday_rule(s.id), f"c{s.id}"),
            }
            for s in data.calendar
        ],
        "waiting": _waiting(coordinator),
        "holidays": _holidays(coordinator),
        "daily": [
            {
                "enabled": s.enabled,
                "effect": coordinator.saved_effect_name(s.effect_id),
                "repeat": _REPEAT_KEYS.get(s.repeat),
                "on": f"{s.on_time:%H:%M}",
                "off": f"{s.off_time:%H:%M}",
                "on_sun": _sun(coordinator.sun_setting("daily", i, "on")),
                "off_sun": _sun(coordinator.sun_setting("daily", i, "off")),
            }
            for i, s in enumerate(data.daily)
        ],
        "sun": _sun_today(coordinator),
        "combined": None
        if data.combined is None
        else {"effects": [name for i in data.combined.effect_ids if (name := coordinator.saved_effect_name(i)) is not None], "interval": data.combined.interval},
    }


def _holiday(coordinator: TrimlightCoordinator, rule: HolidayRule | None, ident: str) -> dict[str, Any] | None:
    if rule is None:
        return None
    _, skipped = coordinator.holiday_plan(ident, rule, now().date())
    return {
        "key": rule.holiday,
        "start_offset": rule.start_offset,
        "end_offset": rule.end_offset,
        "problem": rule.problem,
        "skips": [{"year": year, "to": label} for year, label in skipped],
    }


def _waiting(coordinator: TrimlightCoordinator) -> list[dict[str, Any]]:
    """Holiday schedules held off the controller: when they next light, and from when they can go back on."""
    today = now().date()
    out = []
    for key, waiting in coordinator.holidays.waiting.items():
        window, _ = coordinator.holiday_plan(key, waiting.rule, today)
        times = None if window is None else window_times(coordinator.hass, waiting.on, waiting.off, window[0])

        def clock(index: int, setting: Any, times: Any = times) -> str | None:
            return f"{times[index]:%H:%M}" if times else None if isinstance(setting, SunTime) else f"{setting:%H:%M}"

        out.append(
            {
                "id": key,
                "effect": coordinator.saved_effect_name(waiting.effect_id),
                "holiday": _holiday(coordinator, waiting.rule, key),
                "on": clock(0, waiting.on),
                "off": clock(1, waiting.off),
                "on_sun": _sun(waiting.on if isinstance(waiting.on, SunTime) else None),
                "off_sun": _sun(waiting.off if isinstance(waiting.off, SunTime) else None),
                "next": None if window is None else [window[0].isoformat(), window[1].isoformat()],
                "from": None if window is None else writable_from(window, today).isoformat(),
            }
        )
    return out


def _holidays(coordinator: TrimlightCoordinator) -> list[dict[str, Any]]:
    """The holidays a schedule can follow, with their dates from last year to a few years ahead for the page's previews."""
    year = now().year
    dates = coordinator.holiday_dates
    return [
        {"key": key, "name": holiday.name, "group": holiday.group, "dates": {str(y): d.isoformat() for y in range(year - 1, year + 4) if (d := dates.date(key, y)) is not None}}
        for key, holiday in HOLIDAYS.items()
        if dates.available(key)
    ]


def _look(category: int, mode: int, segments: bytes, pixel_len: int, reverse: bool) -> dict[str, Any]:
    if category == CATEGORY_CUSTOM:
        colors = [{"color": None if rgb is None else "#{:02x}{:02x}{:02x}".format(*rgb), "dots": dots} for dots, rgb in decode_segments(segments)]
        return {"kind": "pattern", "mode": _MODE_KEYS[mode] if mode < len(_MODE_KEYS) else None, "colors": colors}
    if category == CATEGORY_BUILTIN and mode < len(BUILTIN_EFFECTS):
        return {"kind": "builtin", "effect": BUILTIN_EFFECTS[mode], "length": pixel_len, "reverse": reverse}
    if category == CATEGORY_BUILTIN:
        return {"kind": "color"}
    if category == CATEGORY_MUSIC:
        return {"kind": "music", "effect": MUSIC_EFFECTS[mode] if mode < len(MUSIC_EFFECTS) else None}
    return {"kind": "other"}


def _device(coordinator: TrimlightCoordinator) -> dict[str, Any]:
    """The installer settings and versions the Settings tab shows."""
    data = coordinator.data
    firmware = coordinator.client.firmware
    mask = data.port_mask
    return {
        "name": coordinator.config_entry.title,
        "host": coordinator.client.host,
        "firmware": firmware.version if firmware else None,
        "mcu_version": f"{data.mcu_version:#06x}" if data.mcu_version is not None else None,
        "ic_type": IC_TYPES.get(data.ic_type, data.ic_type),
        "channel_order": CHANNEL_ORDERS.get(data.channel_order, data.channel_order),
        "ic_type_value": data.ic_type,
        "channel_order_value": data.channel_order,
        "ic_types": [{"value": k, "name": v} for k, v in IC_TYPES.items()],
        "channel_orders": [{"value": k, "name": v} for k, v in CHANNEL_ORDERS.items()],
        "led_session": None
        if coordinator.led_session is None
        else {
            "ends_at": coordinator.led_session.ends_at.isoformat(),
            "seconds_left": max(0, round((coordinator.led_session.ends_at - dt_util.utcnow()).total_seconds())),
            "changed": coordinator.led_setup() != coordinator.led_session.setup,
        },
        "ports": [{"start": start, "end": end, "enabled": None if mask is None else bool(mask >> i & 1)} for i, (start, end) in enumerate(data.ports)],
        "max_port_dot": MAX_PORT_DOT,
        "clock": data.clock.isoformat(timespec="minutes") if data.clock else None,
        "time_sync": None if data.time_sync_auto is None else ("auto" if data.time_sync_auto else "manual"),
    }


def _music(music: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {"effects": list(MUSIC_EFFECTS)}
    if music is not None:
        source = MusicSource(music.source).name.lower() if music.source in MusicSource._value2member_map_ else None
        settings.update(source=source, color="#{:02x}{:02x}{:02x}".format(*music.color), length=music.length, sensitivity=music.sensitivity)
    return settings


def _sun_today(coordinator: TrimlightCoordinator) -> dict[str, Any]:
    today = now().date()
    sunrise, sunset = (SunTime(event).on_day(coordinator.hass, today) for event in (SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET))
    # Sunset before noon means Home Assistant's home location and time zone disagree, typically a location never set.
    plausible = sunrise is None or sunset is None or sunrise.hour < 12 <= sunset.hour
    return {"sunrise": _clock(sunrise), "sunset": _clock(sunset), "location_ok": plausible}


def _sun(setting: SunTime | None) -> dict[str, Any] | None:
    return None if setting is None else setting.as_dict()


def _clock(value: Any) -> str | None:
    return None if value is None else f"{value:%H:%M}"


def _entry(hass: HomeAssistant, entity_id: str) -> ConfigEntry | None:
    if (entity := er.async_get(hass).async_get(entity_id)) is None or entity.platform != DOMAIN or entity.config_entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    return entry if entry is not None and entry.state is ConfigEntryState.LOADED else None


def _entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """The light, mode select and schedules calendar of one controller, by role."""
    base_id = entry.unique_id or entry.entry_id
    roles = {f"{base_id}_light": "light", f"{base_id}_mode": "mode", f"{base_id}_schedules": "calendar"}
    return {roles[e.unique_id]: e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) if e.unique_id in roles}
