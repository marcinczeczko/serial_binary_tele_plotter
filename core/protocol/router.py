"""
Multi-stream router (R2.2): dispatches CRC-valid frames to every configured stream.

Every configured stream is decoded all the time, whichever one the GUI shows (A1).
Several streams may share a `stream_id`:
- Streams with an identical layout are decoded once and fed to all of them (for example,
  two signal selections over the same frame).
- Streams with different layouts are told apart by payload size.

Frames with an unconfigured ID, or with no layout of matching size, are counted in the
link statistics, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.protocol.constants import LOOP_CNTR_NAME
from core.protocol.record_decoder import RecordDecoder
from core.protocol.stats import LinkStats
from core.types import StreamConfig


@dataclass
class _Route:
    decoder: RecordDecoder
    keys: list[str] = field(default_factory=list)
    last_counter: int | None = None


class StreamRouter:
    def __init__(self, stats: LinkStats | None = None) -> None:
        self.stats = stats if stats is not None else LinkStats()
        # stream_id -> payload size -> route
        self._routes: dict[int, dict[int, _Route]] = {}

    def configure(self, streams: dict[str, StreamConfig]) -> None:
        routes: dict[int, dict[int, _Route]] = {}
        for key, cfg in streams.items():
            frame = cfg["frame"]
            decoder = RecordDecoder(frame.get("endianness", "little"), frame["fields"])
            by_size = routes.setdefault(frame["stream_id"], {})
            route = by_size.get(decoder.size)
            if route is None:
                route = by_size[decoder.size] = _Route(decoder)
            elif route.decoder.dtype != decoder.dtype:
                # Same ID and size but a different layout: ambiguous. Validation warns; the
                # first definition wins, so the data is never decoded with two meanings.
                continue
            route.keys.append(key)
        self._routes = routes

    def reset_counters(self) -> None:
        for by_size in self._routes.values():
            for route in by_size.values():
                route.last_counter = None

    @property
    def stream_keys(self) -> list[str]:
        return [k for by_size in self._routes.values() for r in by_size.values() for k in r.keys]

    def route(self, frames: list[tuple[int, bytes]]) -> dict[str, np.ndarray]:
        """Decodes a batch of frames; returns one structured array per stream key."""
        stats = self.stats
        grouped: dict[tuple[int, int], list[bytes]] = {}
        for stream_id, payload in frames:
            by_size = self._routes.get(stream_id)
            if by_size is None:
                stats.unknown_id_frames += 1
                continue
            if len(payload) not in by_size:
                stats.size_mismatches += 1
                continue
            grouped.setdefault((stream_id, len(payload)), []).append(payload)

        out: dict[str, np.ndarray] = {}
        for (stream_id, size), payloads in grouped.items():
            route = self._routes[stream_id][size]
            records = route.decoder.decode_many(payloads)
            stats.frames_decoded += len(records)
            self._track_counter(route, records)
            for key in route.keys:
                out[key] = records
        return out

    def _track_counter(self, route: _Route, records: np.ndarray) -> None:
        names = records.dtype.names or ()
        if LOOP_CNTR_NAME not in names or not len(records):
            return
        # A plain loop: batches are small (a read's worth of frames), where numpy's per-call
        # overhead would dominate.
        stats = self.stats
        last = route.last_counter
        for value in records[LOOP_CNTR_NAME].tolist():
            if last is not None:
                step = value - last
                if step > 1:
                    stats.counter_gaps += 1
                    stats.counter_missing += step - 1
                elif step <= 0:
                    stats.counter_resets += 1
            last = value
        route.last_counter = last
