"""
Protocol Handler Module.

`ProtocolHandler` is the single-stream convenience API: framing via `FrameParser`, then
per-frame decoding into dicts for the one configured stream. The engine's multi-stream path
uses `FrameParser` + `StreamRouter` directly; commands sent to the MCU are encoded by
`core.protocol.commands` from their config definitions.
"""

from __future__ import annotations

import struct
from collections.abc import Generator

from core.protocol.constants import LOOP_CNTR_NAME
from core.protocol.decoder import FrameDecoder
from core.protocol.frame_parser import HEADER_LEN, MAX_FRAME_LEN, MIN_FRAME_LEN, FrameParser
from core.protocol.stats import LinkStats
from core.types import StreamConfig

__all__ = ["HEADER_LEN", "MAX_FRAME_LEN", "MIN_FRAME_LEN", "ProtocolHandler"]


class ProtocolHandler:
    """
    Feed it raw chunked bytes; it yields decoded frames of the configured stream.

    Frames of other stream IDs are counted (`stats.unknown_id_frames`), not decoded. The
    buffer bound and "nothing dropped silently" guarantees are the `FrameParser`'s.
    """

    def __init__(self) -> None:
        self.parser = FrameParser()
        self.decoder: FrameDecoder | None = None
        self.active_stream_id: int | None = None
        self._last_counter: int | None = None

    @property
    def stats(self) -> LinkStats:
        return self.parser.stats

    @property
    def rx_buffer(self) -> bytearray:
        return self.parser.rx_buffer

    def reset(self) -> None:
        """Drops buffered bytes and zeroes the statistics (e.g. on a new connection)."""
        self.parser.reset()
        self._last_counter = None

    def configure(self, stream_cfg: StreamConfig) -> None:
        """Builds the decoder from the stream's `frame` section."""
        if "frame" not in stream_cfg:
            raise ValueError("Stream config missing 'frame' definition")

        frame = stream_cfg["frame"]
        self.decoder = FrameDecoder(
            endian=frame.get("endianness", "little"),
            fields=frame["fields"],
        )
        self.active_stream_id = frame.get("stream_id")
        self._last_counter = None

    def add_data(self, data: bytes) -> None:
        """Ingests a chunk of raw bytes read from the transport."""
        self.parser.add_data(data)

    def process_available_frames(self) -> Generator[dict[str, int | float]]:
        """Yields every complete, valid frame of the configured stream, decoded to a dict."""
        for p_type, payload in self.parser.frames():
            decoded = self._decode_payload(p_type, payload)
            if decoded is not None:
                self.stats.frames_decoded += 1
                yield decoded

    def _decode_payload(self, p_type: int, payload: bytes) -> dict[str, int | float] | None:
        """Decodes a CRC-valid payload if it belongs to the active stream; counts it otherwise."""
        if not self.decoder or self.active_stream_id is None or p_type != self.active_stream_id:
            self.stats.unknown_id_frames += 1
            return None

        if len(payload) != self.decoder.size:
            self.stats.size_mismatches += 1
            return None

        try:
            decoded = self.decoder.decode(payload)
        except struct.error:  # unreachable while sizes match; kept as a guard
            self.stats.size_mismatches += 1
            return None

        self._track_counter(decoded)
        return decoded

    def _track_counter(self, decoded: dict[str, int | float]) -> None:
        counter = decoded.get(LOOP_CNTR_NAME)
        if counter is None:
            return
        value = int(counter)
        last = self._last_counter
        self._last_counter = value
        if last is None or value == last + 1:
            return
        if value > last:
            self.stats.counter_gaps += 1
            self.stats.counter_missing += value - last - 1
        else:
            self.stats.counter_resets += 1
