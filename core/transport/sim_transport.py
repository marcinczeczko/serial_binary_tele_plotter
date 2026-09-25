"""
Simulated device transport (R2.7, review finding A6).

`SimTransport` is the `VIRTUAL` port: a `Transport` that produces real protocol bytes for
one stream in real time. The engine drives it with the same `ReaderThread`, parser, router
and stores as a serial port, so the simulator exercises the whole receive path. Link
statistics count its bytes and frames too.

Commands written to it are framed and decoded like the firmware would: with the command
layouts from `streams.json` (R5.2), given by `set_commands`. PID gains change the simulated
motors (see `core.simulation.pid_motor`).

For a text profile (`set_text(True)`, R8.3) it prints the stream's pattern lines instead,
and ignores what it's sent: text commands are R8.5.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from core.protocol.commands import CommandDef
from core.protocol.frame_parser import FrameParser
from core.simulation.synth import FrameSynth
from core.transport.base import TransportError
from core.types import StreamConfig

SIM_PORT_NAME = "VIRTUAL"
# Hand out bytes at most every 10 ms, like a serial port delivering a buffer's worth, so a
# fast stream doesn't cost one read per frame.
MIN_READ_INTERVAL_S = 0.01
# If the reader falls further behind than this, the frames in between are skipped (as
# they'd be lost from a real device's buffer). The time base then shows them as a gap.
MAX_FRAMES_PER_READ = 2000


class SimTransport:
    """
    Thread safety: `read()` runs on the reader thread. `set_stream()` and `write()` may
    be called from the engine thread at the same time. All three share one lock.
    """

    def __init__(
        self,
        stream: StreamConfig,
        *,
        seed: int | None = None,
        start_frame: int = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        commands: Sequence[CommandDef] = (),
    ) -> None:
        self._lock = threading.Lock()
        self._command_defs = tuple(commands)
        self._seed = seed
        self._clock = clock
        self._sleep = sleep
        self._synth = FrameSynth(stream, seed)
        self._commands = FrameParser()
        self._open = False
        self._start_frame = start_frame  # e.g. to follow history a benchmark pre-filled
        self._k = 0  # next frame number; frames continue across stream switches
        self._t0 = 0.0  # when frame `_k0` was due
        self._k0 = 0
        self._last_read = 0.0
        self._text = False

    @property
    def name(self) -> str:
        return SIM_PORT_NAME

    @property
    def synth(self) -> FrameSynth:
        return self._synth

    def open(self) -> None:
        with self._lock:
            self._open = True
            self._k = self._k0 = self._start_frame
            self._t0 = self._last_read = self._clock()

    def close(self) -> None:
        with self._lock:
            self._open = False

    def set_commands(self, commands: Sequence[CommandDef]) -> None:
        """The command layouts written packets are decoded with."""
        with self._lock:
            self._command_defs = tuple(commands)

    def set_text(self, text: bool) -> None:
        """Prints text lines (a text profile) instead of binary frames."""
        with self._lock:
            self._text = text

    def set_stream(self, stream: StreamConfig) -> None:
        """Simulates another stream from now on, at that stream's period."""
        with self._lock:
            self._synth = FrameSynth(stream, self._seed)
            self._t0, self._k0 = self._clock(), self._k

    def read(self, timeout_s: float) -> bytes:
        deadline = self._clock() + timeout_s
        while True:
            with self._lock:
                if not self._open:
                    raise TransportError("simulator closed")
                now = self._clock()
                period = max(self._synth.period_s, 1e-6)
                # (+1e-9: a frame due exactly now must not be lost to float rounding)
                due = self._k0 + int((now - self._t0) / period + 1e-9) + 1 - self._k
                ready_at = max(self._last_read + MIN_READ_INTERVAL_S, now if due > 0 else 0.0)
                if due > 0 and now >= ready_at:
                    if due > MAX_FRAMES_PER_READ:
                        self._k += due - MAX_FRAMES_PER_READ
                        due = MAX_FRAMES_PER_READ
                    synth = self._synth
                    data = synth.lines(self._k, due) if self._text else synth.frames(self._k, due)
                    self._k += due
                    self._last_read = now
                    return data
                if due <= 0:
                    ready_at = max(ready_at, self._t0 + (self._k - self._k0) * period)
            if now >= deadline:
                return b""
            self._sleep(max(0.0, min(ready_at, deadline) - now))

    def write(self, data: bytes) -> None:
        with self._lock:
            if not self._open:
                raise TransportError("simulator closed")
            if self._text:
                return  # text commands arrive with R8.5
            for packet_id, payload in self._commands.feed(data):
                self._synth.apply_command(packet_id, payload, self._command_defs)
