"""
Shared test configuration.

Two kinds of tests live here:
- `qt`-marked tests run against real Qt through pytest-qt. The offscreen platform is used by
  default, so no window is shown and no display is needed.
- Everything else is Qt-free or uses the hand-written PyQt6/pyqtgraph stub below (the
  `pyqt_stub` fixture). The stub is only installed when real PyQt6 hasn't been imported.

On machines where Qt can't load (e.g. no libEGL), run `uv run pytest -p no:pytest-qt`: the
`qt` tests are then skipped and the rest run against the stub.
"""

import gc
import os
import sys
from collections.abc import Iterator
from types import ModuleType

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.pluginmanager.has_plugin("pytest-qt"):
        return
    skip_qt = pytest.mark.skip(reason="pytest-qt disabled or Qt unavailable")
    for item in items:
        if "qt" in item.keywords:
            item.add_marker(skip_qt)


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


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    """
    Real-Qt tests must never read or write the user's QSettings (recording folder, record
    on connect, last config). Qt isn't imported here: the stub lane depends on that.
    """
    qtcore = sys.modules.get("PyQt6.QtCore")
    settings_cls = getattr(qtcore, "QSettings", None)
    if settings_cls is None or not hasattr(settings_cls, "setPath"):
        return
    folder = str(tmp_path_factory.mktemp("qsettings"))
    for fmt in (settings_cls.Format.NativeFormat, settings_cls.Format.IniFormat):
        settings_cls.setPath(fmt, settings_cls.Scope.UserScope, folder)


@pytest.fixture(autouse=True)
def collect_qt_garbage(request: pytest.FixtureRequest) -> Iterator[None]:
    """
    Destroys a finished `qt` test's widgets on the GUI thread.

    pytest-qt closes registered widgets with `deleteLater()`, which only runs in a later
    event loop, and reference cycles (slots bound to `self`) keep the Python wrappers
    alive until a garbage collection. Whichever thread runs that collection destroys the
    widgets, possibly a later test's reader or engine thread. Destroyed there, their timers
    stay registered on the GUI thread, and the next timer event hits freed memory: a
    segfault in QObject::event, tests away from the cause.
    """
    yield
    qtcore = sys.modules.get("PyQt6.QtCore")
    if "qt" not in request.keywords or qtcore is None:
        return
    qtcore.QCoreApplication.sendPostedEvents(None, qtcore.QEvent.Type.DeferredDelete)
    gc.collect()
