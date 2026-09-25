"""
The window's side panes and their edge tabs (R9.3, ADR-0012 decision 2).

The window is a splitter: Signals pane, plot, right pane (Tune or Step; the terminal for a
text profile). Edge tabs are always visible, so a closed pane is one click (or `[`, `]`,
`\\`) away and never costs a title bar. `PaneState` is what is open, which view the right
pane shows and how wide each pane was; `UiState` keeps it per profile.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from PyQt6 import QtCore, QtGui, QtWidgets

from styles import BORDER, PANEL, TEXT, TEXT_BRIGHT, TEXT_MUTED, UI_FAMILY

RightView = Literal["tune", "step"]
RIGHT_VIEWS: tuple[RightView, ...] = ("tune", "step")
MIN_PANE_WIDTH = 120
TAB_WIDTH = 22
RIGHT_TAB_HEIGHT = 70
LIT_FILL = "#262626"


@dataclass(frozen=True)
class PaneState:
    left_open: bool = True
    right_open: bool = True
    view: RightView = "tune"
    left_width: int = 250
    right_width: int = 340

    def toggle_left(self) -> PaneState:
        return replace(self, left_open=not self.left_open)

    def toggle_right(self) -> PaneState:
        return replace(self, right_open=not self.right_open)

    def toggle_both(self) -> PaneState:
        """Focus mode: anything open closes both; with both closed, both open."""
        opened = not (self.left_open or self.right_open)
        return replace(self, left_open=opened, right_open=opened)

    def click_right(self, view: RightView) -> PaneState:
        """A right edge tab: its lit tab closes the pane, another one opens it on its view."""
        if self.right_open and self.view == view:
            return replace(self, right_open=False)
        return replace(self, right_open=True, view=view)

    def to_json(self) -> dict[str, Any]:
        return {
            "left_open": self.left_open,
            "right_open": self.right_open,
            "view": self.view,
            "left_width": self.left_width,
            "right_width": self.right_width,
        }

    @classmethod
    def from_json(cls, raw: object) -> PaneState:
        """Whatever was saved, as far as it makes sense; defaults for the rest."""
        if not isinstance(raw, dict):
            return cls()
        default = cls()

        def flag(key: str, fallback: bool) -> bool:
            value = raw.get(key)
            return value if isinstance(value, bool) else fallback

        def width(key: str, fallback: int) -> int:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return max(value, MIN_PANE_WIDTH)
            return fallback

        view = raw.get("view")
        return cls(
            left_open=flag("left_open", default.left_open),
            right_open=flag("right_open", default.right_open),
            view=view if view in RIGHT_VIEWS else default.view,
            left_width=width("left_width", default.left_width),
            right_width=width("right_width", default.right_width),
        )


class EdgeTab(QtWidgets.QAbstractButton):
    """
    A 22 px tab with vertical text on a window edge. Lit (its pane is open): grey fill,
    white text and a 2 px white edge on the pane's side. The left tab reads bottom to top
    from its top end; right tabs read top to bottom.
    """

    def __init__(
        self, text: str, side: Literal["left", "right"], parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.side = side
        self._lit = False
        self.setText(text)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)
        self.setFixedWidth(TAB_WIDTH)
        if side == "right":
            self.setFixedHeight(RIGHT_TAB_HEIGHT)
        else:
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding
            )

    @property
    def lit(self) -> bool:
        return self._lit

    def set_lit(self, lit: bool) -> None:
        if lit != self._lit:
            self._lit = lit
            self.update()

    def label_font(self) -> QtGui.QFont:
        font = QtGui.QFont(UI_FAMILY)
        font.setPixelSize(11)
        font.setBold(True)
        font.setLetterSpacing(QtGui.QFont.SpacingType.PercentageSpacing, 112)
        return font

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        text = QtGui.QFontMetrics(self.label_font()).horizontalAdvance(self.text())
        return QtCore.QSize(TAB_WIDTH, text + 20)

    def paintEvent(self, event: QtGui.QPaintEvent | None) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor(LIT_FILL if self._lit else PANEL))
        if self._lit:
            edge = rect.width() - 2 if self.side == "left" else 0
            painter.fillRect(edge, 0, 2, rect.height(), QtGui.QColor(TEXT_BRIGHT))
        if self.side == "right":  # stacked tabs: a hairline under each
            painter.fillRect(0, rect.height() - 1, rect.width(), 1, QtGui.QColor(BORDER))
        if self._lit:
            color = TEXT_BRIGHT
        elif self.underMouse():
            color = TEXT
        else:
            color = TEXT_MUTED
        painter.setPen(QtGui.QColor(color))
        painter.setFont(self.label_font())
        painter.translate(rect.width() / 2, 0)
        if self.side == "left":  # bottom to top, starting 10 px from the top
            painter.rotate(-90)
            box = QtCore.QRectF(-rect.height(), -rect.width() / 2, rect.height() - 10, rect.width())
            flags = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        else:  # top to bottom, centred
            painter.rotate(90)
            box = QtCore.QRectF(0, -rect.width() / 2, rect.height(), rect.width())
            flags = QtCore.Qt.AlignmentFlag.AlignCenter
        painter.drawText(box, flags, self.text())
        painter.end()


def edge_strip(
    tabs: list[EdgeTab], side: Literal["left", "right"], parent: QtWidgets.QWidget | None = None
) -> QtWidgets.QFrame:
    """The column the tabs sit in, with a hairline towards the plot."""
    strip = QtWidgets.QFrame(parent)
    strip.setObjectName(f"edge_{side}")
    border = "border-right" if side == "left" else "border-left"
    strip.setStyleSheet(
        f"QFrame#edge_{side} {{ background: {PANEL}; {border}: 1px solid {BORDER}; }}"
    )
    strip.setFixedWidth(TAB_WIDTH + 1)
    layout = QtWidgets.QVBoxLayout(strip)
    margins = (0, 0, 1, 0) if side == "left" else (1, 0, 0, 0)
    layout.setContentsMargins(*margins)
    layout.setSpacing(0)
    for tab in tabs:
        layout.addWidget(tab)
    if side == "right":
        layout.addStretch(1)
    return strip
