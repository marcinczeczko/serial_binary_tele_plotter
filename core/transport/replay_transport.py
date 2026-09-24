"""
Replay of an `.sbtp` recording as a transport (R4.2).

The engine drives it like a serial port, with the same `ReaderThread`, parser, router,
time base and stores. So a replay reproduces the original session, including its CRC
errors and lost frames, and recordings double as end-to-end regression fixtures.

Pacing follows the recorded host timestamps divided by `speed`. `speed=None` means as
fast as the reader takes it. A replay can be paused and stepped one recorded read at a
time, and its speed changed on the fly. At the end, `read()` raises `ReplayEnded` (a
`TransportError`), which ends the session like an unplugged device, but is reported as
"finished".
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from core.recording.sbtp import CHUNK_HEADER_SIZE, RecordingHeader, RecordingReader
from core.transport.base import TransportError

REPLAY_PREFIX = "REPLAY:"
MAX_READ_BYTES = 64 * 1024  # at max speed, hand over at most this much per read


class ReplayEnded(TransportError):
    """The recording has been played to the end."""


class ReplayTransport:
    """
    Thread safety: `read()` runs on the reader thread. The controls (`set_speed`,
    `set_paused`, `step`) and `write()` run on the engine thread. They share one lock.
    """

    def __init__(
        self,
        path: str | Path,
        speed: float | None = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = Path(path)
        self._reader = RecordingReader(self.path)  # validates the file now
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._speed = speed
        self._paused = False
        self._steps = 0
        self._chunks: Iterator[tuple[int, bytes]] | None = None
        self._pending: tuple[int, bytes] | None = None
        self._ts0 = 0  # first chunk's timestamp
        self._wall0 = 0.0  # when (wall clock) the recording's ts0 is "due"
        self._last_ts = 0
        self.bytes_read = 0
        self.chunks_read = 0

    @property
    def name(self) -> str:
        return f"{REPLAY_PREFIX}{self.path.name}"

    @property
    def header(self) -> RecordingHeader:
        return self._reader.header

    @property
    def progress(self) -> float:
        """Fraction of the recorded data played so far (0..1)."""
        data = self._reader.size - self._reader.data_start
        return min(1.0, self.bytes_read / max(data, 1))

    @property
    def finished(self) -> bool:
        """Every recorded read has been played."""
        with self._lock:
            return self._chunks is not None and self._pending is None

    @property
    def truncated_bytes(self) -> int:
        return self._reader.truncated_bytes

    def open(self) -> None:
        with self._lock:
            self._chunks = self._reader.chunks()
            self._pending = next(self._chunks, None)
            if self._pending is not None:
                self._ts0 = self._last_ts = self._pending[0]
            self._wall0 = self._clock()

    def close(self) -> None:
        with self._lock:
            self._chunks = None
            self._pending = None

    # --- controls ------------------------------------------------------------------------

    def set_speed(self, speed: float | None) -> None:
        """Changes the pace from now on (None: as fast as possible)."""
        with self._lock:
            self._rebase_locked()
            self._speed = speed if speed is None or speed > 0 else None

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            if self._paused and not paused:
                self._rebase_locked()
            self._paused = paused

    def step(self, reads: int = 1) -> None:
        """While paused: lets the next `reads` recorded reads through."""
        with self._lock:
            self._steps += max(0, reads)

    def _rebase_locked(self) -> None:
        """Continues from the last played chunk as if it were due right now."""
        if self._speed:
            self._wall0 = self._clock() - (self._last_ts - self._ts0) / 1e9 / self._speed

    # --- Transport ---------------------------------------------------------------------

    def read(self, timeout_s: float) -> bytes:
        deadline = self._clock() + timeout_s
        while True:
            with self._lock:
                if self._chunks is None:
                    raise TransportError("replay closed")
                pending = self._pending
                if pending is None:
                    raise ReplayEnded("end of recording")
                now = self._clock()
                wait = 0.0
                if self._paused:
                    if self._steps > 0:
                        self._steps -= 1
                        self._rebase_locked()
                        return self._take_locked(max_bytes=0)
                    wait = timeout_s
                elif self._speed is not None:
                    due = self._wall0 + (pending[0] - self._ts0) / 1e9 / self._speed
                    wait = due - now
                if wait <= 0:
                    return self._take_locked(max_bytes=MAX_READ_BYTES if not self._speed else 0)
            if now >= deadline:
                return b""
            self._sleep(max(0.0, min(wait, deadline - now)))

    def _take_locked(self, max_bytes: int) -> bytes:
        """
        Pops the pending chunk. With `max_bytes`, also pops the following chunks, which are
        due at max speed, up to that size.
        """
        assert self._pending is not None and self._chunks is not None
        parts = [self._pending[1]]
        self._last_ts = self._pending[0]
        size = len(parts[0])
        self._pending = next(self._chunks, None)
        while max_bytes and self._pending is not None and size < max_bytes:
            parts.append(self._pending[1])
            self._last_ts = self._pending[0]
            size += len(self._pending[1])
            self._pending = next(self._chunks, None)
        self.chunks_read += len(parts)
        self.bytes_read += size + CHUNK_HEADER_SIZE * len(parts)
        return b"".join(parts)

    def write(self, data: bytes) -> None:
        raise TransportError("a replay has no device to send commands to")
