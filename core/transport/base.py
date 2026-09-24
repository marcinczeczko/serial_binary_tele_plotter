"""Transport contract shared by serial, simulated and replay sources."""

from __future__ import annotations

from typing import Protocol


class TransportError(Exception):
    """An I/O failure that ends the session (device unplugged, port closed, ...)."""


class Transport(Protocol):
    """
    A bidirectional byte pipe.

    Threading contract: `read()` is called only from the reader thread; `write()` may be
    called concurrently from another thread; `open()`/`close()` are called by the owner
    while no read is in progress (close() may also be used to unblock a pending read).
    """

    @property
    def name(self) -> str: ...

    def open(self) -> None:
        """Opens the transport. Raises TransportError on failure."""
        ...

    def close(self) -> None:
        """Closes the transport. Idempotent; never raises."""
        ...

    def read(self, timeout_s: float) -> bytes:
        """
        Blocks until at least one byte is available or `timeout_s` elapses.

        Returns the available bytes (possibly many), or b"" on timeout. Raises
        TransportError when the transport has failed.
        """
        ...

    def write(self, data: bytes) -> None:
        """Writes all of `data`. Raises TransportError (including on write timeout)."""
        ...
