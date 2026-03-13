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

import logging
import struct
from typing import Generator, Optional

from core.protocol.constants import (
    MAGIC_0,
    MAGIC_1,
    RTP_REQ_PID_ALL,
    RTP_REQ_PID_SINGLE,
)
from core.protocol.crc import calculate_crc8
from core.protocol.decoder import FrameDecoder
from core.types import StreamConfig

DEBUG_DECODE = False
TRACE_DECODE = False
DEBUG_DECODE_PAYLOAD = False

logger = logging.getLogger(__name__)


class ProtocolHandler:
    """
    Manages the binary data stream logic.

    It encapsulates the Receive Buffer (`rx_buffer`) and the `FrameDecoder`.
    It acts as a stream parser: you feed it raw chunked bytes, and it yields
    complete, validated frames.
    """

    def __init__(self) -> None:
        """Initializes the handler with an empty buffer."""
        self.rx_buffer: bytearray = bytearray()
        self.decoder: Optional[FrameDecoder] = None
        self.active_stream_id: Optional[int] = None

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

        if DEBUG_DECODE:
            logger.debug("[PROTO] Configured Stream ID: %s", self.active_stream_id)

    def add_data(self, data: bytes) -> None:
        """
        Ingests raw bytes into the internal processing buffer.

        Args:
            data (bytes): Chunk of data read from the serial port.
        """
        if TRACE_DECODE:
            logger.debug("[RX][BUF] +%s bytes", len(data))
        self.rx_buffer.extend(data)

    def process_available_frames(self) -> Generator[dict[str, int | float], None, None]:
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
        # Safety: Prevent infinite memory growth if sync is never found
        if len(self.rx_buffer) > 4096:
            if TRACE_DECODE:
                logger.warning("[RX] Buffer overflow (>4KB), clearing to reset sync.")
            self.rx_buffer.clear()
            return

        while True:
            # 1. Check for minimal frame size
            # (2 Magic + 1 Type + 1 Len + 1 HeaderCRC + 0 Payload + 1 PayloadCRC = 6 bytes)
            if len(self.rx_buffer) < 6:
                break

            # 2. Synchronization (Find Magic Bytes)
            if self.rx_buffer[0] != MAGIC_0 or self.rx_buffer[1] != MAGIC_1:
                # If first bytes aren't magic, drop one byte and retry (sliding window)
                del self.rx_buffer[0]
                continue

            # 3. Parse Header
            # Header structure: [MAGIC0][MAGIC1][TYPE][LEN][H_CRC]
            p_len = self.rx_buffer[3]
            h_crc = self.rx_buffer[4]

            header = bytes(self.rx_buffer[:4])

            # 4. Validate Header CRC
            if calculate_crc8(header) != h_crc:
                # Corrupted header: drop the first byte and try to re-sync
                del self.rx_buffer[0]
                continue

            # 5. Check Payload Availability
            # Full frame size = 5 bytes (Header+CRC) + p_len (Payload) + 1 byte (PayloadCRC)
            frame_len = 5 + p_len + 1

            if len(self.rx_buffer) < frame_len:
                # We have a valid header, but not enough data for the body yet.
                # Stop processing and wait for the next `add_data` call.
                break

            # 6. Extract Payload
            p_type = self.rx_buffer[2]
            payload = bytes(self.rx_buffer[5 : 5 + p_len])
            p_crc = self.rx_buffer[5 + p_len]

            # 7. Consume the bytes from the buffer
            # Important: Remove bytes BEFORE decoding. If CRC fails, we discard the frame
            # but we must advance the buffer to avoid getting stuck processing the same bytes.
            del self.rx_buffer[:frame_len]

            # 8. Validate Payload CRC and Decode
            if calculate_crc8(payload) == p_crc:
                decoded = self._decode_payload(p_type, payload)
                if decoded is not None:
                    yield decoded
            elif TRACE_DECODE:
                logger.debug("[RX][CRC] Payload CRC check failed")

    def _decode_payload(
        self, p_type: int, payload: bytes
    ) -> Optional[dict[str, int | float]]:
        """
        Decodes payload ONLY if p_type matches the active stream configuration.
        """
        if not self.decoder or self.active_stream_id is None:
            return None

        # Ensure we are processing the expected stream type
        if p_type != self.active_stream_id:
            return None

        # Double check size (though header validation usually covers this)
        if len(payload) != self.decoder.size:
            if DEBUG_DECODE:
                logger.debug(
                    "[RX][ERR] Size mismatch for ID %s. Got %s, expected %s",
                    p_type,
                    len(payload),
                    self.decoder.size,
                )
            return None
        else:
            if DEBUG_DECODE:
                logger.debug(
                    "[RX][OK] Size match for ID %s. Got %s, expected %s",
                    p_type,
                    len(payload),
                    self.decoder.size,
                )
        try:
            decoded = self.decoder.decode(payload)
            if DEBUG_DECODE_PAYLOAD:
                logger.debug("[RX][DATA] %s", decoded)
            return decoded
        except struct.error:
            return None

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
