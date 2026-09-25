"""
The scope look (ADR-0012, R9.1): square, flat greys, pure black plot, B612 / B612 Mono.

Bench scopes (Rigol, Keysight, Tek) show a lot of state in little space with one fixed visual
language; this module is that language for the whole app. Colour means something: traces are
the brightest thing on screen, chrome stays grey, green = running, red = stopped / recording /
fault, orange = trigger, amber = edited / needs attention. Widgets that need one of those
colours take the constants below instead of writing their own hex.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6 import QtGui, QtWidgets

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
UI_FAMILY = "B612"
MONO_FAMILY = "B612 Mono"
# Inline stylesheets name the mono face with fallbacks, for a machine where loading failed.
MONO_CSS = f"font-family: '{MONO_FAMILY}', Menlo, 'DejaVu Sans Mono', monospace;"
# Numbers (readouts, inputs, the top bar) use B612 itself: B612 Mono's decimal point takes a
# full cell (`0. 25`). B612 Mono stays for text that must align by column (code, hex, the
# terminal).
NUMBER_FAMILY = UI_FAMILY
NUMBER_CSS = f"font-family: '{NUMBER_FAMILY}';"

# --- tokens ---------------------------------------------------------------------------------
PLOT_BG = "#000000"
PANEL = "#0d0d0d"
BAR = "#141414"
BORDER = "#333333"
BORDER_DIM = "#262626"
BUTTON = "#222222"
BUTTON_BORDER = "#484848"
TEXT = "#d6d6d6"
TEXT_BRIGHT = "#ffffff"
TEXT_DIM = "#9a9a9a"
TEXT_MUTED = "#777777"
TEXT_DISABLED = "#5c5c5c"
GREEN = "#3DFF6E"
RED = "#FF4040"
RED_FILL = "#D32020"
ORANGE = "#FF9A1A"
AMBER = "#FFB000"
AMBER_FILL = "#2a2008"

_loaded: bool = False


def load_fonts() -> bool:
    """
    Registers the bundled B612 and B612 Mono (SIL OFL 1.1) with Qt, once, so the app looks
    the same on a machine that doesn't have them installed. False if a file didn't load; the
    app then runs in the platform font.
    """
    global _loaded
    if _loaded:
        return True
    ok = True
    for path in sorted(FONT_DIR.glob("*.ttf")):
        if QtGui.QFontDatabase.addApplicationFont(str(path)) < 0:
            log.warning("could not load font %s", path)
            ok = False
    _loaded = ok
    return ok


def number_font(point_size: float | None = None) -> QtGui.QFont:
    """The face for numbers (B612), at `point_size` (default: the application's size)."""
    font = QtGui.QFont(NUMBER_FAMILY)
    size = point_size if point_size is not None else QtGui.QGuiApplication.font().pointSizeF()
    if size > 0:
        font.setPointSizeF(size)
    return font


def mono_font(point_size: float | None = None) -> QtGui.QFont:
    """B612 Mono for numbers and code, at `point_size` (default: the application's size)."""
    font = QtGui.QFont(MONO_FAMILY)
    font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
    size = point_size if point_size is not None else QtGui.QGuiApplication.font().pointSizeF()
    if size > 0:
        font.setPointSizeF(size)
    return font


STYLESHEET = f"""
QMainWindow, QDialog {{ background: {PANEL}; }}
QMainWindow::separator {{ background: {BORDER_DIM}; width: 1px; height: 1px; }}
QToolTip {{
    background: {BAR}; color: {TEXT}; border: 1px solid {BUTTON_BORDER}; padding: 3px 5px;
}}

QPushButton, QToolButton {{
    background: {BUTTON}; color: {TEXT}; border: 1px solid {BUTTON_BORDER}; border-radius: 0;
    padding: 3px 10px;
}}
QToolButton {{ padding: 3px 8px; }}
QPushButton:hover, QToolButton:hover {{ background: #2c2c2c; border-color: #6a6a6a; }}
QPushButton:pressed, QToolButton:pressed {{ background: #1a1a1a; }}
QPushButton:checked, QToolButton:checked {{
    background: {TEXT}; color: #000; border-color: {TEXT};
}}
QPushButton:disabled, QToolButton:disabled {{
    background: #181818; color: {TEXT_DISABLED}; border-color: {BORDER_DIM};
}}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit {{
    background: #000; color: #e0e0e0; border: 1px solid {BUTTON_BORDER}; border-radius: 0;
    padding: 2px 4px; selection-background-color: {BUTTON_BORDER}; selection-color: #fff;
}}
QLineEdit:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {TEXT_DIM};
}}
QLineEdit:disabled, QAbstractSpinBox:disabled, QPlainTextEdit:disabled {{
    color: {TEXT_DISABLED}; border-color: {BORDER_DIM};
}}

QComboBox {{
    background: {BUTTON}; color: {TEXT}; border: 1px solid {BUTTON_BORDER}; border-radius: 0;
    padding: 2px 6px;
}}
QComboBox:hover {{ border-color: #6a6a6a; }}
QComboBox:disabled {{ color: {TEXT_DISABLED}; border-color: {BORDER_DIM}; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background: {BAR}; color: {TEXT}; border: 1px solid {BUTTON_BORDER};
    selection-background-color: {BORDER}; selection-color: #fff; outline: 0;
}}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator, QTreeView::indicator {{
    width: 11px; height: 11px; border: 1px solid #6a6a6a; border-radius: 0; background: #000;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked,
QTreeView::indicator:checked {{
    background: {TEXT}; border-color: {TEXT};
}}
QCheckBox::indicator:indeterminate, QTreeView::indicator:indeterminate {{
    background: #6a6a6a; border-color: #6a6a6a;
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {BORDER}; }}

QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 0; margin-top: 8px; padding-top: 8px;
    color: {TEXT_DIM};
}}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px; }}

QMenuBar {{ background: {BAR}; color: {TEXT}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {BORDER}; }}
QMenu {{ background: {BAR}; color: {TEXT}; border: 1px solid {BUTTON_BORDER}; padding: 2px 0; }}
QMenu::item {{ padding: 4px 20px 4px 20px; }}
QMenu::item:selected {{ background: {BORDER}; color: #fff; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 0; }}
QMenu::indicator {{ width: 9px; height: 9px; left: 6px; border: 1px solid #6a6a6a; }}
QMenu::indicator:checked {{ background: {TEXT}; border-color: {TEXT}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PLOT_BG}; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM}; border: none; border-radius: 0;
    border-bottom: 2px solid transparent; padding: 6px 14px;
}}
QTabBar::tab:selected {{ color: {TEXT_BRIGHT}; border-bottom-color: {TEXT_BRIGHT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QToolBar {{
    background: {BAR}; border: none; border-bottom: 1px solid {BORDER}; spacing: 4px;
    padding: 2px;
}}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 4px 3px; }}
QStatusBar {{ background: {BAR}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QDockWidget {{ color: {TEXT_DIM}; }}
QDockWidget::title {{
    background: {BAR}; border-bottom: 1px solid {BORDER}; padding: 4px 6px; text-align: left;
}}

QHeaderView::section {{
    background: {BAR}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER_DIM}; padding: 3px 6px;
}}
QTreeView, QTableView, QListView {{
    background: {PANEL}; alternate-background-color: #111; border: 1px solid {BORDER_DIM};
    selection-background-color: {BORDER}; selection-color: #fff; outline: 0;
    gridline-color: {BORDER_DIM};
}}
QTableCornerButton::section {{ background: {BAR}; border: none; }}

QScrollBar:vertical {{ background: {PANEL}; width: 10px; margin: 0; border: none; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 10px; margin: 0; border: none; }}
QScrollBar::handle {{ background: {BORDER}; border-radius: 0; }}
QScrollBar::handle:vertical {{ min-height: 20px; }}
QScrollBar::handle:horizontal {{ min-width: 20px; }}
QScrollBar::handle:hover {{ background: {BUTTON_BORDER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSplitter::handle {{ background: {BORDER_DIM}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QProgressBar {{
    background: #000; border: 1px solid {BUTTON_BORDER}; border-radius: 0; text-align: center;
}}
QProgressBar::chunk {{ background: {TEXT}; }}
QSlider::groove:horizontal {{ height: 2px; background: {BUTTON_BORDER}; }}
QSlider::handle:horizontal {{
    width: 8px; margin: -6px 0; background: {TEXT}; border: none; border-radius: 0;
}}
"""


def apply_dark_theme(app: QtWidgets.QApplication) -> None:
    """
    The scope look for the whole application: Fusion as the base (predictable on every
    platform), a flat grey palette, B612 as the UI font and the square stylesheet above.
    Call once, before any window is built.
    """
    app.setStyle("Fusion")

    if load_fonts():
        font = QtGui.QFont(UI_FAMILY)
        font.setPointSizeF(app.font().pointSizeF())  # keep the platform's size
        app.setFont(font)

    palette = QtGui.QPalette()
    role = QtGui.QPalette.ColorRole
    group = QtGui.QPalette.ColorGroup
    for r, color in (
        (role.Window, PANEL),
        (role.WindowText, TEXT),
        (role.Base, PLOT_BG),
        (role.AlternateBase, "#111111"),
        (role.Text, "#e0e0e0"),
        (role.Button, BUTTON),
        (role.ButtonText, TEXT),
        (role.BrightText, TEXT_BRIGHT),
        (role.Highlight, BUTTON_BORDER),
        (role.HighlightedText, TEXT_BRIGHT),
        (role.ToolTipBase, BAR),
        (role.ToolTipText, TEXT),
        (role.PlaceholderText, TEXT_DISABLED),
        (role.Link, AMBER),
        (role.Light, "#3a3a3a"),
        (role.Midlight, "#2a2a2a"),
        (role.Mid, BORDER),
        (role.Dark, "#111111"),
        (role.Shadow, "#000000"),
    ):
        palette.setColor(r, QtGui.QColor(color))
    for r in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(group.Disabled, r, QtGui.QColor(TEXT_DISABLED))
    app.setPalette(palette)

    app.setStyleSheet(STYLESHEET)
