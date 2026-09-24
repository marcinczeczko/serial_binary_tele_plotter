"""
A control panel generated from a `panels` entry in streams.json (R5.2).

One row per parameter and one column per `columns` entry: a spin box for `float` and
`int` parameters, a check box for `bool`. A button placed in a column sits under it and
sends its command with that column's values; a button without a column spans the panel
(its command's fields name their columns).

The panel only collects values: the main window resolves and encodes the command
(`core.protocol.commands`) and hands the packet to the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtWidgets

from core.config.controls import ButtonDef, PanelDef, ParamDef

ParamWidget = QtWidgets.QDoubleSpinBox | QtWidgets.QSpinBox | QtWidgets.QCheckBox


@dataclass(frozen=True)
class SendRequest:
    """A button press: what to send, and every parameter's value at that moment."""

    panel: str
    button: ButtonDef
    column: str | None
    params: dict[str, dict[str, float]]


class CommandPanel(QtWidgets.QWidget):
    send_requested = QtCore.pyqtSignal(object)  # SendRequest
    values_changed = QtCore.pyqtSignal()

    def __init__(self, panel: PanelDef, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.panel = panel
        self.inputs: dict[str, dict[str, ParamWidget]] = {c: {} for c in panel.column_keys}
        self.buttons: list[QtWidgets.QPushButton] = []

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        row = 0
        if panel.columns:
            for i, col in enumerate(panel.columns):
                grid.addWidget(QtWidgets.QLabel(f"<b>{col}</b>"), row, i + 1)
            row += 1
        for param in panel.parameters:
            grid.addWidget(QtWidgets.QLabel(f"{param.label}:"), row, 0)
            for i, col in enumerate(panel.column_keys):
                widget = self._input(param)
                self.inputs[col][param.key] = widget
                grid.addWidget(widget, row, i + 1)
            row += 1

        # Column buttons under their column, stacked; the others span the panel.
        per_column: dict[str, int] = {}
        spanning: list[ButtonDef] = []
        for button in panel.buttons:
            if button.column is None or not panel.columns:
                spanning.append(button)
                continue
            i = panel.columns.index(button.column)
            offset = per_column.get(button.column, 0)
            per_column[button.column] = offset + 1
            grid.addWidget(self._button(button), row + offset, i + 1)
        row += max(per_column.values(), default=0)
        for button in spanning:
            grid.addWidget(self._button(button), row, 0, 1, len(panel.column_keys) + 1)
            row += 1

    def _input(self, param: ParamDef) -> ParamWidget:
        widget: ParamWidget
        if param.kind == "bool":
            widget = QtWidgets.QCheckBox()
            widget.setChecked(bool(param.default))
            widget.toggled.connect(self.values_changed)
            return widget
        if param.kind == "int":
            widget = QtWidgets.QSpinBox()
            widget.setRange(int(param.minimum), int(param.maximum))
            widget.setSingleStep(max(int(param.step), 1))
            widget.setValue(int(param.default))
        else:
            widget = QtWidgets.QDoubleSpinBox()
            widget.setRange(param.minimum, param.maximum)
            widget.setDecimals(param.decimals)
            widget.setSingleStep(param.step)
            widget.setValue(param.default)
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(self.values_changed)
        return widget

    def _button(self, button: ButtonDef) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(button.label)
        btn.clicked.connect(lambda _checked=False, b=button: self._send(b))
        self.buttons.append(btn)
        return btn

    def _send(self, button: ButtonDef) -> None:
        request = SendRequest(
            self.panel.key, button, self.panel.button_column(button), self.values()
        )
        self.send_requested.emit(request)

    def values(self) -> dict[str, dict[str, float]]:
        """{column: {parameter: value}}; check boxes are 1.0 or 0.0."""
        return {
            col: {key: _value(widget) for key, widget in widgets.items()}
            for col, widgets in self.inputs.items()
        }

    def set_values(self, values: dict[str, dict[str, float]]) -> None:
        """Restores values (e.g. from the last session); unknown ones are ignored."""
        for col, params in values.items():
            for key, value in params.items():
                widget = self.inputs.get(col, {}).get(key)
                if widget is None:
                    continue
                widget.blockSignals(True)
                if isinstance(widget, QtWidgets.QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, QtWidgets.QSpinBox):
                    widget.setValue(int(value))
                else:
                    widget.setValue(float(value))
                widget.blockSignals(False)


def _value(widget: ParamWidget) -> float:
    if isinstance(widget, QtWidgets.QCheckBox):
        return 1.0 if widget.isChecked() else 0.0
    return float(widget.value())
