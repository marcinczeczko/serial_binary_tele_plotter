"""
Protocol Handler Module.

`ProtocolHandler` is the single-stream convenience API: framing via `FrameParser`, then
per-frame decoding into dicts for the one configured stream. It also encodes the command
packets sent to the MCU. The engine's multi-stream path uses `FrameParser` +
`StreamRouter` directly.
"""

from __future__ import annotations

import struct
from collections.abc import Generator

from core.protocol.constants import (
    LOOP_CNTR_NAME,
    MAGIC_0,
    MAGIC_1,
    RTP_REQ_PID_ALL,
    RTP_REQ_PID_SINGLE,
)
from core.protocol.crc import calculate_crc8
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

    def create_pid_packet(
        self,
        motor_id: int,
        use_ramp: int,
        use_pi: int,
        kp: float,
        ki: float,
        k1: float,
        k2: float,
        k3: float,
        k_aw: float,
        alpha: float,
        rps: float,
    ) -> bytes:
        """
        Constructs a binary packet for PID configuration to be sent to the MCU.

        Structure:
        [Header: MAGIC0, MAGIC1, PID_REQ_ID, LEN] + [H_CRC] + [Payload] + [P_CRC]

        """
        payload = struct.pack(
            "<BffffffffBB", motor_id, kp, ki, k1, k2, k3, k_aw, alpha, rps, use_ramp, use_pi
        )

        h_base = struct.pack("BBBB", MAGIC_0, MAGIC_1, RTP_REQ_PID_SINGLE, len(payload))
        h_crc = calculate_crc8(h_base)
        p_crc = calculate_crc8(payload)

        return h_base + struct.pack("B", h_crc) + payload + struct.pack("B", p_crc)

    def create_pid_packet_all_motors(
        self,
        l_use_ramp: int,
        l_use_pi: int,
        l_kp: float,
        l_ki: float,
        l_k1: float,
        l_k2: float,
        l_k3: float,
        l_k_aw: float,
        l_alpha: float,
        l_rps: float,
        r_use_ramp: int,
        r_use_pi: int,
        r_kp: float,
        r_ki: float,
        r_k1: float,
        r_k2: float,
        r_k3: float,
        r_k_aw: float,
        r_alpha: float,
        r_rps: float,
    ) -> bytes:
        """
        Constructs a binary packet for PID configuration to be sent to the MCU for both motors
        """
        payload = struct.pack(
            "<ffffffffBBffffffffBB",
            l_kp,
            l_ki,
            l_k1,
            l_k2,
            l_k3,
            l_k_aw,
            l_alpha,
            l_rps,
            l_use_ramp,
            l_use_pi,
            r_kp,
            r_ki,
            r_k1,
            r_k2,
            r_k3,
            r_k_aw,
            r_alpha,
            r_rps,
            r_use_ramp,
            r_use_pi,
        )

        h_base = struct.pack("BBBB", MAGIC_0, MAGIC_1, RTP_REQ_PID_ALL, len(payload))
        h_crc = calculate_crc8(h_base)
        p_crc = calculate_crc8(payload)

        return h_base + struct.pack("B", h_crc) + payload + struct.pack("B", p_crc)
