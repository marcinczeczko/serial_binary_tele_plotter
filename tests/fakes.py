"""Test doubles shared across test modules."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque

from core.transport import TransportError


class FakeTransport:
    """
    In-memory transport: serves queued chunks, then idles (or fails) like a real port.

    `fail_when_drained` makes read() raise once every chunk has been served, which
    simulates a device being unplugged mid-session.
    """

    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        fail_open: bool = False,
        fail_when_drained: bool = False,
        fail_writes: bool = False,
    ) -> None:
        self._chunks: deque[bytes] = deque(chunks or [])
        self.fail_open = fail_open
        self.fail_when_drained = fail_when_drained
        self.fail_writes = fail_writes
        self.opened = False
        self.closed = False
        self.written: list[bytes] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "FAKE"

    def open(self) -> None:
        if self.fail_open:
            raise TransportError("no such device")
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def read(self, timeout_s: float) -> bytes:
        with self._lock:
            if self._chunks:
                return self._chunks.popleft()
        if self.fail_when_drained:
            raise TransportError("device disconnected")
        time.sleep(timeout_s)
        return b""

    def push(self, *chunks: bytes) -> None:
        """Queues more data for the reader (thread-safe), as if it had just arrived."""
        with self._lock:
            self._chunks.extend(chunks)

    def write(self, data: bytes) -> None:
        if self.fail_writes:
            raise TransportError("write timeout")
        self.written.append(data)


_APP: list[object] = []


def _pump_qt_events() -> None:
    """
    Delivers queued Qt events when real PyQt6 is loaded.

    With real Qt, a signal emitted on a worker thread is queued to its receiver's thread
    (here, the test's main thread) and only runs when events are processed. With the
    stub, signals are direct calls and there's nothing to pump.
    """
    widgets = sys.modules.get("PyQt6.QtWidgets")
    app_cls = getattr(widgets, "QApplication", None)
    if app_cls is None:
        return
    if app_cls.instance() is None:
        # A full QApplication (not QCoreApplication), so later qtbot tests can reuse it.
        _APP.append(app_cls([]))
    app_cls.processEvents()


def wait_for(predicate: object, timeout_s: float = 3.0) -> bool:
    """Polls `predicate()` until true or timeout, pumping Qt events if Qt is real."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _pump_qt_events()
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.005)
    return False
