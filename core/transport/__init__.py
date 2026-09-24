"""
Byte transports: where telemetry bytes come from and where commands go.

Everything here is Qt-free. A transport is driven by `ReaderThread`, which does blocking
reads on its own thread, so a busy GUI or engine thread can never delay draining the OS
buffer (P5, ADR-0002).
"""

from core.transport.base import Transport, TransportError
from core.transport.reader import ReaderThread
from core.transport.serial_transport import SerialTransport
from core.transport.sim_transport import SIM_PORT_NAME, SimTransport

__all__ = [
    "SIM_PORT_NAME",
    "ReaderThread",
    "SerialTransport",
    "SimTransport",
    "Transport",
    "TransportError",
]
