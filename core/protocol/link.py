"""
The decoder slot of the receive path (R8.1, ADR-0010).

A `LinkDecoder` turns the bytes of one connection into records: one numpy structured
array per stream key, the shape `SampleStore.append_records` takes. The engine holds one
decoder and knows nothing else about the wire format, so a new format (text lines, R8.3)
is a new decoder, not a change to the transport, reader, stores or GUI.

Contract:
- `configure(streams)` sets the streams to decode; it may be called while connected.
- `reset()` starts a new connection: buffered bytes are dropped, statistics start again,
  and per-stream counter tracking forgets the previous session.
- `feed(data)` takes one chunk as it arrived and returns every record it completed.
  Anything it can't decode is counted in `stats`, never dropped silently (C13).
- Not thread-safe: the engine calls it under `_data_lock`.

Pure Python, no Qt.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

from core.protocol.frame_parser import FrameParser
from core.protocol.router import StreamRouter
from core.protocol.stats import LinkStats
from core.protocol.text_line import TextLineDecoder
from core.types import StreamConfig

BINARY = "binary"
TEXT = "text"


class LinkDecoder(Protocol):
    @property
    def stats(self) -> LinkStats: ...

    def configure(self, streams: dict[str, StreamConfig]) -> None: ...

    def reset(self) -> None: ...

    def feed(self, data: bytes) -> dict[str, np.ndarray]: ...


class BinaryFrameDecoder:
    """Binary frames (`0xAA 0x55` header, CRC): `FrameParser` then `StreamRouter`."""

    def __init__(self) -> None:
        self.parser = FrameParser()
        self.router = StreamRouter(self.parser.stats)

    @property
    def stats(self) -> LinkStats:
        return self.parser.stats

    def configure(self, streams: dict[str, StreamConfig]) -> None:
        self.router.configure(streams)

    def reset(self) -> None:
        self.parser.reset()
        self.router.stats = self.parser.stats
        self.router.reset_counters()

    def feed(self, data: bytes) -> dict[str, np.ndarray]:
        return self.router.route(self.parser.feed(data))


LINK_FORMATS: dict[str, Callable[[], LinkDecoder]] = {
    BINARY: BinaryFrameDecoder,
    TEXT: TextLineDecoder,  # R8.3
}


def make_link_decoder(fmt: str = BINARY) -> LinkDecoder:
    """The decoder for a profile's format; raises ValueError for an unknown one."""
    try:
        return LINK_FORMATS[fmt]()
    except KeyError:
        raise ValueError(
            f"unknown link format {fmt!r} (known: {', '.join(LINK_FORMATS)})"
        ) from None
