import asyncio
from datetime import date, datetime, time
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_EFFECT, ATTR_RGB_COLOR, DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import ATTR_VALUE, DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import ATTR_OPTION, DOMAIN as SELECT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.time import DOMAIN as TIME_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.trimlight_local.const import DOMAIN
from custom_components.trimlight_local.effects import BUILTIN_EFFECTS
from custom_components.trimlight_local.holiday_rules import HolidayDates, HolidayRule, HolidayRules
from custom_components.trimlight_local.sun import SunRules, SunTime
from custom_components.trimlight_local.trimlight import DiscoveredDevice, Sub, TrimlightClient

LIGHT = "light.porch"
MODE = "select.porch_mode"
SPEED = "number.porch_effect_speed"
LENGTH = "number.porch_effect_length"
REVERSE = "switch.porch_reverse_direction"
CALENDAR = "calendar.porch_schedules"
DAILY_1 = "switch.porch_daily_schedule_1"
INVALID = (vol.Invalid, ServiceValidationError)


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", unique_id="127.0.0.1", data={CONF_HOST: "127.0.0.1"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def settle(hass: HomeAssistant) -> None:
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


async def test_config_flow_creates_entry(hass: HomeAssistant, controller) -> None:
    found = [DiscoveredDevice("127.0.0.1", "Roofline", "TL-1", 1, 0, None)]
    with patch("custom_components.trimlight_local.config_flow.discover", return_value=found):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        assert result["description_placeholders"]["found"] == "Roofline (127.0.0.1)"
        with patch("custom_components.trimlight_local.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Roofline"
    assert result["data"] == {CONF_HOST: "127.0.0.1"}


async def test_config_flow_cannot_connect(hass: HomeAssistant) -> None:
    with patch("custom_components.trimlight_local.config_flow.discover", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_light_and_mode(hass: HomeAssistant, controller) -> None:
    entry = await setup_entry(hass)
    state = hass.states.get(LIGHT)
    assert state.state == STATE_ON
    assert len(state.attributes["effect_list"]) == 180
    assert hass.states.get(MODE).state == "manual"

    await hass.services.async_call(LIGHT_DOMAIN, "turn_off", {ATTR_ENTITY_ID: LIGHT}, blocking=True)
    await settle(hass)
    assert controller.switch_state == 0
    assert hass.states.get(LIGHT).state == STATE_OFF
    assert hass.states.get(MODE).state == "off"

    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Rainbow Wave", ATTR_BRIGHTNESS: 128}, blocking=True)
    await settle(hass)
    assert controller.switch_state == 1
    assert controller.passthrough[-1] == (Sub.PREVIEW_BUILTIN, bytes((3, 100, 128, 30, 0)))
    state = hass.states.get(LIGHT)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_EFFECT] == "Rainbow Wave"

    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT, ATTR_RGB_COLOR: (255, 100, 0), ATTR_BRIGHTNESS: 51}, blocking=True)
    await settle(hass)
    assert controller.passthrough[-1] == (Sub.PREVIEW_COLOR, bytes((51, 20, 0)))
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] is None

    await hass.services.async_call(DOMAIN, "show_saved_effect", {ATTR_ENTITY_ID: LIGHT, "effect_id": 4}, blocking=True)
    await settle(hass)
    assert controller.passthrough[-1] == (Sub.SHOW_LIBRARY_EFFECT, b"\x04")

    await hass.services.async_call(SELECT_DOMAIN, "select_option", {ATTR_ENTITY_ID: MODE, ATTR_OPTION: "timer"}, blocking=True)
    await settle(hass)
    assert controller.switch_state == 2
    assert hass.states.get(MODE).state == "timer"
    assert hass.states.get(LIGHT).attributes["mode"] == "timer"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_saved_effects_from_captured_library(hass: HomeAssistant, controller) -> None:
    captured = bytes.fromhex((Path(__file__).parents[1] / "fixtures" / "sync_reply.hex").read_text().strip())
    controller.sync_tail = captured[1:]
    entry = await setup_entry(hass)
    state = hass.states.get(LIGHT)
    effects = state.attributes["effect_list"]
    assert effects[:3] == ["NEW YEAR", "VALENTINE'S DAY", "ST.PATRICK'S DAY"]
    assert len(effects) == 17 + 180
    assert state.attributes[ATTR_EFFECT] == "CANADA DAY"

    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "HALLOWEEN"}, blocking=True)
    await settle(hass)
    assert controller.passthrough[-1] == (Sub.SHOW_LIBRARY_EFFECT, b"\x07")
    assert hass.states.get(LIGHT).attributes[ATTR_BRIGHTNESS] == 255

    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT, ATTR_BRIGHTNESS: 100}, blocking=True)
    await settle(hass)
    sub, data = controller.passthrough[-1]
    assert sub == Sub.PREVIEW_CUSTOM
    assert data[:3] == bytes((1, 1, 100))
    assert data[3:8] == bytes.fromhex("05ff260001")
    assert len(data) == 3 + 150

    await hass.services.async_call(DOMAIN, "show_saved_effect", {ATTR_ENTITY_ID: LIGHT, "effect_id": 9}, blocking=True)
    await settle(hass)
    state = hass.states.get(LIGHT)
    assert state.attributes[ATTR_EFFECT] == "CHRISTMAS"
    assert state.attributes[ATTR_BRIGHTNESS] == 184

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_follows_changes_made_outside_home_assistant(hass: HomeAssistant, controller) -> None:
    fixtures = Path(__file__).parents[1] / "fixtures"
    controller.sync_tail = bytes.fromhex((fixtures / "sync_reply.hex").read_text().strip())[1:]
    entry = await setup_entry(hass)
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "CANADA DAY"

    controller.sync_tail = bytes.fromhex((fixtures / "sync_reply_builtin.hex").read_text().strip())[1:]
    with patch("custom_components.trimlight_local.trimlight.FRESH_SYNC_SECONDS", -1):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(LIGHT)
    assert state.attributes[ATTR_EFFECT] == "Rainbow Wave"
    assert state.attributes[ATTR_BRIGHTNESS] == 180

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_ignored_command_is_corrected_by_next_poll(hass: HomeAssistant, controller) -> None:
    controller.sync_tail = bytes.fromhex((Path(__file__).parents[1] / "fixtures" / "sync_reply.hex").read_text().strip())[1:]
    entry = await setup_entry(hass)
    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Rainbow Wave"}, blocking=True)
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "Rainbow Wave"

    with patch("custom_components.trimlight_local.trimlight.FRESH_SYNC_SECONDS", -1):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "CANADA DAY"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def use_fixture(controller, name: str) -> None:
    controller.sync_tail = bytes.fromhex((Path(__file__).parents[1] / "fixtures" / name).read_text().strip())[1:]


async def set_number(hass: HomeAssistant, entity_id: str, value: int) -> None:
    await hass.services.async_call(NUMBER_DOMAIN, "set_value", {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value}, blocking=True)
    await settle(hass)


async def test_effect_controls_follow_and_resend_builtin_effect(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply_builtin.hex")
    entry = await setup_entry(hass)
    assert hass.states.get(SPEED).state == "100"
    assert hass.states.get(LENGTH).state == "30"
    assert hass.states.get(REVERSE).state == STATE_OFF

    await set_number(hass, SPEED, 200)
    assert controller.passthrough[-1] == (Sub.PREVIEW_BUILTIN, bytes((3, 200, 180, 30, 0)))
    await hass.services.async_call(SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: REVERSE}, blocking=True)
    await settle(hass)
    assert controller.passthrough[-1] == (Sub.PREVIEW_BUILTIN, bytes((3, 200, 180, 30, 1)))
    await set_number(hass, LENGTH, 10)
    assert controller.passthrough[-1] == (Sub.PREVIEW_BUILTIN, bytes((3, 200, 180, 10, 1)))
    assert (hass.states.get(SPEED).state, hass.states.get(LENGTH).state, hass.states.get(REVERSE).state) == ("200", "10", STATE_ON)

    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Red Wave", ATTR_BRIGHTNESS: 90}, blocking=True)
    await settle(hass)
    assert controller.passthrough[-1] == (Sub.PREVIEW_BUILTIN, bytes((BUILTIN_EFFECTS.index("Red Wave"), 200, 90, 10, 1)))

    with patch("custom_components.trimlight_local.trimlight.FRESH_SYNC_SECONDS", -1):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert (hass.states.get(SPEED).state, hass.states.get(REVERSE).state) == ("100", STATE_OFF)
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "Rainbow Wave"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_speed_applies_to_saved_pattern_and_length_waits(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    assert hass.states.get(SPEED).state == "127"
    sent = len(controller.passthrough)

    await set_number(hass, LENGTH, 12)
    assert len(controller.passthrough) == sent
    assert hass.states.get(LENGTH).state == "12"

    await set_number(hass, SPEED, 40)
    sub, data = controller.passthrough[-1]
    assert sub == Sub.PREVIEW_CUSTOM
    assert data[:3] == bytes((0, 40, 255))
    assert data[3:18] == bytes.fromhex("05ff000001" "05ffffff01" "0500000001")
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "CANADA DAY"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_settings_change_while_off_sends_nothing(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply_builtin.hex")
    controller.switch_state = 0
    entry = await setup_entry(hass)
    sent = len(controller.passthrough)
    await set_number(hass, SPEED, 10)
    assert len(controller.passthrough) == sent
    assert hass.states.get(SPEED).state == "10"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_show_pattern(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    controller.switch_state = 2
    entry = await setup_entry(hass)
    await hass.services.async_call(
        DOMAIN,
        "show_pattern",
        {ATTR_ENTITY_ID: LIGHT, "mode": "chase_forward", "colors": ["red", "#00FF00", [0, 0, 255]], "dots": [3, 4, 5], "speed": 77, ATTR_BRIGHTNESS: 150},
        blocking=True,
    )
    await settle(hass)
    assert controller.passthrough[-2] == (Sub.SWITCH_STATE, b"\x01")
    sub, data = controller.passthrough[-1]
    assert sub == Sub.PREVIEW_CUSTOM
    assert data == bytes((1, 77, 150)) + bytes.fromhex("03ff000001" "0400ff0001" "050000ff01") + bytes.fromhex("0000000001") * 27
    state = hass.states.get(LIGHT)
    assert (state.state, state.attributes[ATTR_BRIGHTNESS], state.attributes[ATTR_EFFECT]) == (STATE_ON, 150, None)
    assert hass.states.get(SPEED).state == "77"

    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT, ATTR_BRIGHTNESS: 60}, blocking=True)
    await settle(hass)
    sub, data = controller.passthrough[-1]
    assert (sub, data[:3]) == (Sub.PREVIEW_CUSTOM, bytes((1, 77, 60)))

    canada = {"mode": "static", "colors": ["red", "white", "black"], "speed": 127, ATTR_BRIGHTNESS: 255}
    await hass.services.async_call(DOMAIN, "show_pattern", {ATTR_ENTITY_ID: LIGHT, **canada}, blocking=True)
    await settle(hass)
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "CANADA DAY"

    with pytest.raises(INVALID):
        await hass.services.async_call(DOMAIN, "show_pattern", {ATTR_ENTITY_ID: LIGHT, "mode": "static", "colors": ["red", "blue"], "dots": [1, 2, 3]}, blocking=True)
    with pytest.raises(INVALID):
        await hass.services.async_call(DOMAIN, "show_pattern", {ATTR_ENTITY_ID: LIGHT, "mode": "static", "colors": ["notacolour"]}, blocking=True)
    with pytest.raises(INVALID):
        await hass.services.async_call(DOMAIN, "show_pattern", {ATTR_ENTITY_ID: LIGHT, "mode": "sideways", "colors": ["red"]}, blocking=True)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, service: str, data: dict, *, entity_id: str = LIGHT) -> None:
    await hass.services.async_call(DOMAIN, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True)
    await settle(hass)


async def test_save_rename_and_delete_effects(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert (device.sw_version, device.hw_version) == ("1.11", "MCU 0x1042")

    await call(hass, "save_effect", {"name": "Porch RWB", "mode": "chase_forward", "colors": ["red", "white", "blue"], "speed": 90, ATTR_BRIGHTNESS: 200})
    sub, data = controller.passthrough[-2]
    assert (sub, data[0], data[1:10], data[26:32]) == (Sub.ADD_LIBRARY_EFFECT, 18, b"Porch RWB", bytes((2, 1, 90, 200, 0, 0)))
    assert controller.passthrough[-1][0] == Sub.SYNC
    assert "Porch RWB" in hass.states.get(LIGHT).attributes["effect_list"]

    await call(hass, "save_effect", {"name": "Porch RWB", "mode": "static", "colors": ["red"]})
    assert controller.passthrough[-2][0] == Sub.MODIFY_LIBRARY_EFFECT

    await call(hass, "rename_saved_effect", {ATTR_EFFECT: "Porch RWB", "new_name": "Porch flag"})
    effects = hass.states.get(LIGHT).attributes["effect_list"]
    assert "Porch flag" in effects and "Porch RWB" not in effects

    await call(hass, "delete_saved_effect", {ATTR_EFFECT: "Porch flag"})
    assert controller.passthrough[-2] == (Sub.REMOVE_LIBRARY_EFFECT, b"\x12")
    assert "Porch flag" not in hass.states.get(LIGHT).attributes["effect_list"]

    await call(hass, "save_effect", {"name": "Canada copy"})
    sub, data = controller.passthrough[-2]
    assert (sub, data[0], data[26:30]) == (Sub.ADD_LIBRARY_EFFECT, 18, bytes((2, 0, 127, 255)))

    with pytest.raises(ServiceValidationError, match="calendar schedule 1"):
        await call(hass, "delete_saved_effect", {ATTR_EFFECT: "Simple"})
    with pytest.raises(ServiceValidationError, match="combined effect"):
        await call(hass, "delete_saved_effect", {ATTR_EFFECT: "Christmas Fireworks"})
    with pytest.raises(INVALID):
        await call(hass, "save_effect", {"name": "x" * 26})

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_ignored_change_is_reported(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    controller.switch_state = 2
    controller.writes_need_manual = True
    entry = await setup_entry(hass)
    with patch("custom_components.trimlight_local.coordinator.WRITE_CHECK_DELAY", 0), pytest.raises(HomeAssistantError, match="without the change"):
        await call(hass, "save_effect", {"name": "Lost", "mode": "static", "colors": ["red"]})
    assert controller.ignored_writes == 3
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_schedules_calendar(hass: HomeAssistant, controller) -> None:
    await hass.config.async_set_time_zone("America/Chicago")
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    response = await hass.services.async_call(
        "calendar", "get_events", {ATTR_ENTITY_ID: CALENDAR, "start_date_time": "2026-12-26 12:00:00", "end_date_time": "2026-12-29 12:00:00"}, blocking=True, return_response=True
    )
    events = [(e["start"], e["end"], e["summary"]) for e in response[CALENDAR]["events"]]
    assert events == [
        ("2026-12-26T16:22:00-06:00", "2026-12-27T07:06:00-06:00", "CHRISTMAS"),
        ("2026-12-27T16:22:00-06:00", "2026-12-28T07:06:00-06:00", "CHRISTMAS"),
        ("2026-12-28T16:22:00-06:00", "2026-12-29T07:06:00-06:00", "Post Christmas"),
    ]

    await call(hass, "set_schedule", {ATTR_EFFECT: "HALLOWEEN", "start_date": "2027-03-01", "end_date": "2027-03-03", "on_time": "18:00", "off_time": "23:00"}, entity_id=CALENDAR)
    assert controller.passthrough[-2] == (Sub.ADD_CALENDAR_SCHEDULE, bytes.fromhex("09070301030312001700"))
    await call(hass, "set_schedule", {"schedule_id": 9, ATTR_EFFECT: "Combined effect", "start_date": "2027-03-01", "on_time": "18:30", "off_time": "23:00"}, entity_id=CALENDAR)
    assert controller.passthrough[-2] == (Sub.MODIFY_CALENDAR_SCHEDULE, bytes.fromhex("09c803010301121e1700"))
    with pytest.raises(ServiceValidationError, match="Schedule 6"):
        await call(hass, "set_schedule", {ATTR_EFFECT: "HALLOWEEN", "start_date": "2026-12-24", "end_date": "2026-12-25", "on_time": "18:00", "off_time": "23:00"}, entity_id=CALENDAR)
    with pytest.raises(ServiceValidationError, match="saved effect"):
        await call(hass, "set_schedule", {ATTR_EFFECT: "Rainbow Wave", "start_date": "2027-04-01", "on_time": "18:00", "off_time": "23:00"}, entity_id=CALENDAR)
    await call(hass, "delete_schedule", {"schedule_id": 9}, entity_id=CALENDAR)
    assert controller.passthrough[-2] == (Sub.DELETE_CALENDAR_SCHEDULE, b"\x09")
    assert all(s.id != 9 for s in entry.runtime_data.data.calendar)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_change_read_back_through_controller_silence(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    controller.silent_after = {Sub.ADD_CALENDAR_SCHEDULE: 1.0}
    entry = await setup_entry(hass)
    entry.runtime_data.client._reply_timeout = 0.3
    with patch("custom_components.trimlight_local.coordinator.WRITE_RETRY_DELAY", 0.1):
        await call(hass, "set_schedule", {ATTR_EFFECT: "Simple", "start_date": "2027-03-15", "on_time": "18:00", "off_time": "19:00"}, entity_id=CALENDAR)
    assert controller.dropped > 0
    assert any(s.id == 9 for s in entry.runtime_data.data.calendar)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_daily_schedule_entities(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    assert hass.states.get(DAILY_1).state == STATE_OFF
    assert hass.states.get(f"{SELECT_DOMAIN}.porch_daily_schedule_1_effect").state == "Simple"
    assert hass.states.get(f"{SELECT_DOMAIN}.porch_daily_schedule_1_repeat").state == "today_only"
    assert hass.states.get(f"{TIME_DOMAIN}.porch_daily_schedule_1_on").state == "16:25:00"

    await hass.services.async_call(SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: DAILY_1}, blocking=True)
    await settle(hass)
    sub, data = controller.passthrough[-2]
    assert (sub, data[:8]) == (Sub.MODIFY_DAILY_SCHEDULE, bytes((0, 1, 10, 0, 16, 25, 0, 0)))
    assert hass.states.get(DAILY_1).state == STATE_ON

    await hass.services.async_call(SELECT_DOMAIN, "select_option", {ATTR_ENTITY_ID: f"{SELECT_DOMAIN}.porch_daily_schedule_1_effect", ATTR_OPTION: "HALLOWEEN"}, blocking=True)
    await hass.services.async_call(SELECT_DOMAIN, "select_option", {ATTR_ENTITY_ID: f"{SELECT_DOMAIN}.porch_daily_schedule_1_repeat", ATTR_OPTION: "every_day"}, blocking=True)
    await hass.services.async_call(TIME_DOMAIN, "set_value", {ATTR_ENTITY_ID: f"{TIME_DOMAIN}.porch_daily_schedule_1_on", "time": "17:45:00"}, blocking=True)
    await settle(hass)
    assert controller.passthrough[-2][1][:8] == bytes((0, 1, 7, 1, 17, 45, 0, 0))

    await hass.services.async_call(TIME_DOMAIN, "set_value", {ATTR_ENTITY_ID: f"{TIME_DOMAIN}.porch_daily_schedule_2_on", "time": "20:00:00"}, blocking=True)
    await hass.services.async_call(TIME_DOMAIN, "set_value", {ATTR_ENTITY_ID: f"{TIME_DOMAIN}.porch_daily_schedule_2_off", "time": "21:00:00"}, blocking=True)
    with pytest.raises(ServiceValidationError, match="overlap"):
        await hass.services.async_call(SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: "switch.porch_daily_schedule_2"}, blocking=True)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_overlays_and_combined_effect(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    assert hass.states.get(LIGHT).attributes["combined_effect"] == ["Christmas Fireworks"]

    await call(hass, "set_overlay", {ATTR_EFFECT: "HALLOWEEN", "overlay": "lightning"})
    await call(hass, "set_overlay", {ATTR_EFFECT: "CHRISTMAS", "overlay": "snow"})
    assert controller.passthrough[-2] == (Sub.UPDATE_OVERLAY_EFFECT, bytes.fromhex("00070109"))
    await call(hass, "set_overlay", {ATTR_EFFECT: "HALLOWEEN", "overlay": "none"})
    assert controller.passthrough[-2] == (Sub.UPDATE_OVERLAY_EFFECT, bytes.fromhex("0109"))
    assert hass.states.get(LIGHT).attributes["overlays"] == {"CHRISTMAS": "snow"}

    await call(hass, "set_combined_effect", {"effects": ["CHRISTMAS", "HALLOWEEN"], "interval": 4})
    assert controller.passthrough[-2] == (Sub.UPDATE_COMBINED_EFFECT, bytes.fromhex("02090704"))
    state = hass.states.get(LIGHT)
    assert (state.attributes["combined_effect"], state.attributes["combined_effect_interval"]) == (["CHRISTMAS", "HALLOWEEN"], 4)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_connection_is_short_and_waits_for_the_phone_app(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    quick = partial(TrimlightClient, reply_timeout=0.3)
    with (
        patch("custom_components.trimlight_local.TrimlightClient", quick),
        patch("custom_components.trimlight_local.trimlight.BUSY_RETRY_SECONDS", 0.05),
        patch("custom_components.trimlight_local.trimlight.LINGER_SECONDS", 0.1),
    ):
        entry = await setup_entry(hass)
        await asyncio.sleep(0.2)
        assert not controller.serving

        _, phone = await asyncio.open_connection("127.0.0.1", 8586)
        await asyncio.sleep(0.05)
        syncs = sum(sub == Sub.SYNC for sub, _ in controller.passthrough)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert sum(sub == Sub.SYNC for sub, _ in controller.passthrough) == syncs
        state = hass.states.get(LIGHT)
        assert (state.state, state.attributes["controller_busy"]) == (STATE_ON, True)

        hass.loop.call_later(0.5, phone.close)
        await hass.services.async_call(LIGHT_DOMAIN, "turn_off", {ATTR_ENTITY_ID: LIGHT}, blocking=True)
        await settle(hass)
        assert controller.switch_state == 0
        assert hass.states.get(LIGHT).attributes["controller_busy"] is False

        await asyncio.sleep(0.2)
        assert not controller.serving
        _, phone = await asyncio.open_connection("127.0.0.1", 8586)
        await asyncio.sleep(0.05)
        with patch("custom_components.trimlight_local.trimlight.BUSY_WAIT_SECONDS", 0.5), pytest.raises(HomeAssistantError, match="Trimlight app"):
            await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT}, blocking=True)
        assert controller.switch_state == 0
        phone.close()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_config_flow_reports_a_busy_controller(hass: HomeAssistant, controller) -> None:
    _, phone = await asyncio.open_connection("127.0.0.1", 8586)
    await asyncio.sleep(0.05)
    with (
        patch("custom_components.trimlight_local.config_flow.discover", return_value=[]),
        patch("custom_components.trimlight_local.config_flow.TrimlightClient", partial(TrimlightClient, reply_timeout=0.3)),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1"})
    assert result["errors"] == {"base": "busy"}
    phone.close()


async def test_page_data_and_file(hass: HomeAssistant, controller, hass_ws_client, hass_client) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    await call(hass, "set_overlay", {ATTR_EFFECT: "NEW YEAR", "overlay": "snow"})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/state", "entity_id": LIGHT})
    result = (await ws.receive_json())["result"]
    assert result["entities"] == {"light": LIGHT, "mode": MODE, "calendar": CALENDAR}
    assert (result["switch_state"], result["combined_name"], result["repeats"]) == ("manual", "Combined effect", ["today_only", "every_day", "weekdays", "weekend"])
    assert result["calendar"][5] == {"id": 6, "effect": "CHRISTMAS", "start": [12, 1], "end": [12, 27], "on": "16:22", "off": "07:06", "on_sun": None, "off_sun": None, "holiday": None}
    assert result["calendar"][3]["effect"] == "Combined effect"
    assert result["daily"][0] == {"enabled": False, "effect": "Simple", "repeat": "today_only", "on": "16:25", "off": "00:00", "on_sun": None, "off_sun": None}
    assert set(result["sun"]) == {"sunrise", "sunset", "location_ok"} and len(result["sun"]["sunset"]) == 5
    assert result["sun"]["location_ok"] is True
    # Home Assistant's default location (Amsterdam) with a US time zone, as in a sandbox whose location was never set.
    hass.config.latitude, hass.config.longitude = 52.37, 4.89
    await ws.send_json({"id": 2, "type": f"{DOMAIN}/state", "entity_id": LIGHT})
    assert (await ws.receive_json())["result"]["sun"]["location_ok"] is False
    assert result["combined"] == {"effects": ["Christmas Fireworks"], "interval": 5}
    assert len(result["modes"]) == 21 and result["modes"][1] == {"key": "chase_forward", "name": "Chase forward"}
    assert result["limits"] == {"colors": 30, "dots": 90, "name": 25}
    effects = {e["name"]: e for e in result["effects"]}
    assert len(effects) == 17
    assert effects["HALLOWEEN"] == {
        "id": 7,
        "name": "HALLOWEEN",
        "stored_name": "HALLOWEEN",
        "kind": "pattern",
        "mode": "chase_forward",
        "speed": 1,
        "brightness": 255,
        "colors": [{"color": "#ff2600", "dots": 5}, {"color": "#7700ff", "dots": 5}, {"color": "#7800ff", "dots": 5}],
        "overlay": None,
        "used_by": ["calendar schedule 5", "calendar schedule 7"],
    }
    assert (effects["NEW YEAR"]["overlay"], len(effects["NEW YEAR"]["colors"])) == ("snow", 6)
    assert result["current"] == {"kind": "pattern", "mode": "static", "speed": 127, "brightness": 255, "colors": [{"color": "#ff0000", "dots": 5}, {"color": "#ffffff", "dots": 5}, {"color": "#000000", "dots": 5}]}

    await ws.send_json({"id": 3, "type": f"{DOMAIN}/state", "entity_id": "light.not_trimlight"})
    assert (await ws.receive_json())["error"]["code"] == "not_found"

    response = await (await hass_client()).get("/trimlight_local/trimlight-ui.js")
    assert response.status == 200
    text = await response.text()
    assert '"trimlight-panel": TrimlightPanel' in text and '"trimlight-pattern-card": TrimlightPatternCard' in text

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_sidebar_page_and_card_are_registered(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    hass.config.components.add("frontend")
    with patch("custom_components.trimlight_local.add_extra_js_url") as add_js, patch("custom_components.trimlight_local.panel_custom.async_register_panel") as register:
        entry = await setup_entry(hass)
    [(_, module_url)] = [c.args for c in add_js.call_args_list]
    assert module_url.startswith("/trimlight_local/trimlight-ui.js?v=")
    kwargs = register.call_args.kwargs
    assert (kwargs["frontend_url_path"], kwargs["webcomponent_name"], kwargs["sidebar_title"], kwargs["module_url"]) == ("trimlight", "trimlight-panel", "Trimlight", module_url)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_daily_schedule_saved_in_one_write(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    writes = len([s for s, _ in controller.passthrough if s == Sub.MODIFY_DAILY_SCHEDULE])
    await call(hass, "set_daily_schedule", {"schedule": 1, "enabled": True, ATTR_EFFECT: "HALLOWEEN", "repeat": "every_day", "on_time": "17:45", "off_time": "00:00"})
    daily_writes = [d for s, d in controller.passthrough if s == Sub.MODIFY_DAILY_SCHEDULE]
    assert len(daily_writes) == writes + 1
    assert daily_writes[-1][:8] == bytes((0, 1, 7, 1, 17, 45, 0, 0))
    assert hass.states.get(DAILY_1).state == STATE_ON
    await call(hass, "set_daily_schedule", {"schedule": 2, ATTR_EFFECT: "Combined effect"})
    assert controller.passthrough[-2][1][:3] == bytes((1, 0, 200))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_unlit_colour_in_pattern(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    await call(hass, "show_pattern", {"mode": "static", "colors": ["red", "none", None], "dots": [2, 3, 4]})
    sub, data = controller.passthrough[-1]
    assert (sub, data[3:18]) == (Sub.PREVIEW_CUSTOM, bytes.fromhex("02ff000001" "0300000000" "0400000000"))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def local(*args: int) -> datetime:
    return datetime(*args, tzinfo=dt_util.get_default_time_zone())


async def test_schedule_follows_sunset(hass: HomeAssistant, controller, hass_ws_client) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    before_sunset = SunTime("sunset", -15)
    march = {"start_date": "2027-03-10", "end_date": "2027-03-20"}
    with patch("custom_components.trimlight_local.coordinator.now", return_value=local(2027, 3, 12, 10, 0)):
        await call(hass, "set_schedule", {ATTR_EFFECT: "HALLOWEEN", **march, "on_sun": "sunset", "on_offset": -15, "off_time": "23:00"}, entity_id=CALENDAR)
    on = before_sunset.on_day(hass, date(2027, 3, 12))
    assert controller.passthrough[-2] == (Sub.ADD_CALENDAR_SCHEDULE, bytes((9, 7, 3, 10, 3, 20, on.hour, on.minute, 23, 0)))
    assert coordinator.sun.calendar[9].on == before_sunset

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/state", "entity_id": LIGHT})
    schedule = next(s for s in (await ws.receive_json())["result"]["calendar"] if s["id"] == 9)
    assert (schedule["on_sun"], schedule["off_sun"]) == ({"event": "sunset", "offset": -15}, None)

    response = await hass.services.async_call(
        "calendar", "get_events", {ATTR_ENTITY_ID: CALENDAR, "start_date_time": "2027-03-15 00:00:00", "end_date_time": "2027-03-18 00:00:00"}, blocking=True, return_response=True
    )
    starts = [datetime.fromisoformat(e["start"]) for e in response[CALENDAR]["events"]]
    assert [s.time() for s in starts] == [before_sunset.on_day(hass, date(2027, 3, d)) for d in (15, 16, 17)]

    with patch("custom_components.trimlight_local.coordinator.now", return_value=local(2027, 3, 19, 12, 0)):
        await coordinator.async_refresh_sun_times()
    on_19th = before_sunset.on_day(hass, date(2027, 3, 19))
    assert on_19th != on
    assert controller.passthrough[-2] == (Sub.MODIFY_CALENDAR_SCHEDULE, bytes((9, 7, 3, 10, 3, 20, on_19th.hour, on_19th.minute, 23, 0)))

    stored = SunRules.for_entry(hass, entry.entry_id)
    await stored.async_load()
    assert (stored.calendar[9].on, stored.calendar[9].written) == (before_sunset, (on_19th, time(23, 0)))

    # The phone app changes the time; from then on its clock time stays.
    controller._apply_write(Sub.MODIFY_CALENDAR_SCHEDULE, bytes((9, 7, 3, 10, 3, 20, 18, 0, 23, 0)))
    with patch("custom_components.trimlight_local.trimlight.FRESH_SYNC_SECONDS", -1):
        await coordinator.async_refresh()
    writes = len(controller.passthrough)
    with patch("custom_components.trimlight_local.coordinator.now", return_value=local(2027, 3, 20, 12, 0)):
        await coordinator.async_refresh_sun_times()
    assert 9 not in coordinator.sun.calendar
    assert all(sub == Sub.SYNC for sub, _ in controller.passthrough[writes:])

    await call(hass, "delete_schedule", {"schedule_id": 9}, entity_id=CALENDAR)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_holiday_dates(hass: HomeAssistant) -> None:
    dates = HolidayDates()
    await dates.async_load(hass, date(2026, 11, 9))
    assert [dates.date(k, 2026) for k in ("easter", "thanksgiving", "memorial_day", "mothers_day", "election_day", "orthodox_easter", "diwali", "eid_al_fitr")] == [
        date(2026, 4, 5), date(2026, 11, 26), date(2026, 5, 25), date(2026, 5, 10), date(2026, 11, 3), date(2026, 4, 12), date(2026, 11, 8), date(2026, 3, 20)
    ]
    # The library also lists the days leading up to Dussehra under longer names, which must not count as Dussehra itself.
    assert dates.date("dussehra", 2027) == date(2027, 10, 9)
    # Hanukkah 2027 runs into January 2028, so 2028's own Hanukkah is the one that starts in December.
    assert (dates.date("hanukkah", 2027), dates.date("hanukkah", 2028), dates.date("lunar_new_year", 2028)) == (date(2027, 12, 25), date(2028, 12, 13), date(2028, 1, 26))
    eight_nights = HolidayRule("hanukkah", -1, 6)
    assert dates.windows(eight_nights, date(2027, 12, 30), 1) == [(date(2027, 12, 24), date(2027, 12, 31))]
    assert dates.windows(eight_nights, date(2028, 1, 1), 1) == [(date(2028, 12, 12), date(2028, 12, 19))]
    assert dates.windows(HolidayRule("diwali"), date(2060, 1, 1)) == []


async def test_holiday_schedules_take_turns(hass: HomeAssistant, controller, hass_ws_client) -> None:
    await hass.config.async_set_time_zone("America/Chicago")
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    times = {"on_time": "16:27", "off_time": "06:51"}
    ws = await hass_ws_client(hass)
    request_id = 0

    def at(*when: int):
        moment = local(*when)
        return patch("custom_components.trimlight_local.coordinator.now", return_value=moment), patch("custom_components.trimlight_local.websocket.now", return_value=moment)

    async def refresh(*when: int) -> None:
        first, second = at(*when)
        with first, second:
            await coordinator.async_refresh_holiday_dates()

    async def add(*when: int, **data) -> None:
        first, second = at(*when)
        with first, second:
            await call(hass, "set_schedule", {**times, **data}, entity_id=CALENDAR)

    async def state(*when: int) -> dict:
        nonlocal request_id
        request_id += 1
        first, second = at(*when)
        with first, second:
            await ws.send_json({"id": request_id, "type": f"{DOMAIN}/state", "entity_id": LIGHT})
            return (await ws.receive_json())["result"]

    def on_controller(holiday: str) -> tuple | None:
        ids = [i for i, r in coordinator.holidays.calendar.items() if r.holiday == holiday]
        return next(((s.id, s.start, s.end) for s in coordinator.data.calendar if s.id in ids), None)

    def waiting(holiday: str) -> str | None:
        return next((k for k, w in coordinator.holidays.waiting.items() if w.rule.holiday == holiday), None)

    # Schedule 10 is Thanksgiving week, Nov 24-30 2026: from two days before Thanksgiving to four days after.
    await add(2026, 11, 10, 10, 0, schedule_id=10, effect="Simple", holiday="thanksgiving", holiday_start_offset=-2, holiday_end_offset=4)
    assert controller.passthrough[-2] == (Sub.MODIFY_CALENDAR_SCHEDULE, bytes((10, 10, 11, 24, 11, 30, 16, 27, 6, 51)))
    assert coordinator.holidays.calendar[10] == HolidayRule("thanksgiving", -2, 4, ((11, 24), (11, 30)))

    # Diwali 2027 is Oct 29, inside the Halloween schedule 7 (Oct 26-30), which keeps its nights. The next Diwali is
    # Oct 17, 2028, but Oct 17 comes round in 2027 first, so the schedule waits off the controller until Oct 18, 2027.
    writes = len(controller.passthrough)
    await add(2026, 11, 10, 10, 0, effect="Diwali", holiday="diwali")
    assert len(controller.passthrough) == writes
    result = await state(2026, 11, 10, 10, 0)
    diwali = next(w for w in result["waiting"] if w["holiday"]["key"] == "diwali")
    assert (diwali["effect"], diwali["next"], diwali["from"], diwali["holiday"]["skips"]) == ("Diwali", ["2028-10-17", "2028-10-17"], "2027-10-18", [{"year": 2027, "to": "schedule 7 (HALLOWEEN)"}])
    assert next(s for s in result["calendar"] if s["id"] == 10)["holiday"] == {"key": "thanksgiving", "start_offset": -2, "end_offset": 4, "problem": None, "skips": []}

    # Guru Nanak Jayanti 2026 is Nov 24, in Thanksgiving week: the US holiday wins, so it skips to Nov 14, 2027.
    await add(2026, 11, 10, 10, 0, effect="Nigeria", holiday="guru_nanak_jayanti")
    assert waiting("guru_nanak_jayanti") is not None and on_controller("guru_nanak_jayanti") is None
    await refresh(2026, 11, 15, 12, 0)
    assert on_controller("guru_nanak_jayanti")[1:] == ((11, 14), (11, 14))
    assert on_controller("thanksgiving")[1:] == ((11, 24), (11, 30))

    # The day after Thanksgiving week, it moves on to Nov 23-29, 2027.
    await refresh(2026, 12, 1, 12, 0)
    assert on_controller("thanksgiving") == (10, (11, 23), (11, 29))

    # The calendar shows each year's own nights, and nothing for a skipped year.
    def events(start: str, end: str):
        return hass.services.async_call("calendar", "get_events", {ATTR_ENTITY_ID: CALENDAR, "start_date_time": start, "end_date_time": end}, blocking=True, return_response=True)

    response = await events("2028-11-19 12:00:00", "2028-11-22 12:00:00")
    assert [(e["start"], e["description"]) for e in response[CALENDAR]["events"]] == [("2028-11-21T16:27:00-06:00", "Trimlight schedule 10, following Thanksgiving")]
    response = await events("2027-10-28 12:00:00", "2027-10-30 12:00:00")
    assert {e["summary"] for e in response[CALENDAR]["events"]} == {"HALLOWEEN"}
    response = await events("2028-10-17 12:00:00", "2028-10-18 12:00:00")
    assert [e["summary"] for e in response[CALENDAR]["events"]] == ["Diwali"]

    # Holi 2027 is Mar 22. Easter week (Palm Sunday to Easter, Mar 21-28) is a US holiday, so it takes those nights:
    # Holi comes off the controller, skips 2027, and goes back on for Mar 11, 2028 once Mar 11, 2027 has passed.
    await add(2027, 1, 10, 10, 0, effect="HALLOWEEN", holiday="holi")
    holi_id = on_controller("holi")[0]
    await add(2027, 1, 10, 10, 0, effect="CHRISTMAS", holiday="easter", holiday_start_offset=-7)
    assert (Sub.DELETE_CALENDAR_SCHEDULE, bytes((holi_id,))) in controller.passthrough
    assert on_controller("easter")[1:] == ((3, 21), (3, 28))
    assert on_controller("holi") is None
    result = await state(2027, 1, 10, 10, 0)
    holi = next(w for w in result["waiting"] if w["holiday"]["key"] == "holi")
    assert (holi["next"], holi["from"], holi["holiday"]["skips"][0]["year"]) == (["2028-03-11", "2028-03-11"], "2027-03-12", 2027)
    await refresh(2027, 3, 12, 12, 0)
    assert on_controller("holi")[1:] == ((3, 11), (3, 11))

    # Diwali goes on the controller on Oct 18, 2027, for Oct 17, 2028.
    await refresh(2027, 10, 18, 12, 0)
    diwali_id = on_controller("diwali")[0]
    assert on_controller("diwali")[1:] == ((10, 17), (10, 17))
    # Diwali 2029 is Nov 5, later in the year: Oct 17 can't come round before Nov 5, 2028 has passed, so it keeps it till then.
    await refresh(2028, 10, 18, 12, 0)
    assert on_controller("diwali") == (diwali_id, (10, 17), (10, 17))
    await refresh(2028, 11, 6, 12, 0)
    assert on_controller("diwali") == (diwali_id, (11, 5), (11, 5))

    # Dates changed in the phone app end a schedule's rule.
    controller._apply_write(Sub.MODIFY_CALENDAR_SCHEDULE, bytes((10, 10, 11, 20, 11, 29, 16, 27, 6, 51)))
    with patch("custom_components.trimlight_local.trimlight.FRESH_SYNC_SECONDS", -1):
        await coordinator.async_refresh()
    await refresh(2028, 11, 7, 12, 0)
    assert 10 not in coordinator.holidays.calendar

    # A waiting schedule can be deleted from the page. The night before Diwali 2027 is in Halloween too, so this one waits.
    await add(2026, 11, 10, 10, 0, effect="NEW YEAR", holiday="diwali", holiday_start_offset=-1, holiday_end_offset=-1)
    key = next(k for k, w in coordinator.holidays.waiting.items() if w.rule.start_offset == -1)
    request_id += 1
    await ws.send_json({"id": request_id, "type": f"{DOMAIN}/waiting_delete", "entity_id": LIGHT, "waiting_id": key})
    assert (await ws.receive_json())["success"]
    assert key not in coordinator.holidays.waiting

    for data, error in (
        ({"holiday": "groundhog_day"}, INVALID),
        ({"holiday": "easter", "start_date": "2027-04-01"}, INVALID),
        ({"holiday": "easter", "holiday_start_offset": 2, "holiday_end_offset": 0}, ServiceValidationError),
        ({}, ServiceValidationError),
    ):
        with pytest.raises(error):
            await add(2028, 11, 7, 12, 0, effect="Simple", **data)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_dropped_change_is_sent_again(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    controller.lost_writes = 1
    with patch("custom_components.trimlight_local.coordinator.WRITE_CHECK_DELAY", 0):
        await call(hass, "set_overlay", {ATTR_EFFECT: "CHRISTMAS", "overlay": "snow"})
    assert [sub for sub, _ in controller.passthrough].count(Sub.UPDATE_OVERLAY_EFFECT) == 2
    assert hass.states.get(LIGHT).attributes["overlays"] == {"CHRISTMAS": "snow"}
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_daily_schedule_follows_sun_until_given_a_clock_time(hass: HomeAssistant, controller) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    with patch("custom_components.trimlight_local.coordinator.now", return_value=local(2027, 3, 12, 10, 0)):
        await call(hass, "set_daily_schedule", {"schedule": 2, "enabled": True, "repeat": "every_day", "on_sun": "sunset", "off_sun": "sunrise", "off_offset": 30})
    on = SunTime("sunset").on_day(hass, date(2027, 3, 12))
    off = SunTime("sunrise", 30).on_day(hass, date(2027, 3, 13))
    assert controller.passthrough[-2][1][:8] == bytes((1, 1, 1, 1, on.hour, on.minute, off.hour, off.minute))
    assert (coordinator.sun.daily[1].on, coordinator.sun.daily[1].off) == (SunTime("sunset"), SunTime("sunrise", 30))

    await hass.services.async_call(TIME_DOMAIN, "set_value", {ATTR_ENTITY_ID: f"{TIME_DOMAIN}.porch_daily_schedule_2_on", "time": "18:00:00"}, blocking=True)
    await settle(hass)
    assert (coordinator.sun.daily[1].on, coordinator.sun.daily[1].off) == (None, SunTime("sunrise", 30))
    assert controller.passthrough[-2][1][4:6] == bytes((18, 0))

    with pytest.raises(INVALID):
        await call(hass, "set_daily_schedule", {"schedule": 2, "on_time": "18:00", "on_sun": "sunset"})
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_show_effect_music_and_favourites(hass: HomeAssistant, controller, hass_ws_client) -> None:
    use_fixture(controller, "sync_reply.hex")
    entry = await setup_entry(hass)
    await call(hass, "show_effect", {ATTR_EFFECT: "Rainbow Wave", "speed": 40, "length": 12, "reverse": True, ATTR_BRIGHTNESS: 200})
    assert controller.passthrough[-1] == (Sub.PREVIEW_BUILTIN, bytes((3, 40, 200, 12, 1)))
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "Rainbow Wave"
    assert (hass.states.get(SPEED).state, hass.states.get(LENGTH).state, hass.states.get(REVERSE).state) == ("40", "12", STATE_ON)

    await call(hass, "start_music", {ATTR_EFFECT: "Full Color Rhythm Spectrum"})
    assert controller.passthrough[-1] == (Sub.MUSIC_DATA, bytes((2, 0, 255, 0, 0, 70, 13)) + bytes(16))
    await call(hass, "start_music", {ATTR_EFFECT: "Single Color Pulse", "color": "blue", "length": 100, "sensitivity": 5})
    assert controller.passthrough[-1] == (Sub.MUSIC_DATA, bytes((2, 7, 0, 0, 255, 100, 5)) + bytes(16))
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] is None

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/state", "entity_id": LIGHT})
    result = (await ws.receive_json())["result"]
    assert result["music"] == {"effects": result["music"]["effects"], "source": "aux", "color": "#ff0000", "length": 70, "sensitivity": 13}
    assert (len(result["music"]["effects"]), result["builtins"][0], len(result["builtins"]), result["favourites"]) == (18, "Rainbow Gradual Chase", 180, [])
    assert result["current"]["kind"] == "music" and result["current"]["effect"] == "Single Color Pulse"
    assert result["device"] == {
        "name": "Porch",
        "host": "127.0.0.1",
        "firmware": "1.11",
        "mcu_version": "0x1042",
        "ic_type": "UCS1903",
        "channel_order": "RGB",
        "ports": [{"start": 1, "end": 200, "enabled": True}] + [{"start": 1, "end": 600, "enabled": True}] * 3,
        "clock": "2026-11-08T19:50",
        "time_sync": "auto",
        "ic_type_value": 0,
        "channel_order_value": 0,
        "ic_types": [{"value": 0, "name": "UCS1903"}, {"value": 1, "name": "DMX512"}, {"value": 2, "name": "UCS5603"}],
        "channel_orders": [{"value": i, "name": name} for i, name in enumerate(("RGB", "RBG", "GRB", "GBR", "BRG", "BGR"))],
        "led_session": None,
        "max_port_dot": 2700,
    }

    await ws.send_json({"id": 2, "type": f"{DOMAIN}/favourites", "entity_id": LIGHT, "favourites": [3, 0, 3]})
    assert (await ws.receive_json())["result"] == {"favourites": [3, 0]}
    await ws.send_json({"id": 3, "type": f"{DOMAIN}/favourites", "entity_id": LIGHT, "favourites": [180]})
    assert not (await ws.receive_json())["success"]
    await ws.send_json({"id": 4, "type": f"{DOMAIN}/state", "entity_id": LIGHT})
    state = (await ws.receive_json())["result"]
    assert (state["favourites"], state["roofline"]) == ([3, 0], "house")
    await ws.send_json({"id": 5, "type": f"{DOMAIN}/roofline", "entity_id": LIGHT, "roofline": "two_gables"})
    assert (await ws.receive_json())["result"] == {"roofline": "two_gables"}
    await ws.send_json({"id": 6, "type": f"{DOMAIN}/roofline", "entity_id": LIGHT, "roofline": "castle"})
    assert not (await ws.receive_json())["success"]
    stored = await entry.runtime_data._page_store.async_load()
    assert stored == {"favourites": [3, 0], "roofline": "two_gables"}

    with pytest.raises(INVALID):
        await call(hass, "show_effect", {ATTR_EFFECT: "Not an effect"})
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_led_test_session_keep_undo_and_timeout(hass: HomeAssistant, controller, hass_ws_client) -> None:
    use_fixture(controller, "sync_reply.hex")
    controller.switch_state = 2
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    ws = await hass_ws_client(hass)

    async def send(msg_id: int, **msg) -> dict:
        await ws.send_json({"id": msg_id, "entity_id": LIGHT, **msg})
        return await ws.receive_json()

    assert (await send(1, type=f"{DOMAIN}/led_test", show="red"))["success"]
    assert controller.passthrough[-2:] == [(Sub.SWITCH_STATE, b"\x01"), (Sub.PREVIEW_COLOR, bytes((255, 0, 0)))]
    assert (await send(2, type=f"{DOMAIN}/led_setup", channel_order=2))["success"]
    assert controller.passthrough[-2] == (Sub.CHANNEL_ORDER, b"\x02")
    state = (await send(4, type=f"{DOMAIN}/state"))["result"]["device"]
    assert (state["channel_order"], state["led_session"]["changed"]) == ("GRB", True)
    assert (await send(5, type=f"{DOMAIN}/led_session_end", keep=False))["success"]
    sent = [sub for sub, _ in controller.passthrough]
    assert controller.passthrough[sent.index(Sub.CHANNEL_ORDER, sent.index(Sub.CHANNEL_ORDER) + 1)] == (Sub.CHANNEL_ORDER, b"\x00")
    assert controller.passthrough[-1] == (Sub.SWITCH_STATE, b"\x02")
    sub, data = controller.passthrough[-2]
    assert (sub, data[3:18]) == (Sub.PREVIEW_CUSTOM, bytes.fromhex("05ff000001" "05ffffff01" "0500000001"))
    assert coordinator.led_session is None

    assert (await send(6, type=f"{DOMAIN}/led_setup", ic_type=2))["success"]
    assert (await send(7, type=f"{DOMAIN}/led_session_end", keep=True))["success"]
    assert coordinator.data.ic_type == 2

    with patch("custom_components.trimlight_local.coordinator.LED_TEST_SECONDS", 0.2):
        assert (await send(8, type=f"{DOMAIN}/led_setup", ic_type=1))["success"]
        assert coordinator.data.ic_type == 1
        await asyncio.sleep(0.4)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.led_session is None
    assert coordinator.data.ic_type == 2

    assert not (await send(9, type=f"{DOMAIN}/led_setup", ic_type=7))["success"]

    ports = [{"start": 1, "end": 200, "enabled": True}, {"start": 1, "end": 450, "enabled": True}, {"start": 1, "end": 600, "enabled": False}, {"start": 1, "end": 600, "enabled": True}]
    assert (await send(10, type=f"{DOMAIN}/led_setup", ports=ports))["success"]
    port_writes = [data for sub, data in controller.passthrough if sub == Sub.PORT_RANGES]
    assert port_writes == [bytes.fromhex("01000101c201"), bytes.fromhex("020001025800")]
    device = (await send(11, type=f"{DOMAIN}/state"))["result"]["device"]
    assert [(p["end"], p["enabled"]) for p in device["ports"]] == [(200, True), (450, True), (600, False), (600, True)]
    assert (await send(12, type=f"{DOMAIN}/led_session_end", keep=False))["success"]
    device = (await send(13, type=f"{DOMAIN}/state"))["result"]["device"]
    assert [(p["end"], p["enabled"]) for p in device["ports"]] == [(200, True), (600, True), (600, True), (600, True)]
    reply = await send(14, type=f"{DOMAIN}/led_setup", ports=[{"start": 300, "end": 200, "enabled": True}] + ports[1:])
    assert not reply["success"] and "Port 1" in reply["error"]["message"]
    assert coordinator.led_session is None

    assert (await send(15, type=f"{DOMAIN}/rename", name="  Roofline  "))["success"]
    assert controller.name == "Roofline"
    assert not (await send(16, type=f"{DOMAIN}/rename", name="Dach/Licht"))["success"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_retries_when_unreachable(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", unique_id="127.0.0.1", data={CONF_HOST: "127.0.0.1"})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
