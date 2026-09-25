"""
The top bar (R9.2, ADR-0012 decision 1): one 36 px row of boxed labels, as on a bench scope,
in place of the toolbar, the stream-tabs row and the status bar.

Left to right: profile | port and Connect | RUN/STOP | stream tabs | a transient message |
`Window 10 s` | `Trigger`, and only while they're true: REC with its time, and link
problems in words (`3 CRC errors · 12 lost`). Idle state isn't shown: the owner found the
scope letters (`H`, `T`), the rate and points and an idle `30 kB/s` more noise than help
(after R9.5). The widgets here only draw; `MainWindow` fills them from the engine's
reports and the panels' state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets

from styles import (
    AMBER,
    BAR,
    BORDER,
    GREEN,
    NUMBER_CSS,
    NUMBER_FAMILY,
    RED,
    RED_FILL,
    TEXT,
    TEXT_DIM,
    TEXT_MUTED,
    UI_FAMILY,
)

BAR_HEIGHT = 36
TRANSIENT_MS = 5000
BADGE_IDLE = "#3a3a3a"
BADGE_TEXT = "#eeeeee"
HOVER_FILL = "#1c1c1c"

MessageLevel = str  # "info" | "ok" | "warn" | "error"
MESSAGE_COLORS = {"info": TEXT_DIM, "ok": GREEN, "warn": AMBER, "error": RED}


# --- number formats ---------------------------------------------------------------------


def _sig3(value: float) -> str:
    """Three significant digits, zeros kept, as a scope labels its scales: 10.0, 2.50, 250."""
    if value == 0 or not math.isfinite(value):
        return "0"
    decimals = max(0, 2 - math.floor(math.log10(abs(value))))
    return f"{value:.{decimals}f}"


def _trim(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_duration(seconds: float) -> str:
    """A time span in the unit that keeps it readable: `10 s`, `2.5 s`, `500 ms`, `250 µs`."""
    if seconds >= 1:
        return f"{_trim(_sig3(seconds))} s"
    if seconds >= 1e-3:
        return f"{_trim(_sig3(seconds * 1e3))} ms"
    return f"{_trim(_sig3(seconds * 1e6))} µs"


# --- painted parts ----------------------------------------------------------------------


@dataclass(frozen=True)
class Badge:
    """A letter in a small filled box, where a scope has `H`, `T`, `A`, `B`."""

    letter: str
    fill: str = BADGE_IDLE
    color: str = BADGE_TEXT


@dataclass(frozen=True)
class Square:
    """A small square: filled (a colour, a state that's on) or hollow (off)."""

    color: str
    filled: bool = True
    size: int = 8


@dataclass(frozen=True)
class Text:
    text: str
    color: str = TEXT
    number: bool = True  # the number face (B612), else the UI face
    bold: bool = False
    px: float = 13


Part = Badge | Square | Text


class PartsButton(QtWidgets.QToolButton):
    """
    A flat bar button drawn from parts (a boxed letter, squares, text in the UI or mono
    face). `text()` is the parts' plain text, for accessibility and tests.
    """

    PAD = 12
    GAP = 7

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._parts: list[Part] = []
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding
        )

    def set_parts(self, parts: list[Part]) -> None:
        self._parts = list(parts)
        words = [
            p.letter if isinstance(p, Badge) else p.text for p in parts if not isinstance(p, Square)
        ]
        self.setText(" ".join(words))
        self.updateGeometry()
        self.update()

    @staticmethod
    def _font(part: Text | Badge) -> QtGui.QFont:
        if isinstance(part, Badge):
            font = QtGui.QFont(UI_FAMILY)
            font.setPixelSize(11)
            font.setBold(True)
            return font
        font = QtGui.QFont(NUMBER_FAMILY if part.number else UI_FAMILY)
        font.setPixelSize(round(part.px))
        font.setBold(part.bold)
        return font

    def _width(self, part: Part) -> int:
        if isinstance(part, Badge):
            return 16
        if isinstance(part, Square):
            return part.size
        return QtGui.QFontMetrics(self._font(part)).horizontalAdvance(part.text)

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        widths = [self._width(p) for p in self._parts]
        inner = sum(widths) + self.GAP * max(len(widths) - 1, 0)
        return QtCore.QSize(inner + 2 * self.PAD, BAR_HEIGHT)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event: QtGui.QPaintEvent | None) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        rect = self.rect()
        if self.isEnabled() and (self.underMouse() or self.isDown()):
            painter.fillRect(rect, QtGui.QColor(HOVER_FILL))
        x = self.PAD
        mid = rect.height() / 2
        for part in self._parts:
            w = self._width(part)
            if isinstance(part, Badge):
                box = QtCore.QRectF(x, mid - 8, 16, 16)
                painter.fillRect(box, QtGui.QColor(part.fill))
                painter.setPen(QtGui.QColor(part.color))
                painter.setFont(self._font(part))
                painter.drawText(box, QtCore.Qt.AlignmentFlag.AlignCenter, part.letter)
            elif isinstance(part, Square):
                box = QtCore.QRectF(x, mid - part.size / 2, part.size, part.size)
                if part.filled:
                    painter.fillRect(box, QtGui.QColor(part.color))
                else:
                    painter.setPen(QtGui.QColor(part.color))
                    painter.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
            else:
                color = QtGui.QColor(part.color if self.isEnabled() else TEXT_MUTED)
                painter.setPen(color)
                painter.setFont(self._font(part))
                box = QtCore.QRectF(x, 0, w + 2, rect.height())
                flags = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
                painter.drawText(box, flags, part.text)
            x += w + self.GAP
        painter.end()


class RunBox(QtWidgets.QPushButton):
    """
    RUN / STOP, as on a scope's front panel. Checked = stopped (today's Pause: the view is
    frozen for analysis; acquisition goes on). Disconnected and running: a dim `—`, still
    clickable, so the last session can be frozen and measured.
    """

    RUN = (
        f"QPushButton {{ background: transparent; color: {GREEN}; border: 1px solid {GREEN};"
        f" {NUMBER_CSS} font-weight: bold; }}"
    )
    STOP = (
        f"QPushButton {{ background: {RED_FILL}; color: #fff; border: 1px solid {RED_FILL};"
        f" {NUMBER_CSS} font-weight: bold; }}"
    )
    IDLE = (
        f"QPushButton {{ background: transparent; color: {TEXT_MUTED};"
        f" border: 1px solid {BORDER}; {NUMBER_CSS} }}"
    )

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(58, 24)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._connected = False
        self.toggled.connect(lambda _=False: self.refresh())
        self.refresh()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.refresh()

    def refresh(self) -> None:
        if self.isChecked():
            self.setText("STOP")
            self.setStyleSheet(self.STOP)
            self.setToolTip("Stopped: the view is frozen for measuring. Space runs again.")
        elif self._connected:
            self.setText("RUN")
            self.setStyleSheet(self.RUN)
            self.setToolTip("Running: the plot follows the data. Space stops it.")
        else:
            self.setText("—")
            self.setStyleSheet(self.IDLE)
            self.setToolTip("Not connected. Space freezes what's in the buffers.")


class MessageLabel(QtWidgets.QLabel):
    """
    The bar's transient message (was the status bar): info fades after a few seconds;
    warnings and errors stay until the next message, since they need action.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.level: MessageLevel = "info"
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        self.setContentsMargins(12, 0, 12, 0)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade)
        self.say("")

    def say(self, text: str, level: MessageLevel = "info", tooltip: str = "") -> None:
        self.level = level
        self.setText(text)
        self.setToolTip(tooltip or text)
        weight = " font-weight: bold;" if level in ("warn", "error") else ""
        self.setStyleSheet(
            f"color: {MESSAGE_COLORS.get(level, TEXT_DIM)}; font-size: 12px;{weight}"
        )
        if text and level in ("info", "ok"):
            self._timer.start(TRANSIENT_MS)
        else:
            self._timer.stop()

    def _fade(self) -> None:
        self.setText("")
        self.setToolTip("")


class LinkHealth(QtWidgets.QLabel):
    """
    Link problems in words, black on amber (`3 CRC errors · 12 lost`); nothing while the
    link is clean (`has_problems` false, and the owner hides the item). Every counter and
    the byte rate stay in the tooltip and in View → Link statistics.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background: {AMBER}; color: #000; font-size: 12px; font-weight: bold;"
            " padding: 0 12px; margin: 4px 0;"
        )
        self.rate = ""
        self.details = ""
        self.show_report("", "", "")

    @property
    def has_problems(self) -> bool:
        return bool(self.text())

    def show_report(self, rate: str, problems: str, tooltip: str) -> None:
        self.rate = rate
        self.details = tooltip
        self.setText(problems)
        self.setToolTip(f"{rate}\n{tooltip}".strip())


class TopBar(QtWidgets.QFrame):
    """The row itself: items separated by 1 px dividers; `add_stretch` for the message."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("top_bar")
        self.setFixedHeight(BAR_HEIGHT)
        self.setStyleSheet(
            f"QFrame#top_bar {{ background: {BAR}; border-bottom: 1px solid {BORDER}; }}"
            " QFrame#top_bar QComboBox { background: transparent; border: none; }"
            " QFrame#top_bar QToolButton { background: transparent; border: none; }"
            f" QFrame#top_divider {{ background: {BORDER}; border: none; }}"
        )
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 1)
        self._layout.setSpacing(0)
        self._optional: dict[QtWidgets.QWidget, QtWidgets.QFrame] = {}

    def add(self, *widgets: QtWidgets.QWidget, divider: bool = True, spacing: int = 0) -> None:
        """One item: its widgets side by side, then a divider."""
        if len(widgets) == 1 and spacing == 0:
            self._layout.addWidget(widgets[0])
        else:
            box = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(box)
            row.setContentsMargins(10, 0, 10, 0)
            row.setSpacing(spacing)
            for w in widgets:
                row.addWidget(w)
            self._layout.addWidget(box)
        if divider:
            self.add_divider()

    def add_divider(self) -> None:
        line = QtWidgets.QFrame()
        line.setObjectName("top_divider")
        line.setFixedWidth(1)
        self._layout.addWidget(line)

    def add_stretch(self, widget: QtWidgets.QWidget) -> None:
        self._layout.addWidget(widget, 1)

    def add_optional(self, widget: QtWidgets.QWidget) -> None:
        """An item shown only while it has something to say, with a divider before it."""
        line = QtWidgets.QFrame()
        line.setObjectName("top_divider")
        line.setFixedWidth(1)
        self._layout.addWidget(line)
        self._layout.addWidget(widget)
        self._optional[widget] = line
        self.set_shown(widget, False)

    def set_shown(self, widget: QtWidgets.QWidget, shown: bool) -> None:
        widget.setVisible(shown)
        line = self._optional.get(widget)
        if line is not None:
            line.setVisible(shown)
