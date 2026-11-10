import asyncio
import hashlib
import sys
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from component import effects, trimlight  # noqa: E402
from fake_controller import FakeController, FakeDiscoveryResponder  # noqa: E402
from trimlight import (  # noqa: E402
    Command,
    Frame,
    SFrameMessage,
    StreamDecoder,
    Sub,
    SwitchState,
    TrimlightClient,
    TrimlightError,
    b64decode,
    b64encode,
    encode_frame,
    encode_passthrough,
    encode_s_frame,
    parse_discovery_reply,
    sync_request_data,
    verification_value,
)


class EncodingTests(unittest.TestCase):
    def test_frame_layout(self):
        body = encode_passthrough(Sub.SWITCH_STATE, b"\x01")
        frame = encode_frame(Command.PASSTHROUGH, body, key=0x5A)
        self.assertEqual(frame[:6], b"SPTECH")
        self.assertEqual(frame[6:10], bytes((0x04, 0, 0, 4)))
        self.assertEqual(frame[10:26], hashlib.md5(bytes((0x02, 0, 1, 1))).digest())
        self.assertEqual(frame[26], 0x5A)
        self.assertEqual(frame[27:], bytes(b ^ 0x5A for b in (0x02, 0, 1, 1)))

    def test_heartbeat_frame(self):
        frame = encode_frame(Command.HEARTBEAT, b"\x01", key=0)
        self.assertEqual(frame, b"SPTECH\x0b\x00\x00\x01" + hashlib.md5(b"\x01").digest() + b"\x00\x01")

    def test_decoder_reassembles_byte_by_byte(self):
        frames = encode_frame(Command.PASSTHROUGH, encode_passthrough(Sub.SYNC, bytes(range(200)))) + encode_s_frame(b"0d0d")
        decoder = StreamDecoder()
        out = []
        for byte in frames:
            out += decoder.feed(bytes((byte,)))
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], Frame)
        self.assertTrue(out[0].md5_ok)
        self.assertEqual(out[0].sub, Sub.SYNC)
        self.assertEqual(out[0].data, bytes(range(200)))
        self.assertEqual(out[1], SFrameMessage(b"0d0d"))

    def test_decoder_skips_garbage(self):
        out = StreamDecoder().feed(b"\x00\xff" + bytes.fromhex("5350544543480b00000100"))
        self.assertEqual([f.command for f in out], [Command.HEARTBEAT])

    def test_short_form_heartbeat_reply_captured_from_controller(self):
        sync = encode_frame(Command.PASSTHROUGH, encode_passthrough(Sub.SYNC, b"\x01\x02"))
        out = StreamDecoder().feed(bytes.fromhex("5350544543480b00000100") + sync)
        self.assertEqual([(f.command, f.body) for f in out], [(Command.HEARTBEAT, b"\x00"), (Command.PASSTHROUGH, bytes((Sub.SYNC, 0, 2, 1, 2)))])

    def test_request_decoder_reads_full_form_heartbeat(self):
        out = StreamDecoder(frozenset()).feed(encode_frame(Command.HEARTBEAT, b"\x01"))
        self.assertEqual([(f.command, f.body, f.md5_ok) for f in out], [(Command.HEARTBEAT, b"\x01", True)])

    def test_custom_base64(self):
        self.assertEqual(b64encode(b"\x00\x00\x00"), "0000")
        self.assertEqual(b64encode(b"\xff\xff\xff"), "////")
        for data in (b"\x01\x02\x03", b"\x07", b"\x10\x20"):
            self.assertEqual(b64decode(b64encode(data)), data)

    def test_verify_request(self):
        payload = b64encode(bytes((1, 2, 3))).encode()
        self.assertEqual(encode_s_frame(payload)[:6], bytes((0x53, 1, 0, 0, 4, 4)))
        self.assertEqual(verification_value(1, 2, 3), ((1 ^ 3) << 2 ^ 2) & 0xFF)

    def test_sync_request_data(self):
        self.assertEqual(sync_request_data(datetime(2026, 11, 8, 18, 5, 9)), bytes((26, 11, 8, 7, 18, 5, 9)))

    def test_discovery_reply(self):
        payload = bytes((1, 7)) + b"\x04CODE" + b"\x05Porch" + b"\x03key"
        n = len(payload)
        device = parse_discovery_reply(b"SPTECH\x06" + bytes((0, 0, n)) + payload, "10.0.0.5")
        self.assertEqual((device.host, device.name, device.device_code, device.hash_key, device.category, device.reserve), ("10.0.0.5", "Porch", "CODE", "key", 1, 7))
        self.assertIsNone(parse_discovery_reply(b"SPTECH\x06\x00\x00\x09" + payload, "10.0.0.5"))

    def test_parse_captured_sync(self):
        data = bytes.fromhex((Path(__file__).parent / "fixtures" / "sync_reply.hex").read_text().strip())
        state = trimlight.SyncState.from_data(data)
        self.assertEqual((state.switch_state, state.ic_type, state.channel_order), (0, 0, 0))
        self.assertEqual(state.ports, ((1, 200), (1, 600), (1, 600), (1, 600)))
        self.assertEqual([e.id for e in state.library], list(range(1, 18)))
        halloween = state.library[6]
        self.assertEqual((halloween.name, halloween.category, halloween.mode, halloween.speed, halloween.brightness), ("HALLOWEEN", 2, 1, 1, 255))
        self.assertEqual(len(halloween.segments), 150)
        self.assertEqual(halloween.segments[:5], bytes.fromhex("05ff260001"))
        self.assertEqual(state.library[16].name, "HA Color")
        current = state.current_effect
        self.assertEqual((current.category, current.mode, current.speed, current.brightness), (2, 0, 127, 255))
        self.assertEqual(current.segments[:15], bytes.fromhex("05ff000001" "05ffffff01" "0500000001"))
        self.assertEqual(state.mcu_version, 0x1042)
        self.assertEqual(state.combined, trimlight.CombinedEffect((12,), 5))
        self.assertEqual([s.id for s in state.calendar], [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12])
        self.assertEqual(state.calendar[5], trimlight.CalendarSchedule(6, 9, (12, 1), (12, 27), time(16, 22), time(7, 6)))
        self.assertEqual(state.calendar[3].effect_id, trimlight.COMBINED_EFFECT_ID)
        self.assertEqual(state.calendar[5].encode(), bytes.fromhex("06090c010c1b10160706"))
        self.assertEqual(state.daily[0], trimlight.DailySchedule(False, 10, trimlight.Repeat.TODAY_ONLY, time(16, 25), time(0, 0)))
        self.assertEqual(len(state.daily), 2)
        self.assertEqual((state.overlays, state.time_sync_auto, state.clock), ((), True, datetime(2026, 11, 8, 19, 50, 45)))

    def test_parse_captured_sync_in_music_mode(self):
        data = bytes.fromhex((Path(__file__).parent / "fixtures" / "sync_reply_music.hex").read_text().strip())
        state = trimlight.SyncState.from_data(data)
        self.assertEqual((state.current_effect.category, state.current_effect.mode), (trimlight.CATEGORY_MUSIC, 0))
        self.assertEqual(state.music, trimlight.MusicSettings(trimlight.MusicSource.AUX, (255, 0, 0), 70, 13))
        self.assertEqual((len(state.calendar), state.port_mask, state.clock), (11, 0x0F, datetime(2026, 11, 9, 10, 47, 12)))

    def test_parse_music_settings(self):
        data = bytes.fromhex((Path(__file__).parent / "fixtures" / "sync_reply.hex").read_text().strip())
        self.assertEqual(trimlight.SyncState.from_data(data).music, trimlight.MusicSettings(trimlight.MusicSource.AUX, (255, 0, 0), 70, 13))

    def test_library_entries_encode_back_to_captured_bytes(self):
        data = bytes.fromhex((Path(__file__).parent / "fixtures" / "sync_reply.hex").read_text().strip())
        library = trimlight.SyncState.from_data(data).library
        self.assertEqual(b"".join(e.encode() for e in library), data[23 : 23 + 17 * 182])

    def test_parse_captured_sync_showing_builtin_effect(self):
        data = bytes.fromhex((Path(__file__).parent / "fixtures" / "sync_reply_builtin.hex").read_text().strip())
        state = trimlight.SyncState.from_data(data)
        self.assertEqual(state.switch_state, 1)
        self.assertEqual(len(state.library), 17)
        self.assertEqual(state.current_effect, trimlight.CurrentEffect(1, 3, 100, 180, 30, False, b""))

    def test_parse_variable_length_blocks(self):
        data = bytearray.fromhex((Path(__file__).parent / "fixtures" / "sync_reply.hex").read_text().strip())
        after_library = 23 + 17 * 182
        saved_builtin = trimlight.LibraryEffect(18, "Rainbow", trimlight.CATEGORY_BUILTIN, 3, 100, 255, b"", 30, True)
        data[22] = 18
        data[after_library:after_library] = saved_builtin.encode()
        combined = after_library + 32
        data[combined : combined + 3] = bytes((3, 12, 7, 18, 9))
        overlay_count = len(data) - 16 + 7
        data[overlay_count : overlay_count + 1] = bytes((2, 0, 7, 1, 9))
        state = trimlight.SyncState.from_data(bytes(data))
        self.assertEqual(state.library[-1], saved_builtin)
        self.assertEqual(state.combined, trimlight.CombinedEffect((12, 7, 18), 9))
        self.assertEqual(len(state.calendar), 11)
        self.assertEqual(state.current_effect.speed, 127)
        self.assertEqual(state.overlays, (trimlight.Overlay(trimlight.OverlayKind.LIGHTNING, 7), trimlight.Overlay(trimlight.OverlayKind.SNOW, 9)))
        self.assertEqual(state.clock, datetime(2026, 11, 8, 19, 50, 45))

    def test_parse_truncated_reply_keeps_what_was_read(self):
        data = bytes.fromhex((Path(__file__).parent / "fixtures" / "sync_reply.hex").read_text().strip())
        state = trimlight.SyncState.from_data(data[:3200])
        self.assertEqual((len(state.library), state.combined, state.calendar, state.current_effect), (17, trimlight.CombinedEffect((12,), 5), (), None))
        self.assertEqual(trimlight.SyncState.from_data(bytes(23) + b"\x05").library, ())

    def test_effect_tables(self):
        self.assertEqual((len(effects.BUILTIN_EFFECTS), len(effects.CUSTOM_MODES_V1), len(effects.CUSTOM_MODES_V2)), (180, 18, 21))
        self.assertEqual(len(set(effects.BUILTIN_EFFECTS)), 180)

    def test_encode_segments(self):
        segments = trimlight.encode_segments([(3, (255, 0, 0)), (120, (0, 0, 300))])
        self.assertEqual(len(segments), 150)
        self.assertEqual(segments[:15], bytes.fromhex("03ff000001" "5a0000ff01" "0000000001"))
        self.assertEqual(segments[-5:], bytes.fromhex("0000000001"))
        with self.assertRaises(ValueError):
            trimlight.encode_segments([(1, (0, 0, 0))] * 31)

    def test_unlit_segments_round_trip(self):
        colors = [(3, (255, 0, 0)), (4, None), (5, (0, 0, 0))]
        segments = trimlight.encode_segments(colors)
        self.assertEqual(segments[:15], bytes.fromhex("03ff000001" "0400000000" "0500000001"))
        self.assertEqual(trimlight.decode_segments(segments), colors)
        self.assertEqual(trimlight.decode_segments(b"\x05" + bytes(118) + b"\x01"), [(5, (0, 0, 0))])
        self.assertEqual(trimlight.decode_segments(b"short"), [])


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = FakeController()
        port = await self.controller.start()
        self.client = TrimlightClient("127.0.0.1", port, reply_timeout=0.5)

    async def asyncTearDown(self):
        await self.client.close()
        await self.controller.stop()

    async def test_connect_verifies_and_syncs(self):
        state = await self.client.connect()
        self.assertTrue(self.client.verified)
        self.assertEqual(state.switch_state, SwitchState.MANUAL)
        self.assertEqual(state.channel_order, 2)
        self.assertEqual(self.controller.passthrough[0][0], Sub.SYNC)
        self.assertEqual(len(self.controller.passthrough[0][1]), 7)

    async def test_sync_right_after_connect_reuses_it(self):
        await self.client.connect()
        await self.client.sync()
        self.assertEqual([sub for sub, _ in self.controller.passthrough], [Sub.SYNC])

    async def test_back_to_back_requests_are_paced(self):
        await self.client.connect()
        for _ in range(3):
            await self.client.request_passthrough(Sub.SYNC, sync_request_data(datetime.now()))
        self.assertEqual(self.controller.dropped, 0)

    async def test_switch_state_round_trip(self):
        await self.client.connect()
        await self.client.set_switch_state(SwitchState.OFF)
        state = await self.client.sync()
        self.assertFalse(state.is_on)

    async def test_command_payloads(self):
        await self.client.connect()
        await self.client.preview_builtin(5, brightness=200, speed=10, pixel_len=100, reverse=True)
        await self.client.preview_color(300, 128, -4)
        await self.client.show_library_effect(3)
        await self.client.preview_custom(1, 2, 300, bytes.fromhex("05ff260001"))
        await asyncio.sleep(0.05)
        self.assertEqual(
            self.controller.passthrough[1:],
            [
                (Sub.PREVIEW_BUILTIN, bytes((5, 10, 200, 90, 1))),
                (Sub.PREVIEW_COLOR, bytes((255, 128, 0))),
                (Sub.SHOW_LIBRARY_EFFECT, b"\x03"),
                (Sub.PREVIEW_CUSTOM, bytes.fromhex("0102ff05ff260001")),
            ],
        )

    async def test_old_firmware_gets_lightning_only(self):
        state = await self.client.connect()
        self.assertLess(state.mcu_version & 0xFFF, trimlight.OVERLAY_MCU_VERSION)
        await self.client.set_overlays([trimlight.Overlay(trimlight.OverlayKind.LIGHTNING, 3), trimlight.Overlay(trimlight.OverlayKind.SNOW, 7)])
        await asyncio.sleep(0.05)
        self.assertEqual(self.controller.passthrough[-1], (Sub.UPDATE_LIGHTNING_EFFECT, b"\x03"))

    async def test_memory_change_payloads(self):
        self.controller.sync_tail = bytes.fromhex((Path(__file__).parent / "fixtures" / "sync_reply.hex").read_text().strip())[1:]
        await self.client.connect()
        saved = trimlight.LibraryEffect(18, "Rainbow", trimlight.CATEGORY_BUILTIN, 0, 100, 255, b"", 30, False)
        schedule = trimlight.CalendarSchedule(9, 9, (12, 20), (1, 5), time(16, 30), time(23, 0))
        daily = trimlight.DailySchedule(True, 10, trimlight.Repeat.EVERY_DAY, time(17, 0), time(23, 30))
        await self.client.add_library_effect(saved)
        await self.client.remove_library_effect(18)
        await self.client.set_calendar_schedule(schedule, new=True)
        await self.client.set_calendar_schedule(schedule, new=False)
        await self.client.delete_calendar_schedule(3)
        await self.client.set_daily_schedule(0, daily, datetime(2026, 11, 8).date())
        await self.client.set_combined_effect(trimlight.CombinedEffect((3, 7), 10))
        await self.client.set_overlays([trimlight.Overlay(trimlight.OverlayKind.LIGHTNING, 3), trimlight.Overlay(trimlight.OverlayKind.SNOW, 7)])
        await asyncio.sleep(0.05)
        # Byte strings from the app's own builders, as decoded from libapp.so.
        self.assertEqual(
            self.controller.passthrough[1:],
            [
                (Sub.ADD_LIBRARY_EFFECT, bytes.fromhex("12" "5261696e626f77" + "00" * 18 + "010064ff1e00")),
                (Sub.REMOVE_LIBRARY_EFFECT, b"\x12"),
                (Sub.ADD_CALENDAR_SCHEDULE, bytes.fromhex("09090c140105101e1700")),
                (Sub.MODIFY_CALENDAR_SCHEDULE, bytes.fromhex("09090c140105101e1700")),
                (Sub.DELETE_CALENDAR_SCHEDULE, b"\x03"),
                (Sub.MODIFY_DAILY_SCHEDULE, bytes.fromhex("00010a011100171e0b08")),
                (Sub.UPDATE_COMBINED_EFFECT, bytes.fromhex("0203070a")),
                (Sub.UPDATE_OVERLAY_EFFECT, bytes.fromhex("00030107")),
            ],
        )

    async def test_second_client_takes_over_the_controller(self):
        await self.client.connect()
        other = TrimlightClient("127.0.0.1", self.client.port, reply_timeout=0.5)
        await other.connect()
        await asyncio.sleep(0.05)
        self.assertTrue(self.client.closed_by_controller_within(10))
        self.assertFalse(other.closed_by_controller_within(10))
        await self.client.sync(fresh=True)
        self.assertFalse(self.client.closed_by_controller_within(10))
        await other.close()

    async def test_port_and_rename_payloads(self):
        await self.client.connect()
        await self.client.set_port(0, 1, 200, True)
        await self.client.set_port(3, 1, 600, False)
        await self.client.rename("Roofline")
        await asyncio.sleep(0.05)
        self.assertEqual(self.controller.passthrough[-2:], [(Sub.PORT_RANGES, bytes.fromhex("00000100c801")), (Sub.PORT_RANGES, bytes.fromhex("030001025800"))])
        self.assertEqual(self.controller.commands, [(Command.RENAME, b"\x08Roofline")])
        with self.assertRaises(ValueError):
            await self.client.set_port(0, 0, 10, True)
        with self.assertRaises(ValueError):
            await self.client.rename("Dach/Licht")

    async def test_music_payload(self):
        await self.client.connect()
        await self.client.set_music(13, (0, 0, 255), 200, 0)
        await asyncio.sleep(0.05)
        self.assertEqual(self.controller.passthrough[-1], (Sub.MUSIC_DATA, bytes((2, 13, 0, 0, 255, 150, 1)) + bytes(16)))

    async def test_firmware_from_verify_reply(self):
        await self.client.connect()
        self.assertEqual(self.client.firmware, trimlight.FirmwareInfo(12, "1.11", 0x1042))

    async def test_reconnects_after_drop(self):
        await self.client.connect()
        await self.controller.drop_connections()
        await self.client.set_switch_state(SwitchState.TIMER)
        await asyncio.sleep(0.05)
        self.assertEqual(self.controller.connections, 2)
        self.assertEqual(self.controller.switch_state, SwitchState.TIMER)

    async def test_heartbeat_after_idle(self):
        await self.client.connect()
        with mock.patch.object(trimlight, "IDLE_CHECK_SECONDS", -1):
            await self.client.set_switch_state(SwitchState.OFF)
        self.assertEqual(self.controller.heartbeats, 1)
        self.assertEqual(self.controller.connections, 1)

    async def test_unanswered_heartbeat_reconnects(self):
        await self.client.connect()
        self.controller.answer_heartbeat = False
        with mock.patch.object(trimlight, "IDLE_CHECK_SECONDS", -1):
            await self.client.set_switch_state(SwitchState.OFF)
        self.assertEqual(self.controller.connections, 2)

    async def test_unreachable_host(self):
        await self.controller.stop()
        with self.assertRaises(TrimlightError):
            await self.client.connect()


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_on_loopback(self):
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(lambda: FakeDiscoveryResponder("Roofline"), local_addr=("127.0.0.1", trimlight.DISCOVERY_PORT))
        except OSError as err:
            self.skipTest(f"UDP {trimlight.DISCOVERY_PORT} unavailable: {err}")
        try:
            found = await trimlight.discover(timeout=1.5, broadcast="127.0.0.1")
        finally:
            transport.close()
        self.assertEqual([(d.host, d.name, d.device_code) for d in found], [("127.0.0.1", "Roofline", "TL-EDGE-01")])


if __name__ == "__main__":
    unittest.main()
