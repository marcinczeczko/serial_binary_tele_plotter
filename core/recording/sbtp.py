"""
`.sbtp` raw recordings (R4.1): exactly the bytes a transport delivered, with host timestamps.

Layout (little endian):

    "SBTP"  u16 version  u32 header_len  header (UTF-8 JSON, header_len bytes)
    then repeated chunks:  u64 host_ts_ns  u32 length  bytes (length)

- The header records the stream config in use, so a recording can still be decoded after
  streams.json has changed. It also has the source name and the creation time.
- A chunk is one read from the transport, before any parsing. So a replay goes through the
  same framing, CRC checks, time base and statistics as the original session, including
  its errors.
- `host_ts_ns` is wall-clock time (`time.time_ns`). Replays pace chunks by their
  differences.

Files are opened exclusively (never overwritten). Writes are buffered, and the engine
flushes about once a second, so a crash loses at most about a second of data. A reader
tolerates a truncated last chunk (it counts it) instead of rejecting the file.

Pure Python, no Qt.
"""

from __future__ import annotations

import json
import struct
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"SBTP"
VERSION = 1
SUFFIX = ".sbtp"
_PREAMBLE = struct.Struct("<4sHI")
_CHUNK = struct.Struct("<QI")
CHUNK_HEADER_SIZE = _CHUNK.size
_BUFFER = 1 << 20


class RecordingError(Exception):
    """Not a recording, an unsupported version, or unreadable."""


@dataclass
class RecordingHeader:
    version: int
    created: str
    source: str
    streams: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> bytes:
        doc = {
            "created": self.created,
            "source": self.source,
            "streams": self.streams,
            **self.extra,
        }
        return json.dumps(doc, indent=1).encode("utf-8")


class RecordingWriter:
    """
    Appends chunks to a new `.sbtp` file. `write()` is called from the reader thread,
    `flush()` and `close()` from the engine thread, so they share a lock.
    """

    def __init__(
        self,
        path: str | Path,
        streams: dict[str, Any],
        source: str,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.path = Path(path)
        self._clock_ns = clock_ns
        self._lock = threading.Lock()
        self.chunks = 0
        self.bytes = 0
        header = RecordingHeader(
            VERSION, datetime.now(UTC).isoformat(timespec="seconds"), source, streams
        )
        body = header.to_json()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file: BinaryIO | None = open(self.path, "xb", buffering=_BUFFER)  # noqa: SIM115
        self._file.write(_PREAMBLE.pack(MAGIC, VERSION, len(body)) + body)

    def write(self, data: bytes, ts_ns: int | None = None) -> None:
        if not data:
            return
        with self._lock:
            if self._file is None:
                return
            stamp = self._clock_ns() if ts_ns is None else ts_ns
            self._file.write(_CHUNK.pack(stamp, len(data)))
            self._file.write(data)
            self.chunks += 1
            self.bytes += len(data)

    def flush(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


class RecordingReader:
    """Reads a `.sbtp` file: `header`, then `chunks()` in order."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.truncated_bytes = 0  # a last chunk cut short (e.g. the app was killed)
        try:
            with open(self.path, "rb") as f:
                pre = f.read(_PREAMBLE.size)
                if len(pre) < _PREAMBLE.size:
                    raise RecordingError("file is too short to be a recording")
                magic, version, header_len = _PREAMBLE.unpack(pre)
                if magic != MAGIC:
                    raise RecordingError("not an .sbtp recording (bad magic)")
                if version != VERSION:
                    raise RecordingError(f"unsupported recording version {version}")
                doc = json.loads(f.read(header_len).decode("utf-8"))
                self.data_start: int = _PREAMBLE.size + int(header_len)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise RecordingError(f"cannot read {self.path.name}: {e}") from e
        if not isinstance(doc, dict) or not isinstance(doc.get("streams"), dict):
            raise RecordingError("recording header has no stream configuration")
        known = {"created", "source", "streams"}
        self.header = RecordingHeader(
            version,
            str(doc.get("created", "")),
            str(doc.get("source", "")),
            doc["streams"],
            {k: v for k, v in doc.items() if k not in known},
        )
        self.size = self.path.stat().st_size

    def chunks(self) -> Iterator[tuple[int, bytes]]:
        """Yields (host_ts_ns, data) per recorded read."""
        with open(self.path, "rb") as f:
            f.seek(self.data_start)
            while True:
                head = f.read(_CHUNK.size)
                if not head:
                    return
                if len(head) < _CHUNK.size:
                    self.truncated_bytes += len(head)
                    return
                ts_ns, length = _CHUNK.unpack(head)
                data = f.read(length)
                if len(data) < length:
                    self.truncated_bytes += _CHUNK.size + len(data)
                    return
                yield ts_ns, data


def recording_name(label: str, when: datetime | None = None) -> str:
    """A file name like `pid_20260924-181502.sbtp` (label sanitised for any OS)."""
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label) or "recording"
    return f"{safe}_{stamp}{SUFFIX}"
