"""Lights that follow a song a Home Assistant speaker plays: the song is decoded and cut into the frequency bands the phone app streams."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from homeassistant.components import media_source
from homeassistant.const import STATE_PLAYING
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .trimlight import MUSIC_BANDS, TrimlightError

if TYPE_CHECKING:
    from .coordinator import TrimlightCoordinator

_LOGGER = logging.getLogger(__name__)

FRAME_SECONDS = 0.1
SAMPLE_RATE = 22050
FFT_SIZE = 2048
# The phone app's band edges for its microphone source.
BAND_EDGES_HZ = (50, 100, 160, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000)
# The phone app sends no frame unless a band reaches this.
QUIET_LEVEL = 10
DYNAMIC_RANGE_DB = 48.0
MAX_SECONDS = 20 * 60
# A web stream, such as a radio station, never ends; decoding gives up after this long.
DECODE_TIMEOUT = 120
CACHED_SONGS = 4
DELAY_RANGE_MS = (-1000, 3000)


class MusicError(HomeAssistantError):
    """A song that can't be followed: Home Assistant can't read it, or it isn't a song."""


@dataclass(frozen=True)
class FollowSettings:
    on: bool = False
    speaker: str | None = None
    delay_ms: int = 0
    effect: int = 0
    color: tuple[int, int, int] = (255, 0, 0)
    length: int = 30
    sensitivity: int = 13

    @classmethod
    def from_dict(cls, value: object) -> FollowSettings:
        if not isinstance(value, dict):
            return cls()
        known = {k: v for k, v in value.items() if k in cls.__dataclass_fields__}
        if "color" in known:
            known["color"] = tuple(known["color"])
        try:
            return cls(**known)
        except TypeError:
            return cls()


def song_source(hass: HomeAssistant, content_id: str | None) -> str | None:
    """Where ffmpeg can read the song a speaker plays: a file in Home Assistant's media folders, or a web address."""
    if not content_id:
        return None
    if media_source.is_media_source_id(content_id):
        domain, _, identifier = content_id.removeprefix(media_source.URI_SCHEME).partition("/")
        return _media_file(hass, identifier) if domain == media_source.DOMAIN else None
    parsed = urlparse(content_id)
    if parsed.path.startswith("/media/") and (local := _media_file(hass, unquote(parsed.path.removeprefix("/media/")))):
        return local
    return content_id if parsed.scheme in ("http", "https") else None


def _media_file(hass: HomeAssistant, identifier: str) -> str | None:
    source_dir, _, location = identifier.partition("/")
    base = hass.config.media_dirs.get(source_dir)
    if base is None or not location:
        return None
    path = Path(base, location).resolve()
    if not path.is_relative_to(Path(base).resolve()):
        return None
    return str(path)


async def async_decode(binary: str, source: str) -> bytes:
    """The song as mono 16-bit samples at SAMPLE_RATE."""
    process = await asyncio.create_subprocess_exec(
        binary, "-nostdin", "-v", "error", "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-i", source, "-t", str(MAX_SECONDS), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        pcm, errors = await asyncio.wait_for(process.communicate(), DECODE_TIMEOUT)
    except TimeoutError as err:
        process.kill()
        await process.wait()
        raise MusicError("It doesn't end, so it's probably a stream such as a radio station") from err
    if process.returncode != 0 or not pcm:
        raise MusicError(f"ffmpeg couldn't read it: {errors.decode(errors='replace').strip()[-200:] or 'no sound'}")
    return pcm


def band_frames(pcm: bytes) -> list[bytes]:
    """One frame of MUSIC_BANDS levels (0-255) for every FRAME_SECONDS of the song."""
    import numpy as np  # noqa: PLC0415

    samples = np.frombuffer(pcm, "<i2").astype(np.float32) / 32768.0
    hop = int(SAMPLE_RATE * FRAME_SECONDS)
    count = max(1, len(samples) // hop)
    padded = np.concatenate([samples, np.zeros(FFT_SIZE, np.float32)])
    starts = np.arange(count) * hop
    windows = padded[starts[:, None] + np.arange(FFT_SIZE)[None, :]] * np.hanning(FFT_SIZE).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(windows, axis=1))
    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / SAMPLE_RATE)
    edges = BAND_EDGES_HZ
    bands = np.stack([spectrum[:, (freqs >= lo) & (freqs < hi)].mean(axis=1) for lo, hi in zip(edges[:-1], edges[1:])], axis=1)
    return [bytes(row) for row in _levels(bands)]


def _levels(bands: Any) -> Any:
    """Band magnitudes to bytes: decibels below the song's loudest moments, over DYNAMIC_RANGE_DB."""
    import numpy as np  # noqa: PLC0415

    db = 20 * np.log10(bands + 1e-9)
    top = np.percentile(db, 99.5)
    scaled = (db - (top - DYNAMIC_RANGE_DB)) / DYNAMIC_RANGE_DB
    return np.clip(scaled * 255, 0, 255).astype(np.uint8)


def speaker_position(state: State, now: datetime) -> float | None:
    """Seconds into the song the speaker is, counting on from its last report while it plays."""
    position = state.attributes.get("media_position")
    if position is None:
        return None
    updated = state.attributes.get("media_position_updated_at")
    if isinstance(updated, str):
        updated = dt_util.parse_datetime(updated)
    if state.state == STATE_PLAYING and isinstance(updated, datetime):
        return float(position) + (now - updated).total_seconds()
    return float(position)


class MusicFollower:
    """Follows one speaker while switched on: its songs drive the music effect, and when it stops the lights go back."""

    def __init__(self, coordinator: TrimlightCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.settings = FollowSettings()
        self.status = "off"
        self.detail: str | None = None
        self._unsub: Any = None
        self._lock = asyncio.Lock()
        self._stream: asyncio.Task | None = None
        self._content: str | None = None
        self._frames: list[bytes] = []
        self._started: tuple[float, float] | None = None
        self._skip: str | None = None
        self._cache: OrderedDict[str, list[bytes]] = OrderedDict()

    @property
    def following(self) -> bool:
        return self._stream is not None and not self._stream.done()

    def summary(self) -> dict[str, Any]:
        speaker = self.hass.states.get(self.settings.speaker) if self.settings.speaker else None
        return {
            **asdict(self.settings),
            "color": list(self.settings.color),
            "status": self.status,
            "detail": self.detail,
            "title": speaker.attributes.get("media_title") if speaker is not None and self.status == "following" else None,
            "delay_range": list(DELAY_RANGE_MS),
        }

    @callback
    def async_start(self) -> None:
        self._track()

    async def async_set(self, **changes: Any) -> None:
        settings = replace(self.settings, **changes)
        if settings.on and not settings.speaker:
            raise ServiceValidationError("Pick the speaker to follow on the Trimlight page's Music tab first")
        if not DELAY_RANGE_MS[0] <= settings.delay_ms <= DELAY_RANGE_MS[1]:
            raise ServiceValidationError(f"The delay is {DELAY_RANGE_MS[0]} to {DELAY_RANGE_MS[1]} ms")
        restart = (settings.on, settings.speaker) != (self.settings.on, self.settings.speaker)
        look = (settings.effect, settings.color, settings.length, settings.sensitivity) != (self.settings.effect, self.settings.color, self.settings.length, self.settings.sensitivity)
        self.settings = settings
        self.coordinator.save_settings()
        if restart:
            self._skip = None
            await self._async_stop(restore=True)
            self._track()
        elif look and self.following:
            await self._async_send_settings()
        self.coordinator.async_update_listeners()

    @callback
    def async_shutdown(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._stream is not None:
            self._stream.cancel()

    @callback
    def user_took_over(self) -> None:
        """Someone changed the lights during a song: their change stands until the next song."""
        if self._stream is not None:
            self._stream.cancel()
            self._stream = None
        self._skip = self._content
        self.status = "waiting"
        self.detail = "You changed the lights, so this song isn't followed"

    def _track(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if not self.settings.on or not self.settings.speaker:
            self.status, self.detail = "off", None
            return
        self._unsub = async_track_state_change_event(self.hass, [self.settings.speaker], self._speaker_changed)
        self._speaker_changed(None)

    @callback
    def _speaker_changed(self, _: Event[EventStateChangedData] | None) -> None:
        self.coordinator.config_entry.async_create_background_task(self.hass, self._async_follow(), "trimlight_local follow music")

    async def _async_follow(self) -> None:
        async with self._lock:
            state = self.hass.states.get(self.settings.speaker) if self.settings.on and self.settings.speaker else None
            content = state.attributes.get("media_content_id") if state is not None and state.state == STATE_PLAYING else None
            if content is None:
                self._skip = None
                if self.settings.on:
                    self.status, self.detail = "waiting", None
                await self._async_stop(restore=True)
                self.coordinator.async_update_listeners()
                return
            if content == self._skip:
                return
            if content != self._content or not self._frames:
                self._content = content
                self._frames = []
                self.status, self.detail = "loading", None
                self.coordinator.async_update_listeners()
                try:
                    self._frames = await self._async_frames(content)
                except MusicError as err:
                    self.status, self.detail = "unsupported", str(err)
                    await self._async_stop(restore=True)
                    self.coordinator.async_update_listeners()
                    return
            self._started = (asyncio.get_running_loop().time(), speaker_position(state, dt_util.utcnow()) or 0.0)
            if not self.following:
                try:
                    await self.coordinator.async_begin_music(self.settings)
                except HomeAssistantError as err:
                    self.status, self.detail = "error", str(err)
                    self.coordinator.async_update_listeners()
                    return
                self._stream = self.coordinator.config_entry.async_create_background_task(self.hass, self._async_stream(), "trimlight_local music stream")
            self.status, self.detail = "following", None
            self.coordinator.async_update_listeners()

    async def _async_frames(self, content: str) -> list[bytes]:
        if (frames := self._cache.get(content)) is not None:
            self._cache.move_to_end(content)
            return frames
        source = song_source(self.hass, content)
        if source is None:
            raise MusicError("Home Assistant can't read what it plays; music from Spotify or a radio app doesn't share its sound")
        pcm = await async_decode(self.coordinator.ffmpeg_binary(), source)
        frames = await self.hass.async_add_executor_job(band_frames, pcm)
        self._cache[content] = frames
        while len(self._cache) > CACHED_SONGS:
            self._cache.popitem(last=False)
        return frames

    def _position(self) -> float:
        state = self.hass.states.get(self.settings.speaker) if self.settings.speaker else None
        reported = speaker_position(state, dt_util.utcnow()) if state is not None else None
        if reported is not None:
            return reported
        started_at, started_from = self._started or (asyncio.get_running_loop().time(), 0.0)
        return started_from + asyncio.get_running_loop().time() - started_at

    async def _async_stream(self) -> None:
        loop = asyncio.get_running_loop()
        tick = loop.time()
        silent = bytes(MUSIC_BANDS)
        try:
            while True:
                index = int((self._position() - self.settings.delay_ms / 1000) / FRAME_SECONDS)
                bands = self._frames[index] if 0 <= index < len(self._frames) else silent
                if max(bands) >= QUIET_LEVEL:
                    await self.coordinator.client.stream_music(self.settings.effect, self.settings.color, self.settings.length, self.settings.sensitivity, bands)
                tick += FRAME_SECONDS
                await asyncio.sleep(max(0.0, tick - loop.time()))
        except TrimlightError as err:
            _LOGGER.warning("Stopped following the music: %s", err)
            self.status, self.detail = "error", str(err)
            self._stream = None
            await self.coordinator.async_end_music(restore=True)
            self.coordinator.async_update_listeners()

    async def _async_send_settings(self) -> None:
        await self.coordinator.async_begin_music(self.settings)

    async def _async_stop(self, *, restore: bool) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
            await self.coordinator.async_end_music(restore=restore)
