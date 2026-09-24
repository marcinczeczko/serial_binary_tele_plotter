"""pyserial-backed transport."""

from __future__ import annotations

import serial

from core.transport.base import TransportError

# A device that stops draining its RX buffer must not block the writer forever (C9).
SERIAL_WRITE_TIMEOUT_S = 0.2


class SerialTransport:
    """
    Serial port transport.

    `read()` asks for `max(1, in_waiting)` bytes: when idle it blocks until the first byte
    (or the timeout), when data is queued it drains everything already buffered in one call.
    """

    def __init__(self, port: str, baudrate: int) -> None:
        self.port = port
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None

    @property
    def name(self) -> str:
        return self.port

    def open(self) -> None:
        try:
            self._serial = serial.Serial(
                self.port, self.baudrate, timeout=0.05, write_timeout=SERIAL_WRITE_TIMEOUT_S
            )
            self._serial.reset_input_buffer()
        except (ValueError, serial.SerialException, OSError) as e:
            self._serial = None
            raise TransportError(str(e)) from e

    def close(self) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            try:
                port.close()
            except serial.SerialException, OSError:
                pass

    def read(self, timeout_s: float) -> bytes:
        port = self._serial
        if port is None:
            raise TransportError("port is not open")
        try:
            if port.timeout != timeout_s:
                port.timeout = timeout_s
            data: bytes = port.read(max(1, port.in_waiting))
        except (serial.SerialException, OSError, TypeError, AttributeError) as e:
            # TypeError/AttributeError: pyserial internals after a concurrent close().
            raise TransportError(str(e)) from e
        return data

    def write(self, data: bytes) -> None:
        port = self._serial
        if port is None:
            raise TransportError("port is not open")
        try:
            port.write(data)
        except (serial.SerialTimeoutException, serial.SerialException, OSError) as e:
            raise TransportError(f"write failed: {e}") from e
