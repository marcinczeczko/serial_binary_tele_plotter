import sys
from types import ModuleType

import pytest


class _DummySignal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args, **kwargs):
        for fn in list(self._slots):
            fn(*args, **kwargs)


class _SignalDescriptor:
    def __init__(self):
        self._name = f"_sig_{id(self)}"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        sig = instance.__dict__.get(self._name)
        if sig is None:
            sig = _DummySignal()
            instance.__dict__[self._name] = sig
        return sig


def _pyqt_signal(*_args, **_kwargs):
    return _SignalDescriptor()


def _pyqt_slot(*_args, **_kwargs):
    def _decorator(fn):
        return fn

    return _decorator


class _QObject:
    def __init__(self, parent=None):
        self._parent = parent


class _QWidget:
    def __init__(self, *args, **kwargs):
        pass


class _QLayout:
    def __init__(self, *args, **kwargs):
        pass

    def setContentsMargins(self, *args, **kwargs):
        pass

    def setSpacing(self, *args, **kwargs):
        pass

    def addWidget(self, *args, **kwargs):
        pass

    def addLayout(self, *args, **kwargs):
        pass


class _QTimer:
    def __init__(self, parent=None):
        self._parent = parent
        self._active = False
        self._interval = 0
        self.timeout = _DummySignal()

    def start(self, interval=None):
        if interval is not None:
            self._interval = int(interval)
        self._active = True

    def stop(self):
        self._active = False

    def isActive(self):
        return self._active

    def setInterval(self, interval):
        self._interval = int(interval)

    @staticmethod
    def singleShot(_ms, fn):
        fn()


class _Qt:
    class ConnectionType:
        QueuedConnection = 0

    class PenStyle:
        DashLine = 0

    class ToolButtonStyle:
        ToolButtonTextBesideIcon = 0

    class ArrowType:
        RightArrow = 0
        DownArrow = 1

    class MouseButton:
        LeftButton = 0

    class AlignmentFlag:
        AlignCenter = 0


def install_pyqt6_stub() -> None:
    if "PyQt6" in sys.modules:
        return

    pyqt6 = ModuleType("PyQt6")
    qtcore = ModuleType("PyQt6.QtCore")
    qtwidgets = ModuleType("PyQt6.QtWidgets")
    qtgui = ModuleType("PyQt6.QtGui")

    qtcore.QObject = _QObject
    qtcore.QTimer = _QTimer
    qtcore.pyqtSignal = _pyqt_signal
    qtcore.pyqtSlot = _pyqt_slot
    qtcore.Qt = _Qt

    qtwidgets.QWidget = _QWidget
    qtwidgets.QVBoxLayout = _QLayout
    qtwidgets.QHBoxLayout = _QLayout

    pyqt6.QtCore = qtcore
    pyqt6.QtWidgets = qtwidgets
    pyqt6.QtGui = qtgui

    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore
    sys.modules["PyQt6.QtWidgets"] = qtwidgets
    sys.modules["PyQt6.QtGui"] = qtgui

    if "pyqtgraph" not in sys.modules:
        pyqtgraph = ModuleType("pyqtgraph")

        class _Dummy:
            def __init__(self, *args, **kwargs):
                pass

            def __getattr__(self, _name):
                return _Dummy()

            def __call__(self, *args, **kwargs):
                return _Dummy()

        pyqtgraph.GraphicsLayoutWidget = _Dummy
        pyqtgraph.PlotItem = _Dummy
        pyqtgraph.PlotDataItem = _Dummy
        pyqtgraph.InfiniteLine = _Dummy
        pyqtgraph.TextItem = _Dummy

        def _mkpen(*_args, **_kwargs):
            return None

        pyqtgraph.mkPen = _mkpen
        sys.modules["pyqtgraph"] = pyqtgraph


@pytest.fixture
def pyqt_stub():
    install_pyqt6_stub()
    yield
