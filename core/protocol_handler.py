"""
Background worker module for telemetry data processing.

This module handles the core logic of the application running in a separate thread.
It coordinates data ingestion from Serial/Virtual sources, delegates parsing to
ProtocolHandler, and storage to SignalDataManager.
"""

import struct
from typing import Generator, Optional

from core.frame_decoder import FrameDecoder
from core.protocol import MAGIC_0, MAGIC_1, RTP_REQ_PID, calculate_crc8

DEBUG_DECODE = False
TRACE_DECODE = False


class ProtocolHandler:
    """
    Handles the low-level byte stream parsing, CRC validation, and Frame Decoding.
    It encapsulates the Rx buffer and the FrameDecoder state.
    """

    def __init__(self):
        self.rx_buffer = bytearray()
        self.decoder: Optional[FrameDecoder] = None
        self.active_stream_id: Optional[int] = None

    def configure(self, stream_cfg: dict):
        """Sets up the decoder based on the stream configuration."""
        if "frame" not in stream_cfg:
            raise ValueError("Stream config missing 'frame' definition")

        frame = stream_cfg["frame"]
        self.decoder = FrameDecoder(
            endian=frame.get("endianness", "little"),
            fields=frame["fields"],
        )
        self.active_stream_id = frame.get("stream_id")

    def add_data(self, data: bytes):
        """Ingests raw bytes into the internal buffer."""
        if TRACE_DECODE:
            print(f"[RX][BUF] +{len(data)} bytes")
        self.rx_buffer.extend(data)

    def process_available_frames(self) -> Generator[dict, None, None]:
        """
        Generates decoded frame dictionaries from the current buffer.
        Yields frames one by one as they are successfully parsed.
        """
        if len(self.rx_buffer) > 4096:
            if TRACE_DECODE:
                print(f"[RX][WARN] Buffer overflow, clearing")
            self.rx_buffer.clear()
            return

        while True:
            # 1. Minimal length check
            if len(self.rx_buffer) < 6:
                break

            # 2. Sync to MAGIC
            if self.rx_buffer[0] != MAGIC_0 or self.rx_buffer[1] != MAGIC_1:
                del self.rx_buffer[0]
                continue

            # 3. Header check
            p_len = self.rx_buffer[3]
            h_crc = self.rx_buffer[4]

            header = bytes(self.rx_buffer[:4])
            if calculate_crc8(header) != h_crc:
                del self.rx_buffer[0]
                continue

            # 4. Payload availability check
            frame_len = 5 + p_len + 1
            if len(self.rx_buffer) < frame_len:
                break  # Wait for more data

            # 5. Extract and Validate Payload
            p_type = self.rx_buffer[2]
            payload = bytes(self.rx_buffer[5 : 5 + p_len])
            p_crc = self.rx_buffer[5 + p_len]

            # Consume the raw bytes
            del self.rx_buffer[:frame_len]

            if calculate_crc8(payload) == p_crc:
                decoded = self._decode_payload(p_type, payload)
                if decoded:
                    yield decoded
            elif TRACE_DECODE:
                print("[RX][CRC] Payload CRC error")

    def _decode_payload(self, p_type: int, payload: bytes) -> Optional[dict]:
        """Internal helper to decode validated bytes using FrameDecoder."""
        if not self.decoder or self.active_stream_id is None:
            return None
        if p_type != self.active_stream_id:
            return None
        if len(payload) != self.decoder.size:
            return None

        try:
            return self.decoder.decode(payload)
        except struct.error:
            return None

    def create_pid_packet(self, ramp_type, motor_id, kp, ki, kff, alpha, rps) -> bytes:
        """Constructs a binary packet for PID configuration."""
        payload = struct.pack("<BfffffB", motor_id, kp, ki, kff, alpha, rps, ramp_type)
        h_base = struct.pack("BBBB", MAGIC_0, MAGIC_1, RTP_REQ_PID, len(payload))
        h_crc = calculate_crc8(h_base)
        p_crc = calculate_crc8(payload)
        return h_base + struct.pack("B", h_crc) + payload + struct.pack("B", p_crc)
