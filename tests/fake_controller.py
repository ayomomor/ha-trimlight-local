"""A stand-in controller that speaks the protocol in PROTOCOL.md, for exercising the client without hardware."""

from __future__ import annotations

import asyncio

from component import trimlight

Command = trimlight.Command
Frame = trimlight.Frame
MAGIC = trimlight.MAGIC
SFrameMessage = trimlight.SFrameMessage
StreamDecoder = trimlight.StreamDecoder
Sub = trimlight.Sub
b64decode = trimlight.b64decode
b64encode = trimlight.b64encode
encode_frame = trimlight.encode_frame
encode_passthrough = trimlight.encode_passthrough
encode_s_frame = trimlight.encode_s_frame
verification_value = trimlight.verification_value


# Music source, colour and length, then the port enable mask: the block between the current effect and the overlay lists.
MUSIC_AND_PORT_MASK_LEN = 6


class FakeController:
    def __init__(self) -> None:
        self.switch_state = 1
        self.sync_tail = bytes((0, 2)) + bytes(range(40))
        self.answer_heartbeat = True
        # Like the real controller, ignore a request that arrives right after a reply.
        self.drop_within = 0.1
        self.dropped = 0
        self._last_reply_at = -1.0
        self.connections = 0
        self.heartbeats = 0
        self.passthrough: list[tuple[int, bytes]] = []
        self.commands: list[tuple[int, bytes]] = []
        self.music_frames: list[bytes] = []
        self.name = "Trimlight"
        # Set to model firmware that, like the app, refuses memory changes outside manual mode.
        self.writes_need_manual = False
        self.ignored_writes = 0
        # Memory writes to lose without a trace, as the real controller now and then does.
        self.lost_writes = 0
        # Seconds to ignore every request after a write with this sub-command, as the real controller does after schedule changes.
        self.silent_after: dict[int, float] = {}
        self._silent_until = -1.0
        self._server: asyncio.base_events.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self._ignored: list[asyncio.StreamWriter] = []
        self.ignored_connections = 0

    async def start(self, port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        await self.drop_connections()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def drop_connections(self) -> None:
        for writer in self._writers + self._ignored:
            writer.close()
        self._writers.clear()
        self._ignored.clear()
        await asyncio.sleep(0.05)

    @property
    def serving(self) -> bool:
        return bool(self._writers)

    def sync_data(self) -> bytes:
        return bytes((self.switch_state,)) + self.sync_tail

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        # Like the real controller with a client on another device: while one is served, a new one is accepted but never answered.
        if self._writers:
            self.ignored_connections += 1
            self._ignored.append(writer)
            try:
                while await reader.read(4096):
                    pass
            except (ConnectionError, OSError):
                pass
            finally:
                if writer in self._ignored:
                    self._ignored.remove(writer)
                writer.close()
            return
        self._writers = [writer]
        decoder = StreamDecoder(frozenset())
        loop = asyncio.get_running_loop()
        try:
            while data := await reader.read(4096):
                for message in decoder.feed(data):
                    if loop.time() < self._silent_until:
                        self.dropped += 1
                        continue
                    if loop.time() - self._last_reply_at < self.drop_within:
                        self.dropped += 1
                        continue
                    reply = self._reply(message)
                    if reply:
                        self._last_reply_at = loop.time() + 0.01
                        # Split the reply to make the client reassemble it.
                        writer.write(reply[:5])
                        await writer.drain()
                        await asyncio.sleep(0.01)
                        writer.write(reply[5:])
                        await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            writer.close()

    def _reply(self, message: Frame | SFrameMessage) -> bytes | None:
        if isinstance(message, SFrameMessage):
            a, b, c = b64decode(message.payload.decode("ascii"))
            # Same shape as the real controller's reply: check, module version 12, "1.11", MCU version 0x1042.
            return encode_s_frame(b64encode(bytes((verification_value(a, b, c), 0, 12, 4)) + b"1.11" + bytes((0x10, 0x42))).encode("ascii"))
        assert message.md5_ok
        if message.command == Command.HEARTBEAT:
            self.heartbeats += 1
            return MAGIC + bytes((Command.HEARTBEAT, 0, 0, 1, 0)) if self.answer_heartbeat else None
        if message.command == Command.MUSIC:
            self.music_frames.append(message.body)
            return None
        if message.command == Command.RENAME:
            self.commands.append((message.command, message.body))
            self.name = message.body[1 : 1 + message.body[0]].decode("ascii")
            return None
        if message.command == Command.PASSTHROUGH:
            self.passthrough.append((message.body[0], message.data))
            if message.sub == Sub.SYNC:
                return encode_frame(Command.PASSTHROUGH, encode_passthrough(Sub.SYNC, self.sync_data()))
            if message.sub == Sub.SWITCH_STATE:
                self.switch_state = message.data[0]
            elif message.sub == Sub.PORT_RANGES:
                self._set_port(message.data)
            elif message.sub in (Sub.IC_TYPE, Sub.CHANNEL_ORDER):
                # The IC type and channel order are sync data bytes 1 and 2, the first two of sync_tail.
                tail = bytearray(self.sync_tail)
                tail[0 if message.sub == Sub.IC_TYPE else 1] = message.data[0]
                self.sync_tail = bytes(tail)
            elif message.sub in _WRITES and self.lost_writes:
                self.lost_writes -= 1
            elif message.sub in _WRITES:
                self._apply_write(message.sub, message.data)
                if message.sub in self.silent_after:
                    self._silent_until = asyncio.get_running_loop().time() + self.silent_after[message.sub]
        return None

    def _set_port(self, data: bytes) -> None:
        index, enabled = data[0], data[5]
        raw = bytearray(self.sync_data())
        raw[3 + 4 * index : 7 + 4 * index] = data[1:5]
        state = trimlight.SyncState.from_data(bytes(raw))
        if state.clock is not None:
            mask_at = len(_encode_before_tail(state, state.library, state.combined, state.calendar, state.daily)) + MUSIC_AND_PORT_MASK_LEN - 1
            raw[mask_at] = raw[mask_at] | (1 << index) if enabled else raw[mask_at] & ~(1 << index)
        self.sync_tail = bytes(raw[1:])

    def _apply_write(self, sub: int, data: bytes) -> None:
        if self.writes_need_manual and self.switch_state != 1:
            self.ignored_writes += 1
            return
        state = trimlight.SyncState.from_data(self.sync_data())
        if state.clock is None:
            return
        library, calendar, daily, combined, overlays = list(state.library), list(state.calendar), list(state.daily), state.combined, list(state.overlays)
        if sub in (Sub.ADD_LIBRARY_EFFECT, Sub.MODIFY_LIBRARY_EFFECT):
            effect = trimlight._read_library_effect(trimlight._Reader(data, 0), True)
            library = [e for e in library if e.id != effect.id] + [effect] if sub == Sub.ADD_LIBRARY_EFFECT else [effect if e.id == effect.id else e for e in library]
        elif sub == Sub.REMOVE_LIBRARY_EFFECT:
            library = [e for e in library if e.id != data[0]]
        elif sub in (Sub.ADD_CALENDAR_SCHEDULE, Sub.MODIFY_CALENDAR_SCHEDULE):
            schedule = trimlight._read_calendar_schedule(trimlight._Reader(data, 0))
            calendar = [s for s in calendar if s.id != schedule.id] + [schedule] if sub == Sub.ADD_CALENDAR_SCHEDULE else [schedule if s.id == schedule.id else s for s in calendar]
        elif sub == Sub.DELETE_CALENDAR_SCHEDULE:
            calendar = [s for s in calendar if s.id != data[0]]
        elif sub == Sub.MODIFY_DAILY_SCHEDULE:
            daily[data[0]] = trimlight._read_daily_schedule(trimlight._Reader(data[1:8], 0))
        elif sub == Sub.UPDATE_COMBINED_EFFECT:
            combined = trimlight.CombinedEffect(tuple(data[1 : 1 + data[0]]), data[1 + data[0]])
        elif sub == Sub.UPDATE_OVERLAY_EFFECT:
            overlays = [trimlight.Overlay(data[i], data[i + 1]) for i in range(0, len(data), 2)]
        original = _encode_before_tail(state, state.library, state.combined, state.calendar, state.daily)
        music_and_ports = state.raw[len(original) : len(original) + MUSIC_AND_PORT_MASK_LEN]
        overlay_block = bytes((0, len(overlays))) + b"".join(bytes((o.kind, o.effect_id)) for o in overlays)
        # The last 8 bytes are the time-sync mode, the clock and one unused byte.
        self.sync_tail = (_encode_before_tail(state, library, combined, calendar, daily) + music_and_ports + overlay_block + state.raw[-8:])[1:]


_WRITES = frozenset(
    (
        Sub.ADD_LIBRARY_EFFECT,
        Sub.MODIFY_LIBRARY_EFFECT,
        Sub.REMOVE_LIBRARY_EFFECT,
        Sub.ADD_CALENDAR_SCHEDULE,
        Sub.MODIFY_CALENDAR_SCHEDULE,
        Sub.DELETE_CALENDAR_SCHEDULE,
        Sub.MODIFY_DAILY_SCHEDULE,
        Sub.UPDATE_COMBINED_EFFECT,
        Sub.UPDATE_OVERLAY_EFFECT,
    )
)


def _encode_before_tail(state, library, combined, calendar, daily) -> bytes:
    """The sync data up to the music settings, in the layout SyncState.from_data reads."""
    current = state.current_effect
    return b"".join(
        (
            state.raw[:22],
            bytes((len(library),)) + b"".join(e.encode() for e in library),
            bytes((len(combined.effect_ids), *combined.effect_ids, combined.interval)),
            bytes((len(calendar),)) + b"".join(s.encode() for s in calendar),
            b"".join(bytes((int(d.enabled), d.effect_id, d.repeat, d.on_time.hour, d.on_time.minute, d.off_time.hour, d.off_time.minute)) for d in daily),
            bytes((current.category, current.mode))
            + (b"" if current.category == trimlight.CATEGORY_MUSIC else bytes((current.speed, current.brightness, current.pixel_len, int(current.reverse))) + current.segments),
        )
    )


class FakeDiscoveryResponder(asyncio.DatagramProtocol):
    def __init__(self, name: str) -> None:
        self.name = name
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if data[:7] != MAGIC + bytes((Command.DISCOVER,)):
            return
        payload = bytes((1, 0)) + _field(b"TL-EDGE-01") + _field(self.name.encode()) + _field(b"abc123")
        n = len(payload)
        assert self.transport is not None
        self.transport.sendto(MAGIC + bytes((Command.DISCOVER, n >> 16, n >> 8 & 0xFF, n & 0xFF)) + payload, addr)


def _field(value: bytes) -> bytes:
    return bytes((len(value),)) + value
