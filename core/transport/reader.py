"""Dedicated reader thread: drains a transport with blocking reads."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from core.transport.base import Transport, TransportError

logger = logging.getLogger(__name__)


class ReaderThread(threading.Thread):
    """
    Reads `transport` until stopped and hands each chunk to `on_data`.

    Callbacks run on this thread: `on_data` must be quick and thread-safe (the engine
    parses and stores under a short lock). `on_error` is called at most once, when the
    transport fails; the thread then exits. It is not called for a requested stop.
    """

    def __init__(
        self,
        transport: Transport,
        on_data: Callable[[bytes], None],
        on_error: Callable[[str], None],
        read_timeout_s: float = 0.05,
    ) -> None:
        super().__init__(name=f"reader:{transport.name}", daemon=True)
        self._transport = transport
        self._on_data = on_data
        self._on_error = on_error
        self._read_timeout_s = read_timeout_s
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                data = self._transport.read(self._read_timeout_s)
            except TransportError as e:
                if not self._stop_event.is_set():
                    self._on_error(str(e))
                return
            if data and not self._stop_event.is_set():
                try:
                    self._on_data(data)
                except Exception:  # a parser bug must not silently kill acquisition
                    logger.exception("Error while handling received data")
                    self._on_error("internal error while handling received data")
                    return

    def stop(self, timeout_s: float = 1.0) -> None:
        """Requests the loop to end and waits (reads time out every `read_timeout_s`)."""
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout_s)
            if self.is_alive():
                logger.warning("Reader thread %s did not stop within %.1f s", self.name, timeout_s)
