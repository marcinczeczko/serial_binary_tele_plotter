import sys
import json
from pathlib import Path
import math
import random
from serial.tools import list_ports


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


class StreamConfigLoader:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def list_streams(self):
        return self.data["streams"]

    def get_stream(self, stream_id: str):
        return self.data["streams"][stream_id]


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
        self.refresh_btn = QtWidgets.QPushButton("⟳")
        self.refresh_btn.clicked.connect(self.refresh_serial_ports)

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])

        serial_layout.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        serial_layout.addWidget(self.port_combo, 0, 1)
        serial_layout.addWidget(self.refresh_btn, 0, 2)
        serial_layout.addWidget(QtWidgets.QLabel("Baud:"), 1, 0)
        serial_layout.addWidget(self.baud_combo, 1, 1, 1, 2)
        
        # --- Time Window ---
        time_group = QtWidgets.QGroupBox("Time Window")
        time_layout = QtWidgets.QGridLayout(time_group)

        self.sample_period_edit = QtWidgets.QDoubleSpinBox()
        self.sample_period_edit.setSuffix(" ms")
        self.sample_period_edit.setRange(1, 1000)
        self.sample_period_edit.setValue(50.0)
        self.sample_period_edit.setDecimals(1)
        self.sample_period_edit.valueChanged.connect(self._on_time_config_changed)

        self.sample_count_edit = QtWidgets.QSpinBox()
        self.sample_count_edit.setRange(10, 10000)
        self.sample_count_edit.setValue(200)
        self.sample_count_edit.valueChanged.connect(self._on_time_config_changed)

        time_layout.addWidget(QtWidgets.QLabel("Sample period:"), 0, 0)
        time_layout.addWidget(self.sample_period_edit, 0, 1)

        time_layout.addWidget(QtWidgets.QLabel("Samples:"), 1, 0)
        time_layout.addWidget(self.sample_count_edit, 1, 1)

        # --- Payload selection ---
        payload_group = QtWidgets.QGroupBox("Payload Type")
        payload_layout = QtWidgets.QVBoxLayout(payload_group)

        self.stream_loader = StreamConfigLoader("streams.json")
        self.payload_combo = QtWidgets.QComboBox()
        
        for stream_id, stream in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(stream["name"], stream_id)

        self.payload_combo.currentIndexChanged.connect(self._on_stream_changed)

        payload_layout.addWidget(self.payload_combo)

        # --- Connect button ---
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setMinimumHeight(36)
        
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.setMinimumHeight(32)
        self.apply_btn.clicked.connect(self._apply_plot_config)

        # --- Y axis control placeholder ---
        # --- Y Axis / Signals ---
        axis_group = QtWidgets.QGroupBox("Signals")
        self.axis_layout = QtWidgets.QVBoxLayout(axis_group)
        self.axis_layout.setSpacing(8)        
        self.y_controls = []

        # Spacer
        layout.addWidget(serial_group)
        layout.addWidget(self.connect_btn)
        layout.addWidget(time_group)
        layout.addWidget(payload_group)
        layout.addWidget(axis_group)
        layout.addWidget(self.apply_btn)
        layout.addStretch()
        self.refresh_serial_ports()
        
    def _apply_plot_config(self):
        config = {}

        for w in self.y_controls:
            config[w.signal_id] = {
                "visible": w.enable_checkbox.isChecked(),
                "y_min": w.min_edit.value(),
                "y_max": w.max_edit.value(),
                "lock": w.lock_checkbox.isChecked(),
            }

        self.plot_area.apply_plot_config(config)
        
    def _on_time_config_changed(self):
        period_ms = self.sample_period_edit.value()
        samples = self.sample_count_edit.value()
        self.plot_area.set_time_config(period_ms, samples)

    def _collapse_others(self, opened, groups):
        for g in groups:
            if g is not opened:
                g.toggle.setChecked(False)
                
    def _on_stream_changed(self, index: int):
        stream_id = self.payload_combo.itemData(index)
        if not stream_id:
            return

        stream_cfg = self.stream_loader.get_stream(stream_id)
        self._build_signal_panel(stream_cfg)
        self.plot_area.configure_signals(stream_cfg)

    def _on_group_expanded(self, opened):
        for g in self._accordion_groups:
            if g is not opened:
                g.toggle.setChecked(False)

        
    def _build_signal_panel(self, stream_cfg: dict):
        # Clear previous
        while self.axis_layout.count():
            item = self.axis_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.y_controls = []

        # Create groups sorted by order
        groups_cfg = stream_cfg["groups"]
        signals_cfg = stream_cfg["signals"]

        groups = {}
        for group_id, g in sorted(groups_cfg.items(), key=lambda x: x[1]["order"]):
            group = CollapsibleGroup(g["label"])
            self.axis_layout.addWidget(group)
            groups[group_id] = group

        # Add signals
        for sig_id, sig in signals_cfg.items():
            widget = YAxisControlWidget(sig["label"], sig["color"])
            widget.signal_id = sig_id  # ← ważne

            # --- default values from JSON ---
            widget.min_edit.setValue(sig["y_range"]["min"])
            widget.max_edit.setValue(sig["y_range"]["max"])
            widget.enable_checkbox.setChecked(sig.get("visible", True))

            # --- UI → PlotArea bindings ---
            plot = self.plot_area  # ControlPanel → MainWindow → PlotArea

            widget.enable_checkbox.toggled.connect(
                lambda checked, sid=sig_id: plot.set_signal_visible(sid, checked)
            )

            widget.lock_checkbox.toggled.connect(
                lambda locked, sid=sig_id: plot.set_signal_lock(sid, locked)
            )

            # --- add to UI ---
            groups[sig["group"]].content_layout.addWidget(widget)

            separator = QtWidgets.QFrame()
            separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            separator.setStyleSheet("color: white;")
            groups[sig["group"]].content_layout.addWidget(separator)

            self.y_controls.append(widget)

        self.axis_layout.addStretch()

        # Accordion behavior
        group_list = list(groups.values())
        for g in group_list:
            g.expanded.connect(self._on_group_expanded)

        self._accordion_groups = group_list

        # Open first group by default
        if group_list:
            group_list[0].toggle.setChecked(True)
            
    def refresh_serial_ports(self):
        self.port_combo.clear()

        ports = list_ports.comports()
        for p in ports:
            # p.device = np. /dev/tty.usbmodemXXXX lub COM3
            # p.description = opis urządzenia
            label = f"{p.device}  ({p.description})"
            self.port_combo.addItem(label, p.device)

        if self.port_combo.count() == 0:
            self.port_combo.addItem("No ports found", None)

# -----------------------------
# Plot area
# -----------------------------
class PlotArea(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        
        self.fake_time = 0.0
        self.fake_data = {}
        
        self.sample_period_ms = 50.0
        self.sample_period_s = self.sample_period_ms / 1000.0
        self.max_samples = 200
        self.sample_index = 0

        self.signal_views = {}
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
        
        self.fake_timer = QtCore.QTimer(self)
        self.fake_timer.timeout.connect(self._fake_step)
        
    def apply_plot_config(self, config: dict):
        # 1. Stop drawing
        self.fake_timer.stop()
        self.sample_index = 0

        # 2. Apply per-signal config
        for sig_id, cfg in config.items():
            view = self.signal_views.get(sig_id)
            if not view:
                continue

            curve = view["curve"]
            vb = view["viewbox"]

            curve.setVisible(cfg["visible"])

            if cfg["lock"]:
                vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
                vb.setYRange(cfg["y_min"], cfg["y_max"], padding=0)
            else:
                vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)

        # 3. HARD reset buffers
        self.fake_data.clear()
        for sig_id in self.signal_views.keys():
            self.fake_data[sig_id] = []

        # 4. HARD reset X axis
        base_vb = self.plot.getViewBox()
        base_vb.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)

        window_t = self.max_samples * self.sample_period_s
        base_vb.setXRange(0.0, window_t, padding=0)

        # 5. Restart drawing
        self.fake_timer.start(int(self.sample_period_ms))
        
    def set_time_config(self, sample_period_ms: float, max_samples: int):
        self.sample_period_ms = sample_period_ms
        self.sample_period_s = sample_period_ms / 1000.0
        self.max_samples = max_samples

        # Trim buffers if needed
        for buf in self.fake_data.values():
            while len(buf) > self.max_samples:
                buf.pop(0)
        
    def _init_fake_data(self):
        self.fake_time = 0.0
        self.fake_data.clear()

        for sig_id in self.signal_views.keys():
            self.fake_data[sig_id] = []
            
    def _fake_step(self):
        # Example PID-like behavior
        setpoint = math.sin(self.sample_index * self.sample_period_s * 0.5)
        measurement = setpoint + random.uniform(-0.05, 0.05)
        error = setpoint - measurement

        p = error * 20.0
        i = math.sin(self.sample_index * self.sample_period_s * 0.2) * 5.0
        outputRaw = p + i
        output = max(min(outputRaw, 100), -100)

        values = {
            "setpoint": setpoint,
            "measurement": measurement,
            "error": error,
            "p_term": p,
            "i_term": i,
            "outputRaw": outputRaw,
            "output": output,
        }

        for sig_id, v in values.items():
            if sig_id not in self.fake_data:
                continue

            buf = self.fake_data[sig_id]
            buf.append(v)

            if len(buf) > self.max_samples:
                buf.pop(0)

            start_index = self.sample_index - len(buf) + 1
            x = [
                (start_index + i) * self.sample_period_s
                for i in range(len(buf))
            ]

            self.signal_views[sig_id]["curve"].setData(x, buf)

        # --- advance time ---
        self.sample_index += 1

        # --- auto-scroll X axis (TIME) ---
        end_t = self.sample_index * self.sample_period_s
        window_t = self.max_samples * self.sample_period_s
        start_t = max(0.0, end_t - window_t)

        self.plot.getViewBox().setXRange(start_t, end_t, padding=0)


    def configure_signals(self, stream_cfg: dict):
        # Clear previous
        self.plot.clear()
        self.plot.setLabel("bottom", "Time [s]")
        self.signal_views.clear()

        base_vb = self.plot.getViewBox()
        base_vb.setMouseEnabled(x=True, y=False)
        # Hide default Y axes (we use logical Y ranges per ViewBox)
        self.plot.getAxis("left").setVisible(False)
        self.plot.getAxis("right").setVisible(False)

        for sig_id, sig in stream_cfg["signals"].items():
            vb = pg.ViewBox()
            self.plot.scene().addItem(vb)
            vb.setXLink(base_vb)
            vb.setMouseEnabled(x=False, y=False)

            pen = pg.mkPen(
                color=sig["color"],
                width=sig.get("line", {}).get("width", 2),
                style=LINE_STYLE_MAP.get(
                    sig.get("line", {}).get("style", "solid"),
                    QtCore.Qt.PenStyle.SolidLine
                )
            )

            curve = pg.PlotDataItem(
                        pen=pen,
                        name=sig["label"],
                        clipToView=True,
                        downsample=1,          # aktywuje mechanizm
                        autoDownsample=True,
                        downsampleMethod="peak"
                    )
            
            vb.addItem(curve)

            # Default Y range
            yr = sig["y_range"]
            vb.setYRange(yr["min"], yr["max"], padding=0)

            self.signal_views[sig_id] = {
                "viewbox": vb,
                "curve": curve
            }

        base_vb.sigResized.connect(self._sync_views)
        self._init_fake_data()
        self.fake_timer.start(50)  # 20 Hz
        
    def _sync_views(self):
        rect = self.plot.getViewBox().sceneBoundingRect()
        for s in self.signal_views.values():
            s["viewbox"].setGeometry(rect)
            
    def set_signal_visible(self, sig_id: str, visible: bool):
        self.signal_views[sig_id]["curve"].setVisible(visible)


    def set_signal_y_range(self, sig_id: str, y_min: float, y_max: float):
        vb = self.signal_views[sig_id]["viewbox"]

        # 🔑 WYŁĄCZ AUTORANGE zanim ustawisz zakres
        vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)

        vb.setYRange(y_min, y_max, padding=0)

    def set_signal_lock(self, sig_id: str, locked: bool):
        vb = self.signal_views[sig_id]["viewbox"]
        vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=not locked)


LINE_STYLE_MAP = {
    "solid": QtCore.Qt.PenStyle.SolidLine,
    "dashed": QtCore.Qt.PenStyle.DashLine,
    "dotted": QtCore.Qt.PenStyle.DotLine,
}


## Widget kontroli osi Y
class YAxisControlWidget(QtWidgets.QWidget):
    def __init__(self, name: str, color: str):
        super().__init__()
        
        self._debounce_timer = QtCore.QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)

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
        self.control_panel.plot_area = self.plot_area
        if self.control_panel.payload_combo.count() > 0:
            self.control_panel._on_stream_changed(0)

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
