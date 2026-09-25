"""
The scope look (R9.1, ADR-0012): square flat styling, the bundled B612 fonts and
locale-free numbers.

Marked `qt`: skipped when pytest-qt is disabled or Qt can't load (see tests/conftest.py).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402

pytestmark = pytest.mark.qt

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def comma_locale() -> Iterator[None]:
    """A Polish desktop: the system locale writes 0,12."""
    before = QtCore.QLocale()
    QtCore.QLocale.setDefault(QtCore.QLocale(QtCore.QLocale.Language.Polish))
    yield
    QtCore.QLocale.setDefault(before)


def test_bundled_fonts_load_without_being_installed(qtbot: Any) -> None:
    from styles import FONT_DIR, MONO_FAMILY, UI_FAMILY, load_fonts, mono_font

    assert (FONT_DIR / "OFL.txt").is_file()  # the licence ships with the fonts
    assert load_fonts()
    families = QtGui.QFontDatabase.families()
    assert UI_FAMILY in families and MONO_FAMILY in families
    assert QtGui.QFontInfo(mono_font(10)).family() == MONO_FAMILY


def test_theme_sets_b612_and_square_styles(qtbot: Any) -> None:
    from styles import STYLESHEET, UI_FAMILY, apply_dark_theme

    app = QtWidgets.QApplication.instance()
    assert isinstance(app, QtWidgets.QApplication)
    saved = (app.font(), app.palette(), app.styleSheet())
    try:
        apply_dark_theme(app)
        assert app.font().family() == UI_FAMILY
        assert app.styleSheet() == STYLESHEET
        button = QtWidgets.QPushButton("Send")
        qtbot.addWidget(button)
        assert QtGui.QFontInfo(button.font()).family() == UI_FAMILY
    finally:
        app.setStyleSheet(saved[2])
        app.setPalette(saved[1])
        app.setFont(saved[0])


def test_no_widget_style_has_rounded_corners() -> None:
    """Every border-radius in the app's stylesheets is 0 (the scope look is square)."""
    sources = [ROOT / "styles.py", *sorted((ROOT / "ui").rglob("*.py"))]
    radii = [
        (path.name, value)
        for path in sources
        for value in re.findall(r"border-radius:\s*([^;\"}]+)", path.read_text(encoding="utf-8"))
    ]
    assert radii and all(value.strip() in ("0", "0px") for _, value in radii), radii


@pytest.mark.parametrize(
    ("value", "decimals", "sign", "text"),
    [
        (0.12, 4, False, "0.12"),
        (26.5, 4, False, "26.5"),
        (2.0, 3, False, "2"),
        (0.0001, 3, False, "0"),
        (-0.0001, 3, False, "0"),
        (1.5, 3, True, "+1.5"),
        (-0.25, 3, True, "-0.25"),
        (1234.5678, 2, False, "1234.57"),
        (float("nan"), 3, False, "n/a"),
    ],
)
def test_format_number_shows_significant_digits(
    value: float, decimals: int, sign: bool, text: str
) -> None:
    from ui.common.numbers import format_number

    assert format_number(value, decimals, sign) == text


def test_spin_box_uses_a_dot_whatever_the_locale(qtbot: Any, comma_locale: None) -> None:
    from ui.common.numbers import ScopeDoubleSpinBox

    assert QtCore.QLocale().decimalPoint() == ","  # the test really runs on a comma locale
    sb = ScopeDoubleSpinBox()
    qtbot.addWidget(sb)
    sb.setDecimals(4)
    sb.setRange(0.0, 1000.0)
    sb.setValue(0.12)
    assert sb.text() == "0.12"
    sb.setValue(26.5)
    assert sb.text() == "26.5"

    line = sb.lineEdit()
    assert line is not None
    for typed, expected in (("0.015", 0.015), ("0,5", 0.5)):  # a comma out of habit works too
        line.setText(typed)
        sb.interpretText()
        assert sb.value() == pytest.approx(expected)


def test_pid_panel_inputs_show_short_numbers(qtbot: Any, comma_locale: None) -> None:
    """The finding that started it: `0,1200` in the PID panel."""
    from core.config import DEFAULT_CONFIG_PATH, StreamConfigLoader
    from ui.panels.command_panel import CommandPanel

    panel = CommandPanel(StreamConfigLoader(DEFAULT_CONFIG_PATH).panels["diffbot_pid"])
    qtbot.addWidget(panel)
    texts = [
        w.text()
        for col in panel.inputs.values()
        for w in col.values()
        if isinstance(w, QtWidgets.QDoubleSpinBox)
    ]
    assert texts
    assert all("," not in t and not re.search(r"\.\d*0$", t) for t in texts), texts
