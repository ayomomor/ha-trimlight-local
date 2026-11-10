"""Local LAN client for Trimlight Edge controllers.

Standard library only, so it can be used outside Home Assistant. The wire format is described in PROTOCOL.md.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import itertools
import logging
import random
import re
import socket
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from enum import IntEnum

_LOGGER = logging.getLogger(__name__)

TCP_PORT = 8586
DISCOVERY_LISTEN_PORT = 8588
DISCOVERY_PORT = 8589

MAGIC = b"SPTECH"
HEADER_LEN = 27
S_FRAME_MAGIC = 0x53

# The app re-checks a connection with a heartbeat before sending when it has been quiet this long.
IDLE_CHECK_SECONDS = 5.0
REPLY_TIMEOUT = 5.0
CONNECT_TIMEOUT = 10.0
# The app waits this long between the verify handshake and the first sync.
POST_VERIFY_DELAY = 0.4
# Measured on a controller: a request sent within a few ms of its previous reply is sometimes dropped without an answer; 0.2 s was always enough.
MIN_REQUEST_GAP = 0.3
# A sync this recent (usually the one connect() just did) is reused instead of asking again.
FRESH_SYNC_SECONDS = 2.0

B64_ALPHABET = "0dY8ubicPgrReph1ENo9jvCn67y2tFQ5HWJIwOVZzDfkT3K4GSsABlqUXaMLxm+/"
_STD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_TO_CUSTOM = str.maketrans(_STD_ALPHABET, B64_ALPHABET)
_TO_STD = str.maketrans(B64_ALPHABET, _STD_ALPHABET)


class Command(IntEnum):
    VERIFY = 0x01
    NETWORK_CONFIG = 0x02
    PASSTHROUGH = 0x04
    MODULE_FIRMWARE = 0x05
    DISCOVER = 0x06
    RENAME = 0x07
    BIG_DATA = 0x08
    MCU_FIRMWARE = 0x09
    MUSIC = 0x0A
    HEARTBEAT = 0x0B


# The controller answers these without the MD5 and key, e.g. a heartbeat reply is 53 50 54 45 43 48 0b 00 00 01 00.
SHORT_FORM_COMMANDS = frozenset((Command.HEARTBEAT, Command.DISCOVER))


class Sub(IntEnum):
    SWITCH_STATE = 0x02
    PREVIEW_BUILTIN = 0x03
    SHOW_LIBRARY_EFFECT = 0x04
    REMOVE_LIBRARY_EFFECT = 0x05
    CHANNEL_ORDER = 0x07
    IC_TYPE = 0x09
    PORT_RANGES = 0x0A
    PREVIEW_COLOR = 0x0B
    MUSIC_DATA = 0x0C
    PREVIEW_CUSTOM = 0x0D
    MODIFY_LIBRARY_EFFECT = 0x0E
    ADD_LIBRARY_EFFECT = 0x0F
    ADD_CALENDAR_SCHEDULE = 0x10
    MODIFY_CALENDAR_SCHEDULE = 0x11
    DELETE_CALENDAR_SCHEDULE = 0x12
    MODIFY_DAILY_SCHEDULE = 0x13
    UPDATE_COMBINED_EFFECT = 0x14
    SYNC = 0x15
    GROUP_SYNC = 0x16
    UPDATE_LIGHTNING_EFFECT = 0x1A
    UPDATE_OVERLAY_EFFECT = 0x1B
    UPDATE_TIME_SYNC_MODE = 0x1C


class SwitchState(IntEnum):
    OFF = 0
    MANUAL = 1
    TIMER = 2


IC_TYPES = {0: "UCS1903", 1: "DMX512", 2: "UCS5603"}
CHANNEL_ORDERS = {0: "RGB", 1: "RBG", 2: "GRB", 3: "GBR", 4: "BRG", 5: "BGR"}


class TrimlightError(Exception):
    """Raised when the controller cannot be reached or does not answer."""


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii").translate(_TO_CUSTOM)


def b64decode(text: str) -> bytes:
    text = text.strip().rstrip("=")
    return base64.b64decode(text.translate(_TO_STD) + "=" * (-len(text) % 4))


def encode_frame(command: int, body: bytes, key: int | None = None) -> bytes:
    if key is None:
        key = random.randrange(255)
    n = len(body)
    return b"".join(
        (
            MAGIC,
            bytes((command, n >> 16 & 0xFF, n >> 8 & 0xFF, n & 0xFF)),
            hashlib.md5(body).digest(),
            bytes((key,)),
            bytes(b ^ key for b in body),
        )
    )


def encode_passthrough(sub: int, data: bytes = b"") -> bytes:
    n = len(data)
    return bytes((sub, n >> 8 & 0xFF, n & 0xFF)) + data


def encode_s_frame(payload: bytes) -> bytes:
    # Only the single-frame form is needed: the verify request is always 4 bytes.
    if len(payload) > 0xFF:
        raise ValueError("S-frame payload longer than one frame")
    n = len(payload)
    return bytes((S_FRAME_MAGIC, 1, 0, n >> 8 & 0xFF, n & 0xFF, n)) + payload


def verification_value(a: int, b: int, c: int) -> int:
    return (((a ^ c) << 2) ^ b) & 0xFF


@dataclass(frozen=True)
class Frame:
    """A decoded SPTECH frame."""

    command: int
    body: bytes
    md5_ok: bool

    @property
    def sub(self) -> int | None:
        return self.body[0] if self.command == Command.PASSTHROUGH and self.body else None

    @property
    def data(self) -> bytes:
        """Pass-through data after the sub-command and its 2-byte length."""
        return self.body[3:]


@dataclass(frozen=True)
class SFrameMessage:
    """A reassembled S-frame message (used only by the verify handshake)."""

    payload: bytes


class StreamDecoder:
    """Splits the TCP byte stream into SPTECH frames and S-frame messages.

    `short_form_commands` lists commands whose frames carry no MD5 and key. That holds for the controller's replies;
    a decoder reading the app's requests passes an empty set.
    """

    def __init__(self, short_form_commands: frozenset[int] = SHORT_FORM_COMMANDS) -> None:
        self._buf = bytearray()
        self._parts: dict[int, bytes] = {}
        self._short_form = short_form_commands

    def feed(self, data: bytes) -> list[Frame | SFrameMessage]:
        self._buf += data
        out: list[Frame | SFrameMessage] = []
        buf = self._buf
        while buf:
            if buf[0] != S_FRAME_MAGIC:
                skip = buf.find(S_FRAME_MAGIC)
                if skip < 0:
                    skip = len(buf)
                _LOGGER.debug("Discarding unexpected bytes: %s", bytes(buf[:skip]).hex())
                del buf[:skip]
                continue
            if len(buf) < 2:
                break
            if buf[1] == MAGIC[1]:
                if len(buf) < len(MAGIC):
                    break
                if buf[: len(MAGIC)] != MAGIC:
                    del buf[0]
                    continue
                if len(buf) < 10:
                    break
                n = int.from_bytes(buf[7:10], "big")
                if buf[6] in self._short_form:
                    if len(buf) < 10 + n:
                        break
                    out.append(Frame(buf[6], bytes(buf[10 : 10 + n]), True))
                    del buf[: 10 + n]
                    continue
                if len(buf) < HEADER_LEN + n:
                    break
                key = buf[26]
                body = bytes(b ^ key for b in buf[HEADER_LEN : HEADER_LEN + n])
                md5_ok = hashlib.md5(body).digest() == bytes(buf[10:26])
                if not md5_ok:
                    _LOGGER.debug("MD5 mismatch on frame cmd=0x%02x", buf[6])
                out.append(Frame(buf[6], body, md5_ok))
                del buf[: HEADER_LEN + n]
                continue
            if len(buf) < 6:
                break
            count, index, frame_len = buf[1], buf[2], buf[5]
            if len(buf) < 6 + frame_len:
                break
            self._parts[index] = bytes(buf[6 : 6 + frame_len])
            del buf[: 6 + frame_len]
            if len(self._parts) >= count:
                out.append(SFrameMessage(b"".join(self._parts[i] for i in sorted(self._parts))))
                self._parts.clear()
        return out


PORT_COUNT = 4
# The phone app enforces these; what the firmware accepts beyond them is unknown.
MAX_PORT_DOT = 2700
NAME_PATTERN = r"[ a-zA-Z0-9_-]{1,25}"
MCU_VERSION_OFFSET = 19
LIBRARY_COUNT_OFFSET = 22
LIBRARY_NAME_LEN = 25
LIBRARY_HEADER_LEN = 1 + LIBRARY_NAME_LEN + 6
SEGMENT_COUNT = 30
# The app's limit for one colour's dot count ("Color pixels is up to 90.").
MAX_SEGMENT_DOTS = 90
# From this MCU firmware version on, each pattern segment has a fifth byte [0x444eb0].
V2_MCU_VERSION = 19
MAX_LIBRARY_EFFECTS = 60
MAX_CALENDAR_SCHEDULES = 60
DAILY_SCHEDULE_COUNT = 2
# Schedules and the combined effect's own entries refer to the combined effect by this effect id.
COMBINED_EFFECT_ID = 200
CATEGORY_BUILTIN = 1
CATEGORY_CUSTOM = 2
CATEGORY_MUSIC = 3
SOLID_COLOR_MODE = 181
MUSIC_SENSITIVITY_OFFSET = 21
MUSIC_BANDS = 16


def segment_len(v2: bool) -> int:
    return 5 if v2 else 4


def library_entry_len(category: int, v2: bool) -> int | None:
    if category == CATEGORY_CUSTOM:
        return LIBRARY_HEADER_LEN + SEGMENT_COUNT * segment_len(v2)
    if category == CATEGORY_BUILTIN:
        return LIBRARY_HEADER_LEN
    if category == CATEGORY_MUSIC:
        return LIBRARY_HEADER_LEN + 1
    return None


def encode_segments(colors: list[tuple[int, tuple[int, int, int] | None]]) -> bytes:
    """Encode up to 30 (dots, (R, G, B)) pairs as the 30 v2 segments of a custom pattern; a colour of None leaves those dots unlit."""
    if len(colors) > SEGMENT_COUNT:
        raise ValueError(f"at most {SEGMENT_COUNT} colours")
    # The fifth byte is 0 only for the app's "no colour" swatch; every other segment, unused ones included, has 1.
    used = b"".join(bytes((max(1, min(MAX_SEGMENT_DOTS, dots)), *(_byte(c) for c in rgb or (0, 0, 0)), 0 if rgb is None else 1)) for dots, rgb in colors)
    return used + bytes((0, 0, 0, 0, 1)) * (SEGMENT_COUNT - len(colors))


def decode_segments(segments: bytes) -> list[tuple[int, tuple[int, int, int] | None]]:
    """The colours of a custom pattern as (dots, (R, G, B)) pairs, None for "no colour"; unused segments are left out."""
    size = len(segments) // SEGMENT_COUNT
    if size not in (segment_len(True), segment_len(False)) or len(segments) != size * SEGMENT_COUNT:
        return []
    colors = []
    for i in range(0, len(segments), size):
        dots, red, green, blue = segments[i : i + 4]
        if dots:
            colors.append((dots, (red, green, blue) if size == 4 or segments[i + 4] else None))
    return colors


@dataclass(frozen=True)
class LibraryEffect:
    """An effect saved in the controller's own library: a custom pattern (with segments) or a built-in effect with its settings."""

    id: int
    name: str
    category: int
    mode: int
    speed: int
    brightness: int
    segments: bytes
    pixel_len: int = 0
    reverse: bool = False

    def encode(self) -> bytes:
        # The app writes one byte per UTF-16 code unit, so names are effectively Latin-1.
        name = self.name.encode("latin-1", "replace")[:LIBRARY_NAME_LEN].ljust(LIBRARY_NAME_LEN, b"\0")
        head = bytes((self.id,)) + name + bytes((self.category, self.mode, _byte(self.speed), _byte(self.brightness), self.pixel_len, int(self.reverse)))
        return head + (self.segments if self.category == CATEGORY_CUSTOM else b"")


@dataclass(frozen=True)
class CurrentEffect:
    """What the controller shows in manual mode. A solid colour is recorded as built-in mode 181, without the colour."""

    category: int
    mode: int
    speed: int
    brightness: int
    pixel_len: int
    reverse: bool
    segments: bytes


@dataclass(frozen=True)
class CombinedEffect:
    """Saved effects shown one after another, each for `interval` minutes."""

    effect_ids: tuple[int, ...]
    interval: int


@dataclass(frozen=True)
class CalendarSchedule:
    """Shows an effect every night from `on_time` to `off_time`, on each date from `start` to `end` (month, day), every year.

    `end` before `start` wraps past New Year; `off_time` before `on_time` ends the next morning.
    """

    id: int
    effect_id: int
    start: tuple[int, int]
    end: tuple[int, int]
    on_time: dtime
    off_time: dtime

    def encode(self) -> bytes:
        return bytes((self.id, self.effect_id, *self.start, *self.end, self.on_time.hour, self.on_time.minute, self.off_time.hour, self.off_time.minute))


class Repeat(IntEnum):
    TODAY_ONLY = 0
    EVERY_DAY = 1
    WEEKDAYS = 2
    WEEKEND = 3


@dataclass(frozen=True)
class DailySchedule:
    enabled: bool
    effect_id: int
    repeat: int
    on_time: dtime
    off_time: dtime

    def encode(self, index: int, today: date) -> bytes:
        # The command carries today's date, which is how the controller knows which day TODAY_ONLY means; the stored form drops it.
        on, off = self.on_time, self.off_time
        return bytes((index, int(self.enabled), self.effect_id, self.repeat, on.hour, on.minute, off.hour, off.minute, today.month, today.day))


class MusicSource(IntEnum):
    PHONE_PLAYER = 0
    PHONE_MICROPHONE = 1
    AUX = 2


@dataclass(frozen=True)
class MusicSettings:
    """The music effect settings the controller keeps: where the sound comes from, the colour for single-colour effects, length and sensitivity."""

    source: int
    color: tuple[int, int, int]
    length: int
    sensitivity: int | None


class OverlayKind(IntEnum):
    LIGHTNING = 0
    SNOW = 1


# MCU firmware below this (low 12 bits of the version) has only lightning, set with sub 0x1a [0x4a4398].
OVERLAY_MCU_VERSION = 60


@dataclass(frozen=True)
class Overlay:
    """Lightning or snow drawn over a saved effect."""

    kind: int
    effect_id: int


@dataclass(frozen=True)
class SyncState:
    """Controller state from a sync reply. Fields after `library` are None or empty when the reply is shorter than expected."""

    switch_state: int
    ic_type: int | None
    channel_order: int | None
    ports: tuple[tuple[int, int], ...]
    mcu_version: int | None
    library: tuple[LibraryEffect, ...]
    combined: CombinedEffect | None
    calendar: tuple[CalendarSchedule, ...]
    daily: tuple[DailySchedule, ...]
    current_effect: CurrentEffect | None
    music: MusicSettings | None
    port_mask: int | None
    overlays: tuple[Overlay, ...]
    time_sync_auto: bool | None
    clock: datetime | None
    raw: bytes

    @classmethod
    def from_data(cls, data: bytes) -> SyncState:
        if not data:
            raise TrimlightError("Empty sync reply")
        mcu_version = int.from_bytes(data[MCU_VERSION_OFFSET : MCU_VERSION_OFFSET + 2], "big") if len(data) >= MCU_VERSION_OFFSET + 2 else None
        v2 = mcu_version is None or mcu_version >= V2_MCU_VERSION
        fields: dict = {"library": (), "combined": None, "calendar": (), "daily": (), "current_effect": None, "music": None, "port_mask": None, "overlays": (), "time_sync_auto": None, "clock": None}
        reader = _Reader(data, LIBRARY_COUNT_OFFSET)
        try:
            fields["library"] = tuple(_read_library_effect(reader, v2) for _ in range(reader.byte()))
            ids = reader.take(reader.byte())
            fields["combined"] = CombinedEffect(tuple(ids), reader.byte())
            fields["calendar"] = tuple(_read_calendar_schedule(reader) for _ in range(reader.byte()))
            fields["daily"] = tuple(_read_daily_schedule(reader) for _ in range(DAILY_SCHEDULE_COUNT))
            fields["current_effect"] = _read_current_effect(reader, v2)
            source, red, green, blue, length, fields["port_mask"] = reader.take(6)
            sensitivity = data[MUSIC_SENSITIVITY_OFFSET] if len(data) > MUSIC_SENSITIVITY_OFFSET else None
            fields["music"] = MusicSettings(source, (red, green, blue), length, sensitivity)
            # Older firmware's lightning setting: a list of saved-effect ids, superseded by the overlay list.
            legacy_lightning = [i for i in reader.take(reader.byte()) if i != 0xFF]
            overlays = [Overlay(*reader.take(2)) for _ in range(reader.byte())]
            fields["overlays"] = tuple(overlays or (Overlay(OverlayKind.LIGHTNING, i) for i in legacy_lightning))
            fields["time_sync_auto"] = reader.byte() == 0
            fields["clock"] = _read_clock(reader.take(6))
        except _Truncated:
            _LOGGER.debug("Sync reply ends at %d bytes, before every field was read", len(data))
        except _Unrecognised as err:
            _LOGGER.debug("Sync reply not understood from byte %d: %s", reader.pos, err)
        return cls(
            switch_state=data[0],
            ic_type=data[1] if len(data) > 1 else None,
            channel_order=data[2] if len(data) > 2 else None,
            ports=_parse_ports(data),
            mcu_version=mcu_version,
            raw=data,
            **fields,
        )

    @property
    def is_on(self) -> bool:
        return self.switch_state != SwitchState.OFF

    def effect(self, effect_id: int) -> LibraryEffect | None:
        return next((e for e in self.library if e.id == effect_id), None)


class _Truncated(Exception):
    pass


class _Unrecognised(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise _Truncated
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def byte(self) -> int:
        return self.take(1)[0]


def _parse_ports(data: bytes) -> tuple[tuple[int, int], ...]:
    if len(data) < 3 + 4 * PORT_COUNT:
        return ()
    return tuple((int.from_bytes(data[3 + 4 * i : 5 + 4 * i], "big"), int.from_bytes(data[5 + 4 * i : 7 + 4 * i], "big")) for i in range(PORT_COUNT))


def _read_library_effect(reader: _Reader, v2: bool) -> LibraryEffect:
    head = reader.take(LIBRARY_HEADER_LEN)
    category = head[26]
    if (length := library_entry_len(category, v2)) is None:
        raise _Unrecognised(f"saved effect {head[0]} has category {category}")
    rest = reader.take(length - LIBRARY_HEADER_LEN)
    # The app ends a name at the first 00 or ff byte.
    raw_name = head[1 : 1 + LIBRARY_NAME_LEN]
    end = min((i for i, b in enumerate(raw_name) if b in (0, 0xFF)), default=LIBRARY_NAME_LEN)
    name = raw_name[:end].decode("latin-1").strip()
    segments = rest if category == CATEGORY_CUSTOM else b""
    return LibraryEffect(head[0], name, category, head[27], head[28], head[29], segments, head[30], head[31] == 1)


def _read_current_effect(reader: _Reader, v2: bool) -> CurrentEffect:
    category, mode = reader.take(2)
    if category == CATEGORY_MUSIC:
        # Seen on the controller: a music effect is recorded as only its category and number.
        return CurrentEffect(category, mode, 0, 255, 0, False, b"")
    speed, brightness, pixel_len, reverse = reader.take(4)
    segments = reader.take(SEGMENT_COUNT * segment_len(v2)) if category == CATEGORY_CUSTOM else b""
    return CurrentEffect(category, mode, speed, brightness, pixel_len, reverse == 1, segments)


def _read_calendar_schedule(reader: _Reader) -> CalendarSchedule:
    sid, effect_id, start_month, start_day, end_month, end_day, on_h, on_m, off_h, off_m = reader.take(10)
    return CalendarSchedule(sid, effect_id, (start_month, start_day), (end_month, end_day), _time(on_h, on_m), _time(off_h, off_m))


def _read_daily_schedule(reader: _Reader) -> DailySchedule:
    enabled, effect_id, repeat, on_h, on_m, off_h, off_m = reader.take(7)
    return DailySchedule(enabled == 1, effect_id, repeat, _time(on_h, on_m), _time(off_h, off_m))


def _read_clock(raw: bytes) -> datetime | None:
    try:
        return datetime(2000 + raw[0], *raw[1:6])
    except ValueError:
        return None


def _time(hour: int, minute: int) -> dtime:
    return dtime(min(hour, 23), min(minute, 59))


def next_free_id(used: Iterable[int], *, skip: int | None = None) -> int:
    """The id the app gives a new saved effect or calendar schedule: the smallest one not in use."""
    taken = set(used)
    return next(i for i in itertools.count(1) if i not in taken and i != skip)


@dataclass(frozen=True)
class FirmwareInfo:
    module_version: int
    version: str
    mcu_version: int | None


def parse_verify_reply(value: bytes) -> FirmwareInfo | None:
    """The decoded verify reply is [check, module version (2), n, version text (n), MCU version (2)]."""
    if len(value) < 4 or len(value) < 4 + value[3]:
        return None
    n = value[3]
    mcu = int.from_bytes(value[4 + n : 6 + n], "big") if len(value) >= 6 + n else None
    return FirmwareInfo(int.from_bytes(value[1:3], "big"), value[4 : 4 + n].decode("latin-1"), mcu)


def sync_request_data(now: datetime) -> bytes:
    return bytes((now.year % 2000 & 0xFF, now.month, now.day, now.isoweekday(), now.hour, now.minute, now.second))


class TrimlightClient:
    """One TCP control session to a controller, reconnecting as needed."""

    def __init__(self, host: str, port: int = TCP_PORT, *, reply_timeout: float = REPLY_TIMEOUT, clock: Callable[[], datetime] = datetime.now) -> None:
        self.host = host
        self._clock = clock
        self.port = port
        self._reply_timeout = reply_timeout
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._waiters: list[tuple[Callable[[Frame | SFrameMessage], bool], asyncio.Future]] = []
        self._last_activity = 0.0
        self._synced_at = 0.0
        self.verified: bool | None = None
        self.firmware: FirmwareInfo | None = None
        self.state: SyncState | None = None
        self._closed_by_controller_at: float | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and self._read_task is not None and not self._read_task.done()

    def closed_by_controller_within(self, seconds: float) -> bool:
        """Whether the controller dropped this connection less than `seconds` ago and it has not been reopened.

        The controller serves one client at a time and drops the older connection when another (such as the phone app) connects.
        """
        return not self.connected and self._closed_by_controller_at is not None and time.monotonic() - self._closed_by_controller_at < seconds

    async def connect(self) -> SyncState:
        async with self._lock:
            try:
                await self._connect_locked()
            except (OSError, ConnectionError, asyncio.TimeoutError) as err:
                await self._close_locked()
                raise TrimlightError(f"{self.host}: {err!r}") from err
            assert self.state is not None
            return self.state

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    async def sync(self, *, fresh: bool = False) -> SyncState:
        """Read the controller state; this also sets the controller's clock from `clock`, as the app does.

        A sync from the last couple of seconds is reused unless `fresh` is set.
        """

        async def refresh() -> None:
            if fresh or time.monotonic() - self._synced_at > FRESH_SYNC_SECONDS:
                await self._sync_locked()

        async with self._lock:
            await self._retry_locked(refresh)
            assert self.state is not None
            return self.state

    async def set_switch_state(self, state: SwitchState) -> None:
        await self.send_passthrough(Sub.SWITCH_STATE, bytes((state,)))

    async def preview_builtin(self, mode: int, *, brightness: int = 255, speed: int = 100, pixel_len: int = 30, reverse: bool = False) -> None:
        if not 0 <= mode <= 179:
            raise ValueError("mode must be 0..179")
        await self.send_passthrough(
            Sub.PREVIEW_BUILTIN,
            bytes((mode, _byte(speed), _byte(brightness), max(1, min(90, pixel_len)), int(reverse))),
        )

    async def preview_color(self, red: int, green: int, blue: int) -> None:
        await self.send_passthrough(Sub.PREVIEW_COLOR, bytes((_byte(red), _byte(green), _byte(blue))))

    async def show_library_effect(self, effect_id: int) -> None:
        await self.send_passthrough(Sub.SHOW_LIBRARY_EFFECT, bytes((_byte(effect_id),)))

    async def preview_custom(self, mode: int, speed: int, brightness: int, segments: bytes) -> None:
        """Show a custom pattern; `segments` uses the library layout, 5 bytes [count, R, G, B, flag] per segment."""
        await self.send_passthrough(Sub.PREVIEW_CUSTOM, bytes((_byte(mode), _byte(speed), _byte(brightness))) + segments)

    async def add_library_effect(self, effect: LibraryEffect) -> None:
        await self.send_passthrough(Sub.ADD_LIBRARY_EFFECT, effect.encode())

    async def modify_library_effect(self, effect: LibraryEffect) -> None:
        await self.send_passthrough(Sub.MODIFY_LIBRARY_EFFECT, effect.encode())

    async def remove_library_effect(self, effect_id: int) -> None:
        await self.send_passthrough(Sub.REMOVE_LIBRARY_EFFECT, bytes((effect_id,)))

    async def set_calendar_schedule(self, schedule: CalendarSchedule, *, new: bool) -> None:
        await self.send_passthrough(Sub.ADD_CALENDAR_SCHEDULE if new else Sub.MODIFY_CALENDAR_SCHEDULE, schedule.encode())

    async def delete_calendar_schedule(self, schedule_id: int) -> None:
        await self.send_passthrough(Sub.DELETE_CALENDAR_SCHEDULE, bytes((schedule_id,)))

    async def set_daily_schedule(self, index: int, schedule: DailySchedule, today: date) -> None:
        await self.send_passthrough(Sub.MODIFY_DAILY_SCHEDULE, schedule.encode(index, today))

    async def set_combined_effect(self, combined: CombinedEffect) -> None:
        await self.send_passthrough(Sub.UPDATE_COMBINED_EFFECT, bytes((len(combined.effect_ids), *combined.effect_ids, combined.interval)))

    async def set_music(self, effect: int, color: tuple[int, int, int], length: int, sensitivity: int, source: MusicSource = MusicSource.AUX) -> None:
        """Start a music effect. The phone app streams 16 frequency bands in this layout; with the AUX source it sends one frame of zero bands and stops."""
        head = bytes((source, _byte(effect), *(_byte(c) for c in color), max(1, min(150, length)), max(1, min(16, sensitivity))))
        await self.send_passthrough(Sub.MUSIC_DATA, head + bytes(MUSIC_BANDS))

    async def set_port(self, index: int, start: int, end: int, enabled: bool) -> None:
        """Set one output port's first and last dot, and whether it is used; the app sends one port per command."""
        if not 0 <= index < PORT_COUNT or not 1 <= start <= end <= MAX_PORT_DOT:
            raise ValueError("port index 0..3 and 1 <= start <= end <= 2700")
        await self.send_passthrough(Sub.PORT_RANGES, bytes((index,)) + start.to_bytes(2, "big") + end.to_bytes(2, "big") + bytes((int(enabled),)))

    async def rename(self, name: str) -> None:
        """Rename the controller as the phone app and discovery show it. The name is not part of the sync reply."""
        if not re.fullmatch(NAME_PATTERN, name):
            raise ValueError("1 to 25 letters, digits, spaces, - or _")
        frame = encode_frame(Command.RENAME, bytes((len(name),)) + name.encode("ascii"))
        async with self._lock:
            await self._retry_locked(lambda: self._write(frame))

    async def set_ic_type(self, ic_type: int) -> None:
        await self.send_passthrough(Sub.IC_TYPE, bytes((ic_type,)))

    async def set_channel_order(self, order: int) -> None:
        await self.send_passthrough(Sub.CHANNEL_ORDER, bytes((order,)))

    async def set_overlays(self, overlays: Sequence[Overlay]) -> None:
        """Replace the whole lightning/snow table; there is one entry per saved effect at most."""
        mcu_version = self.state.mcu_version if self.state is not None else None
        if mcu_version is not None and mcu_version & 0xFFF < OVERLAY_MCU_VERSION:
            await self.send_passthrough(Sub.UPDATE_LIGHTNING_EFFECT, bytes(o.effect_id for o in overlays if o.kind == OverlayKind.LIGHTNING))
        else:
            await self.send_passthrough(Sub.UPDATE_OVERLAY_EFFECT, b"".join(bytes((o.kind, o.effect_id)) for o in overlays))

    async def send_passthrough(self, sub: int, data: bytes = b"") -> None:
        frame = encode_frame(Command.PASSTHROUGH, encode_passthrough(sub, data))

        async def send() -> None:
            await self._write(frame)
            self._synced_at = 0.0

        async with self._lock:
            await self._retry_locked(send)

    async def request_passthrough(self, sub: int, data: bytes = b"", timeout: float | None = None) -> Frame:
        """Send a pass-through command and wait for the controller's reply to the same sub-command."""
        frame = encode_frame(Command.PASSTHROUGH, encode_passthrough(sub, data))
        result: list[Frame] = []

        async def exchange() -> None:
            result.append(await self._exchange(frame, lambda m: isinstance(m, Frame) and m.sub == sub, timeout))

        async with self._lock:
            await self._retry_locked(exchange)
        return result[0]

    async def _retry_locked(self, action: Callable[[], object]) -> None:
        for attempt in (1, 2):
            try:
                await self._ensure_alive_locked()
                await action()  # type: ignore[misc]
                return
            except asyncio.TimeoutError as err:
                if attempt == 2:
                    await self._close_locked()
                    raise TrimlightError(f"{self.host}: {err!r}") from err
                # Seen on the controller: it drops a request now and then but keeps the connection, so a heartbeat decides
                # whether to ask again on it or reconnect, which costs a verify and a full sync.
                _LOGGER.debug("%s: no answer, checking the connection", self.host)
                self._last_activity = 0.0
            except (OSError, ConnectionError) as err:
                await self._close_locked()
                if attempt == 2:
                    raise TrimlightError(f"{self.host}: {err!r}") from err
                _LOGGER.debug("%s: %r, reconnecting", self.host, err)

    async def _ensure_alive_locked(self) -> None:
        if not self.connected:
            await self._connect_locked()
            return
        if time.monotonic() - self._last_activity > IDLE_CHECK_SECONDS:
            try:
                await self._exchange(encode_frame(Command.HEARTBEAT, b"\x01"), lambda m: isinstance(m, Frame) and m.command == Command.HEARTBEAT)
            except (OSError, ConnectionError, asyncio.TimeoutError):
                _LOGGER.debug("%s: no heartbeat reply, reconnecting", self.host)
                await self._connect_locked()

    async def _connect_locked(self) -> None:
        await self._close_locked()
        _LOGGER.debug("Connecting to %s:%d", self.host, self.port)
        try:
            self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as err:
            raise TrimlightError(f"Cannot connect to {self.host}:{self.port}: {err!r}") from err
        self._read_task = asyncio.create_task(self._read_loop(self._reader, StreamDecoder()))
        self._touch()
        self.verified = await self._verify_locked()
        await asyncio.sleep(POST_VERIFY_DELAY)
        await self._sync_locked()

    async def _close_locked(self) -> None:
        task, writer = self._read_task, self._writer
        self._read_task = self._reader = self._writer = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._fail_waiters(ConnectionError("connection closed"))

    async def _verify_locked(self) -> bool:
        a, b, c = (random.randrange(256) for _ in range(3))
        request = encode_s_frame(b64encode(bytes((a, b, c))).encode("ascii"))
        try:
            reply = await self._exchange(request, lambda m: isinstance(m, SFrameMessage))
        except asyncio.TimeoutError:
            _LOGGER.warning("%s did not answer the verify handshake", self.host)
            return False
        assert isinstance(reply, SFrameMessage)
        try:
            value = b64decode(reply.payload.decode("ascii"))
        except ValueError:
            _LOGGER.warning("%s sent an unreadable verify reply: %s", self.host, reply.payload.hex())
            return False
        self.firmware = parse_verify_reply(value)
        ok = bool(value) and value[0] == verification_value(a, b, c)
        if not ok:
            _LOGGER.warning("%s verify reply %s does not match the expected value", self.host, value.hex())
        return ok

    async def _sync_locked(self) -> None:
        body = encode_passthrough(Sub.SYNC, sync_request_data(self._clock()))
        reply = await self._exchange(
            encode_frame(Command.PASSTHROUGH, body),
            lambda m: isinstance(m, Frame) and m.sub == Sub.SYNC,
        )
        assert isinstance(reply, Frame)
        self.state = SyncState.from_data(reply.data)
        self._synced_at = time.monotonic()
        _LOGGER.debug("%s sync: %s", self.host, reply.data.hex())

    async def _exchange(self, data: bytes, predicate: Callable[[Frame | SFrameMessage], bool], timeout: float | None = None) -> Frame | SFrameMessage:
        future = asyncio.get_running_loop().create_future()
        waiter = (predicate, future)
        self._waiters.append(waiter)
        try:
            await self._write(data)
            return await asyncio.wait_for(future, timeout or self._reply_timeout)
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)

    async def _write(self, data: bytes) -> None:
        if self._writer is None:
            raise ConnectionError("not connected")
        if (wait := MIN_REQUEST_GAP - (time.monotonic() - self._last_activity)) > 0:
            await asyncio.sleep(wait)
        _LOGGER.debug("%s <- %s", self.host, data.hex())
        self._writer.write(data)
        await self._writer.drain()
        self._touch()

    async def _read_loop(self, reader: asyncio.StreamReader, decoder: StreamDecoder) -> None:
        try:
            while data := await reader.read(4096):
                self._touch()
                _LOGGER.debug("%s -> %s", self.host, data.hex())
                for message in decoder.feed(data):
                    self._dispatch(message)
        except OSError as err:
            _LOGGER.debug("%s read error: %r", self.host, err)
        self._closed_by_controller_at = time.monotonic()
        self._fail_waiters(ConnectionError("connection closed by controller"))

    def _dispatch(self, message: Frame | SFrameMessage) -> None:
        for predicate, future in self._waiters:
            if not future.done() and predicate(message):
                future.set_result(message)
                return
        _LOGGER.debug("%s unsolicited %r", self.host, message)

    def _fail_waiters(self, err: Exception) -> None:
        for _, future in self._waiters:
            if not future.done():
                future.set_exception(err)

    def _touch(self) -> None:
        self._last_activity = time.monotonic()


def _byte(value: int) -> int:
    return max(0, min(255, int(value)))


@dataclass(frozen=True)
class DiscoveredDevice:
    host: str
    name: str
    device_code: str
    category: int
    reserve: int
    hash_key: str | None


DISCOVERY_REQUEST = MAGIC + bytes((Command.DISCOVER, 0, 0, 0))


def parse_discovery_reply(data: bytes, host: str) -> DiscoveredDevice | None:
    if len(data) < 13 or data[: len(MAGIC)] != MAGIC or data[6] != Command.DISCOVER:
        return None
    n = int.from_bytes(data[7:10], "big")
    if n <= 0 or n != len(data) - 10:
        return None
    pos = 12
    fields: list[bytes] = []
    while pos < len(data) and len(fields) < 3:
        length = data[pos]
        fields.append(data[pos + 1 : pos + 1 + length])
        pos += 1 + length
    if len(fields) < 2:
        return None
    return DiscoveredDevice(
        host=host,
        device_code=fields[0].decode("latin-1"),
        name=fields[1].decode("utf-8", "replace"),
        category=data[10],
        reserve=data[11],
        hash_key=fields[2].decode("latin-1") if len(fields) > 2 else None,
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, DiscoveredDevice] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if device := parse_discovery_reply(data, addr[0]):
            self.found[device.host] = device
        elif data != DISCOVERY_REQUEST:
            _LOGGER.debug("Ignoring UDP from %s: %s", addr, data.hex())


async def discover(timeout: float = 3.0, broadcast: str = "255.255.255.255") -> list[DiscoveredDevice]:
    """Broadcast discovery requests for `timeout` seconds and return every controller that answered."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    try:
        sock.bind(("0.0.0.0", DISCOVERY_LISTEN_PORT))
    except OSError:
        _LOGGER.debug("UDP %d busy, listening on an ephemeral port", DISCOVERY_LISTEN_PORT)
        sock.bind(("0.0.0.0", 0))
    transport, protocol = await loop.create_datagram_endpoint(_DiscoveryProtocol, sock=sock)
    try:
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            transport.sendto(DISCOVERY_REQUEST, (broadcast, DISCOVERY_PORT))
            await asyncio.sleep(min(1.0, max(0.0, deadline - loop.time())))
    finally:
        transport.close()
    return list(protocol.found.values())
