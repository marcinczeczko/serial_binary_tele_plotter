"""
Protocol Handler Module.

This module provides the `ProtocolHandler` class, which is responsible for the
low-level details of the binary communication protocol. It handles:
1. Buffering incoming raw bytes.
2. Synchronizing to the data stream (finding Magic Bytes).
3. Validating integrity via CRC8 (Header and Payload).
4. decoding binary payloads into Python dictionaries.
5. Encoding configuration commands back into binary frames.
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
from core.protocol.stats import LinkStats
from core.types import StreamConfig

HEADER_LEN = 5  # MAGIC0, MAGIC1, TYPE, LEN, H_CRC
MIN_FRAME_LEN = HEADER_LEN + 1  # empty payload + P_CRC
MAX_FRAME_LEN = HEADER_LEN + 255 + 1
_MAGIC = bytes([MAGIC_0, MAGIC_1])


class ProtocolHandler:
    """
    Manages the binary data stream logic.

    It encapsulates the Receive Buffer (`rx_buffer`) and the `FrameDecoder`.
    It acts as a stream parser: you feed it raw chunked bytes, and it yields
    complete, validated frames.

    Buffer invariant: after `process_available_frames()` has been fully consumed, the buffer
    holds less than `MAX_FRAME_LEN` bytes (at most one incomplete frame), however large the
    chunks passed to `add_data()` were. Nothing is ever dropped without being counted in
    `stats`.
    """

    def __init__(self) -> None:
        """Initializes the handler with an empty buffer."""
        self.rx_buffer: bytearray = bytearray()
        self.decoder: FrameDecoder | None = None
        self.active_stream_id: int | None = None
        self.stats: LinkStats = LinkStats()
        self._last_counter: int | None = None

    def reset(self) -> None:
        """Drops buffered bytes and zeroes the statistics (e.g. on a new connection)."""
        self.rx_buffer.clear()
        self.stats = LinkStats()
        self._last_counter = None

    def configure(self, stream_cfg: StreamConfig) -> None:
        """
        Configures the FrameDecoder based on the JSON stream definition.

        Args:
            stream_cfg (dict): Configuration dictionary containing the 'frame' section.
        """
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
        """
        Ingests raw bytes into the internal processing buffer.

        Args:
            data (bytes): Chunk of data read from the serial port.
        """
        self.stats.bytes_rx += len(data)
        self.rx_buffer.extend(data)

    def process_available_frames(self) -> Generator[dict[str, int | float]]:
        """
        Parses the internal buffer and yields all complete, valid frames found.

        This method implements a 'state machine' loop that:
        1. Synchronizes to Magic Bytes (0xAA 0x55).
        2. Validates the Header CRC.
        3. Waits until enough bytes are available for the Payload.
        4. Validates Payload CRC and decodes.

        Yields:
            dict: Decoded telemetry frame.
        """
        buf = self.rx_buffer
        stats = self.stats
        while True:
            if len(buf) < MIN_FRAME_LEN:
                break

            # 1. Synchronize: jump straight to the next magic pair.
            if buf[0] != MAGIC_0 or buf[1] != MAGIC_1:
                magic_offset = buf.find(_MAGIC)
                if magic_offset < 0:
                    # Keep the last byte: it may be the 0xAA of a pair split across reads.
                    skipped = len(buf) - 1 if buf[-1] == MAGIC_0 else len(buf)
                    stats.discarded_bytes += skipped
                    del buf[:skipped]
                    break
                stats.discarded_bytes += magic_offset
                del buf[:magic_offset]
                continue

            # 2. Header [MAGIC0][MAGIC1][TYPE][LEN][H_CRC]: must be valid before LEN is trusted.
            if calculate_crc8(bytes(buf[:4])) != buf[4]:
                stats.header_crc_errors += 1
                stats.discarded_bytes += 1
                del buf[0]  # re-sync from the next byte
                continue

            p_len = buf[3]
            frame_len = HEADER_LEN + p_len + 1
            if len(buf) < frame_len:
                break  # valid header, payload still in flight

            # 3. Consume the frame before validating it, so a bad frame can't stall the parser.
            p_type = buf[2]
            payload = bytes(buf[HEADER_LEN : HEADER_LEN + p_len])
            p_crc = buf[HEADER_LEN + p_len]
            del buf[:frame_len]

            if calculate_crc8(payload) != p_crc:
                stats.payload_crc_errors += 1
                continue

            stats.frames_by_id[p_type] = stats.frames_by_id.get(p_type, 0) + 1
            decoded = self._decode_payload(p_type, payload)
            if decoded is not None:
                stats.frames_decoded += 1
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
