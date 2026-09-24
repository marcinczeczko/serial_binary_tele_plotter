from __future__ import annotations

import threading
from unittest import mock

import pytest
import serial

from core.transport import ReaderThread, SerialTransport, TransportError
from core.transport.serial_transport import SERIAL_WRITE_TIMEOUT_S
from tests.fakes import FakeTransport, wait_for


def _collect(transport: FakeTransport) -> tuple[ReaderThread, list[bytes], list[str]]:
    data: list[bytes] = []
    errors: list[str] = []
    reader = ReaderThread(transport, data.append, errors.append, read_timeout_s=0.01)
    return reader, data, errors


def test_reader_delivers_chunks_in_order_and_stops_quickly() -> None:
    chunks = [bytes([i]) * (i + 1) for i in range(50)]
    reader, data, errors = _collect(FakeTransport(chunks))
    reader.start()
    assert wait_for(lambda: len(data) == 50)
    reader.stop(timeout_s=1.0)
    assert not reader.is_alive()
    assert data == chunks
    assert errors == []


def test_reader_reports_transport_failure_once_and_exits() -> None:
    reader, data, errors = _collect(FakeTransport([b"ab"], fail_when_drained=True))
    reader.start()
    reader.join(2.0)
    assert not reader.is_alive()
    assert data == [b"ab"]
    assert errors == ["device disconnected"]


def test_reader_reports_handler_crash() -> None:
    def boom(_data: bytes) -> None:
        raise RuntimeError("parser bug")

    errors: list[str] = []
    reader = ReaderThread(FakeTransport([b"x"]), boom, errors.append, read_timeout_s=0.01)
    reader.start()
    reader.join(2.0)
    assert not reader.is_alive()
    assert errors and "internal error" in errors[0]


def test_reader_stop_from_its_own_callback_does_not_deadlock() -> None:
    holder: dict[str, ReaderThread] = {}
    done = threading.Event()

    def on_error(_msg: str) -> None:
        holder["r"].stop()  # the engine does this via stop_working() in the stub lane
        done.set()

    reader = ReaderThread(FakeTransport(fail_when_drained=True), lambda d: None, on_error)
    holder["r"] = reader
    reader.start()
    assert done.wait(2.0)


def test_serial_transport_opens_with_write_timeout() -> None:
    port = mock.MagicMock(in_waiting=0, timeout=0.05)
    with mock.patch("serial.Serial", return_value=port) as serial_cls:
        transport = SerialTransport("COM7", 230400)
        transport.open()
    serial_cls.assert_called_once_with(
        "COM7", 230400, timeout=0.05, write_timeout=SERIAL_WRITE_TIMEOUT_S
    )
    port.reset_input_buffer.assert_called_once()
    assert transport.name == "COM7"


def test_serial_transport_open_failure_raises_transport_error() -> None:
    with mock.patch("serial.Serial", side_effect=serial.SerialException("busy")):
        with pytest.raises(TransportError, match="busy"):
            SerialTransport("COM7", 115200).open()


def test_serial_transport_read_drains_waiting_bytes() -> None:
    port = mock.MagicMock(in_waiting=300, timeout=0.05)
    port.read.return_value = b"x" * 300
    with mock.patch("serial.Serial", return_value=port):
        transport = SerialTransport("COM7", 115200)
        transport.open()
        assert transport.read(0.05) == b"x" * 300
    port.read.assert_called_once_with(300)

    port.in_waiting = 0
    port.read.return_value = b""
    assert transport.read(0.05) == b""
    port.read.assert_called_with(1)  # idle: block for the first byte


def test_serial_transport_errors_become_transport_errors() -> None:
    port = mock.MagicMock(in_waiting=0, timeout=0.05)
    port.read.side_effect = serial.SerialException("unplugged")
    port.write.side_effect = serial.SerialTimeoutException("timeout")
    with mock.patch("serial.Serial", return_value=port):
        transport = SerialTransport("COM7", 115200)
        transport.open()
    with pytest.raises(TransportError, match="unplugged"):
        transport.read(0.05)
    with pytest.raises(TransportError, match="write failed"):
        transport.write(b"\x01")
    transport.close()
    transport.close()  # idempotent
    with pytest.raises(TransportError, match="not open"):
        transport.read(0.05)


@pytest.mark.skipif(not hasattr(__import__("os"), "openpty"), reason="needs a POSIX pty")
def test_serial_transport_over_a_real_pty() -> None:
    """End to end through pyserial on a pseudo-terminal: bursts, then unplug."""
    import os
    import tty

    from core.protocol.handler import ProtocolHandler
    from tests.test_acquisition_engine import _CFG_BYTES, _frames

    master, slave = os.openpty()
    tty.setraw(slave)
    transport = SerialTransport(os.ttyname(slave), 921600)
    transport.open()
    handler = ProtocolHandler()
    handler.configure(_CFG_BYTES)  # type: ignore[arg-type]
    decoded: list[dict[str, int | float]] = []
    errors: list[str] = []

    def on_data(data: bytes) -> None:
        handler.add_data(data)
        decoded.extend(handler.process_available_frames())

    reader = ReaderThread(transport, on_data, errors.append, read_timeout_s=0.02)
    reader.start()
    try:
        blob = _frames(2000)  # 14 kB: one burst, far above the old 4 KiB limit (C1)
        for i in range(0, len(blob), 4096):
            os.write(master, blob[i : i + 4096])
        assert wait_for(lambda: len(decoded) == 2000)
        assert handler.stats.errors == 0

        os.close(master)  # "unplug": reads on the slave now fail with EIO
        master = -1
        assert wait_for(lambda: bool(errors))
    finally:
        reader.stop()
        transport.close()
        os.close(slave)
        if master >= 0:
            os.close(master)
