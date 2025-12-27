import sys
from PyQt6 import QtWidgets, QtCore
import pyqtgraph as pg


# -----------------------------
# Global application styling
# -----------------------------
def apply_dark_theme(app: QtWidgets.QApplication):
    app.setStyle("Fusion")

    palette = app.palette()
    palette.setColor(palette.ColorRole.Window, QtCore.Qt.GlobalColor.black)
    palette.setColor(palette.ColorRole.WindowText, QtCore.Qt.GlobalColor.white)
    palette.setColor(palette.ColorRole.Base, QtCore.Qt.GlobalColor.black)
    palette.setColor(palette.ColorRole.AlternateBase, QtCore.Qt.GlobalColor.darkGray)
    palette.setColor(palette.ColorRole.Text, QtCore.Qt.GlobalColor.white)
    palette.setColor(palette.ColorRole.Button, QtCore.Qt.GlobalColor.darkGray)
    palette.setColor(palette.ColorRole.ButtonText, QtCore.Qt.GlobalColor.white)
    palette.setColor(palette.ColorRole.Highlight, QtCore.Qt.GlobalColor.blue)

    app.setPalette(palette)
    app.setStyleSheet("""
        QCheckBox {
            spacing: 6px;
        }

        QCheckBox::indicator {
            width: 14px;
            height: 14px;
            border: 1px solid #666;
            background-color: #111;
        }

        QCheckBox::indicator:checked {
            background-color: #4FC3F7;
            border: 1px solid #4FC3F7;
        }

        QCheckBox::indicator:unchecked {
            background-color: #111;
        }

        QCheckBox::indicator:disabled {
            border: 1px solid #333;
            background-color: #222;
        }
        
        QDoubleSpinBox {
            padding: 1px 4px;
            background-color: #1e1e1e;
            border: 1px solid #333;
        }

        QToolButton {
            padding: 2px;
        }
        """)


# -----------------------------
# Left control panel
# -----------------------------
class ControlPanel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        # --- Serial settings ---
        serial_group = QtWidgets.QGroupBox("Serial Connection")
        serial_layout = QtWidgets.QGridLayout(serial_group)

        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.addItem("COM1")
        self.port_combo.addItem("COM2")

        self.refresh_btn = QtWidgets.QPushButton("⟳")

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])

        serial_layout.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        serial_layout.addWidget(self.port_combo, 0, 1)
        serial_layout.addWidget(self.refresh_btn, 0, 2)
        serial_layout.addWidget(QtWidgets.QLabel("Baud:"), 1, 0)
        serial_layout.addWidget(self.baud_combo, 1, 1, 1, 2)

        # --- Payload selection ---
        payload_group = QtWidgets.QGroupBox("Payload Type")
        payload_layout = QtWidgets.QVBoxLayout(payload_group)

        self.payload_combo = QtWidgets.QComboBox()
        self.payload_combo.addItem("PID Telemetry")

        payload_layout.addWidget(self.payload_combo)

        # --- Connect button ---
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setMinimumHeight(36)

        # --- Y axis control placeholder ---
        # --- Y Axis / Signals ---
        axis_group = QtWidgets.QGroupBox("Signals")
        axis_layout = QtWidgets.QVBoxLayout(axis_group)
        axis_layout.setSpacing(8)

        self.y_controls = []

        # --- Groups ---
        group_pid_core = CollapsibleGroup("PID Core")
        group_pid_terms = CollapsibleGroup("PID Terms")
        group_actuator = CollapsibleGroup("Actuator")
        
        groups = [group_pid_core, group_pid_terms, group_actuator]
        for g in groups:
            g.expanded.connect(lambda opened, g=g: self._collapse_others(opened, groups))

        # --- PID Core ---
        core_signals = [
            ("Setpoint", "#4FC3F7"),
            ("Measurement", "#81C784"),
            ("Error", "#BA68C8"),
        ]

        for name, color in core_signals:
            w = YAxisControlWidget(name, color)
            group_pid_core.content_layout.addWidget(w)
            separator = QtWidgets.QFrame()
            separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            separator.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
            separator.setStyleSheet("color: white;")
            group_pid_core.content_layout.addWidget(separator)
            self.y_controls.append(w)

        # --- PID Terms ---
        term_signals = [
            ("P Term", "#FFD54F"),
            ("I Term", "#4DB6AC"),
            ("D Term", "#E57373"),
        ]

        for name, color in term_signals:
            w = YAxisControlWidget(name, color)
            group_pid_terms.content_layout.addWidget(w)
            self.y_controls.append(w)

        # --- Actuator ---
        actuator_signals = [
            ("Output", "#FF8A65"),
        ]

        for name, color in actuator_signals:
            w = YAxisControlWidget(name, color)
            group_actuator.content_layout.addWidget(w)
            self.y_controls.append(w)

        # --- Add groups ---
        axis_layout.addWidget(group_pid_core)
        axis_layout.addWidget(group_pid_terms)
        axis_layout.addWidget(group_actuator)
        axis_layout.addStretch()

        layout.addWidget(axis_group)
        # axis_group = QtWidgets.QGroupBox("Y Axis Control (PID)")
        # axis_layout = QtWidgets.QVBoxLayout(axis_group)

        # self.lock_y_checkbox = QtWidgets.QCheckBox("Lock Y axis range")
        # axis_layout.addWidget(self.lock_y_checkbox)

        # Spacer
        layout.addWidget(serial_group)
        layout.addWidget(payload_group)
        layout.addWidget(axis_group)
        layout.addStretch()
        layout.addWidget(self.connect_btn)
        
    def _collapse_others(self, opened, groups):
        for g in groups:
            if g is not opened:
                g.toggle.setChecked(False)


# -----------------------------
# Plot area
# -----------------------------
class PlotArea(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)

        pg.setConfigOptions(antialias=True)
        pg.setConfigOption("background", "#111111")
        pg.setConfigOption("foreground", "#DDDDDD")

        # --- Main plot item ---
        self.plot = self.graphics.addPlot(title="PID Telemetry")
        self.plot.showGrid(x=True, y=True, alpha=0.3)

        self.plot.setLabel("bottom", "Sample")

        # --- Main ViewBox (Setpoint) ---
        self.vb_setpoint = self.plot.getViewBox()

        self.curve_setpoint = pg.PlotCurveItem(
            pen=pg.mkPen(color="#4FC3F7", width=2),
            name="Setpoint"
        )
        self.vb_setpoint.addItem(self.curve_setpoint)
        self.plot.setLabel("left", "Setpoint")

        # --- Measurement ViewBox ---
        self.vb_measurement = pg.ViewBox()
        self.plot.scene().addItem(self.vb_measurement)
        self.plot.getAxis("right").setLabel("Measurement")
        self.plot.getAxis("right").setStyle(tickTextOffset=10)
        self.plot.getAxis("right").linkToView(self.vb_measurement)
        self.vb_measurement.setXLink(self.vb_setpoint)

        self.curve_measurement = pg.PlotCurveItem(
            pen=pg.mkPen(color="#81C784", width=2),
            name="Measurement"
        )
        self.vb_measurement.addItem(self.curve_measurement)

        # --- Output ViewBox (second right axis) ---
        self.vb_output = pg.ViewBox()
        self.plot.scene().addItem(self.vb_output)
        self.vb_output.setXLink(self.vb_setpoint)

        self.axis_output = pg.AxisItem("right")
        self.axis_output.setLabel("Output")
        self.axis_output.setStyle(tickTextOffset=30)
        self.plot.layout.addItem(self.axis_output, 2, 3)
        self.axis_output.linkToView(self.vb_output)

        self.curve_output = pg.PlotCurveItem(
            pen=pg.mkPen(color="#FF8A65", width=2),
            name="Output"
        )
        self.vb_output.addItem(self.curve_output)

        # --- Keep viewboxes aligned ---
        self.vb_setpoint.sigResized.connect(self._update_views)

    def _update_views(self):
        rect = self.vb_setpoint.sceneBoundingRect()
        self.vb_measurement.setGeometry(rect)
        self.vb_output.setGeometry(rect)


## Widget kontroli osi Y
class YAxisControlWidget(QtWidgets.QWidget):
    def __init__(self, name: str, color: str):
        super().__init__()

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(4, 2, 4, 2)
        main_layout.setSpacing(2)

        # --- Header row ---
        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setSpacing(6)

        self.enable_checkbox = QtWidgets.QCheckBox(name)
        self.enable_checkbox.setChecked(True)

        color_dot = QtWidgets.QLabel("●")
        color_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

        header_layout.addWidget(color_dot)
        header_layout.addWidget(self.enable_checkbox)
        header_layout.addStretch()

        # --- Range row ---
        range_layout = QtWidgets.QHBoxLayout()
        range_layout.setSpacing(4)

        self.min_edit = QtWidgets.QDoubleSpinBox()
        self.max_edit = QtWidgets.QDoubleSpinBox()

        for w in (self.min_edit, self.max_edit):
            w.setRange(-1e6, 1e6)
            w.setDecimals(3)
            w.setMaximumWidth(80)
            w.setFixedHeight(22)

        range_layout.addWidget(QtWidgets.QLabel("Y:"))
        range_layout.addWidget(self.min_edit)
        range_layout.addWidget(self.max_edit)
        range_layout.addStretch()

        # --- Lock ---
        self.lock_checkbox = QtWidgets.QCheckBox("Lock")

        main_layout.addLayout(header_layout)
        main_layout.addLayout(range_layout)
        main_layout.addWidget(self.lock_checkbox)


class CollapsibleGroup(QtWidgets.QWidget):
    expanded = QtCore.pyqtSignal(object)

    def __init__(self, title: str):
        super().__init__()

        self.toggle = QtWidgets.QToolButton(text=title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.toggle.setStyleSheet("""
            QToolButton {
                font-weight: bold;
                border: none;
                padding: 4px;
            }
        """)

        self.content = QtWidgets.QWidget()
        self.content.setVisible(False)
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 4, 4, 4)
        self.content_layout.setSpacing(6)

        self.toggle.toggled.connect(self._on_toggled)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)

    def _on_toggled(self, checked: bool):
        self.content.setVisible(checked)
        self.toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked
            else QtCore.Qt.ArrowType.RightArrow
        )

        if checked:
            self.expanded.emit(self)


# -----------------------------
# Main window
# -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("DiffBot Telemetry Viewer")
        self.resize(1200, 800)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        self.control_panel = ControlPanel()
        self.plot_area = PlotArea()

        splitter.addWidget(self.control_panel)
        splitter.addWidget(self.plot_area)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 900])


# -----------------------------
# Application entry point
# -----------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
