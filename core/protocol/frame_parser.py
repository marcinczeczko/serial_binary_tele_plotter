"""
Frame parser: turns a byte stream into CRC-valid `(stream_id, payload)` frames, for all IDs.

Decoding happens elsewhere (`RecordDecoder` / `StreamRouter`), so one parser serves every
stream multiplexed on the link (A1).

Buffer invariant: after `frames()` has been fully consumed, the buffer holds less than
`MAX_FRAME_LEN` bytes (at most one incomplete frame), however large the chunks passed to
`add_data()` were. Nothing is dropped without being counted in `stats` (C1, C13).
"""

from __future__ import annotations

from collections.abc import Iterator

from core.protocol.constants import MAGIC_0, MAGIC_1
from core.protocol.crc import calculate_crc8
from core.protocol.stats import LinkStats

HEADER_LEN = 5  # MAGIC0, MAGIC1, TYPE, LEN, H_CRC
MIN_FRAME_LEN = HEADER_LEN + 1  # empty payload + P_CRC
MAX_FRAME_LEN = HEADER_LEN + 255 + 1
_MAGIC = bytes([MAGIC_0, MAGIC_1])
# CRC of 4-byte headers. Bounded: only headers starting with the magic pair are checked,
# so at most 256 types x 256 lengths entries.
_HEADER_CRC: dict[bytes, int] = {}


class FrameParser:
    def __init__(self, stats: LinkStats | None = None) -> None:
        self.rx_buffer = bytearray()
        self.stats = stats if stats is not None else LinkStats()

    def reset(self, stats: LinkStats | None = None) -> None:
        """Drops buffered bytes and starts new statistics (e.g. on a new connection)."""
        self.rx_buffer.clear()
        self.stats = stats if stats is not None else LinkStats()

    def add_data(self, data: bytes) -> None:
        self.stats.bytes_rx += len(data)
        self.rx_buffer.extend(data)

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        """Adds a chunk and returns every frame completed by it."""
        self.add_data(data)
        return list(self.frames())

    def frames(self) -> Iterator[tuple[int, bytes]]:
        """Yields `(stream_id, payload)` for each complete, CRC-valid frame in the buffer."""
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
            #    Headers repeat for every frame of a stream, so their CRC is memoised.
            header = bytes(buf[:4])
            h_crc = _HEADER_CRC.get(header)
            if h_crc is None:
                h_crc = _HEADER_CRC[header] = calculate_crc8(header)
            if h_crc != buf[4]:
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
            yield p_type, payload
