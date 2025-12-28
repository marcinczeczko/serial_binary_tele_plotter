import sys
import json
from pathlib import Path
import math
from enum import Enum
import random
import numpy as np
from collections import deque
from serial.tools import list_ports

from PyQt6 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

class PlotMode(Enum):
    LIVE = 1
    ANALYSIS = 2

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
        QCheckBox { spacing: 6px; color: #ddd; }
        QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #666; background-color: #111; }
        QCheckBox::indicator:checked { background-color: #4FC3F7; border: 1px solid #4FC3F7; }
        QDoubleSpinBox, QSpinBox { padding: 2px; background-color: #1e1e1e; border: 1px solid #333; color: #fff; }
        QGroupBox { border: 1px solid #333; margin-top: 6px; padding-top: 10px; font-weight: bold; color: #aaa; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px; }
        QPushButton { background-color: #333; border: 1px solid #555; border-radius: 3px; padding: 5px; color: white; }
        QPushButton:hover { background-color: #444; }
        QPushButton:pressed { background-color: #222; }
        QPushButton:checked { background-color: #555; border: 1px solid #888; }
        QComboBox { background-color: #1e1e1e; border: 1px solid #333; color: white; padding: 4px; }
        QStatusBar { color: #888; }
    """)

# -----------------------------
# Data & Logic Worker
# -----------------------------
class TelemetryWorker(QtCore.QObject):
    data_ready = QtCore.pyqtSignal(dict)

    def __init__(self, sample_period_ms: float, max_samples: int):
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0
        self.max_samples = max_samples
        
        self.sample_index = 0
        self.buffers = {}
        self.scale = {}
        self.timer = None

    @QtCore.pyqtSlot()
    def start_working(self):
        """Uruchamia timer generujący dane"""
        if self.timer is None:
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self._step)
        
        self.timer.start(int(self.sample_period_s * 1000))

    @QtCore.pyqtSlot()
    def stop_working(self):
        """Zatrzymuje timer"""
        if self.timer:
            self.timer.stop()

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms: float, max_samples: int):
        self.sample_period_s = period_ms / 1000.0
        self.max_samples = max_samples
        
        # Reset buforów
        new_buffers = {}
        for k in self.buffers.keys():
            new_buffers[k] = deque(maxlen=self.max_samples)
        self.buffers = new_buffers
        
        if self.timer and self.timer.isActive():
            self.timer.setInterval(int(period_ms))

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        self.buffers.clear()
        self.scale.clear()

        for sig_id, sig in signals_cfg.items():
            self.buffers[sig_id] = deque(maxlen=self.max_samples)
            yr = sig["y_range"]
            self.scale[sig_id] = (yr["min"], yr["max"])

        self.sample_index = 0

    @QtCore.pyqtSlot(str, float, float)
    def update_scale(self, sig_id: str, ymin: float, ymax: float):
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)

    def _step(self):
        t = self.sample_index * self.sample_period_s

        # Mock generator
        setpoint = math.sin(t * 0.5)
        measurement = setpoint + random.uniform(-0.05, 0.05)
        error = setpoint - measurement

        values = {
            "setpoint": setpoint,
            "measurement": measurement,
            "error": error,
            "p_term": error * 20.0,
            "i_term": math.sin(t * 0.2) * 5.0,
            "outputRaw": error * 30.0,
            "output": max(min(error * 30.0, 100), -100),
        }

        for k, v in values.items():
            if k in self.buffers:
                self.buffers[k].append(v)

        self.sample_index += 1
        self._emit()

    def _emit(self):
        if not self.buffers: return
        try:
            first_buf = next(iter(self.buffers.values()))
        except StopIteration: return

        n = len(first_buf)
        if n < 2: return

        t0 = (self.sample_index - n) * self.sample_period_s
        time = np.linspace(t0, t0 + n * self.sample_period_s, n, endpoint=False)

        out_signals_norm = {}
        out_signals_raw = {}

        for sig_id, buf in self.buffers.items():
            arr = np.array(buf)
            out_signals_raw[sig_id] = arr

            if sig_id not in self.scale: continue
            ymin, ymax = self.scale[sig_id]
            scale = max(ymax - ymin, 1e-12)
            y_norm = np.clip((arr - ymin) / scale, 0.0, 1.0)
            out_signals_norm[sig_id] = y_norm

        self.data_ready.emit({
            "time": time,
            "t0": t0,
            "dt": self.sample_period_s,
            "signals": out_signals_norm,
            "raw": out_signals_raw,
            "count": self.sample_index
        })


# -----------------------------
# Config Loader (Bez zmian)
# -----------------------------
class StreamConfigLoader:
    def __init__(self, path: str):
        self.path = Path(path)
        if not self.path.exists():
            # Default mock config
            self.data = {
                "streams": {
                    "pid_control": {
                        "name": "PID Controller",
                        "groups": {
                            "main": {"label": "Main Variables", "order": 1},
                            "terms": {"label": "PID Terms", "order": 2},
                            "out": {"label": "Output", "order": 3}
                        },
                        "signals": {
                            "setpoint": {"label": "Setpoint", "color": "#00FF00", "group": "main", "y_range": {"min": -1.5, "max": 1.5}},
                            "measurement": {"label": "Measurement", "color": "#FF0000", "group": "main", "y_range": {"min": -1.5, "max": 1.5}},
                            "error": {"label": "Error", "color": "#FFFF00", "group": "main", "y_range": {"min": -0.5, "max": 0.5}},
                            "p_term": {"label": "P Term", "color": "#00FFFF", "group": "terms", "y_range": {"min": -50, "max": 50}},
                            "i_term": {"label": "I Term", "color": "#FF00FF", "group": "terms", "y_range": {"min": -10, "max": 10}},
                            "output": {"label": "Motor Output", "color": "#FFFFFF", "group": "out", "y_range": {"min": -100, "max": 100}}
                        }
                    }
                }
            }
        else:
            self._load()

    def _load(self):
        with self.path.open("r", encoding="utf-8") as f:
            self.data = json.load(f)

    def list_streams(self):
        return self.data["streams"]

    def get_stream(self, stream_id: str):
        return self.data["streams"][stream_id]


# -----------------------------
# UI Components
# -----------------------------
class CollapsibleGroup(QtWidgets.QWidget):
    expanded = QtCore.pyqtSignal(object)
    def __init__(self, title: str):
        super().__init__()
        self.toggle = QtWidgets.QToolButton(text=title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; color: #ccc; }")
        self.content = QtWidgets.QWidget()
        self.content.setVisible(False)
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(10, 0, 0, 0)
        self.toggle.toggled.connect(self._on_toggled)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
    def _on_toggled(self, checked: bool):
        self.content.setVisible(checked)
        self.toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)
        if checked: self.expanded.emit(self)

class YAxisControlWidget(QtWidgets.QWidget):
    def __init__(self, name: str, color: str):
        super().__init__()
        self.signal_id = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 8)
        layout.setSpacing(4)
        h_layout = QtWidgets.QHBoxLayout()
        color_lbl = QtWidgets.QLabel("●")
        color_lbl.setStyleSheet(f"color: {color}; font-size: 16px;")
        self.enable_checkbox = QtWidgets.QCheckBox(name)
        self.enable_checkbox.setChecked(True)
        h_layout.addWidget(color_lbl)
        h_layout.addWidget(self.enable_checkbox)
        h_layout.addStretch()
        r_layout = QtWidgets.QHBoxLayout()
        self.min_edit = QtWidgets.QDoubleSpinBox()
        self.max_edit = QtWidgets.QDoubleSpinBox()
        for w in (self.min_edit, self.max_edit):
            w.setRange(-1e6, 1e6)
            w.setDecimals(2)
            w.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        r_layout.addWidget(QtWidgets.QLabel("Min:"))
        r_layout.addWidget(self.min_edit)
        r_layout.addWidget(QtWidgets.QLabel("Max:"))
        r_layout.addWidget(self.max_edit)
        self.lock_checkbox = QtWidgets.QCheckBox("Lock Y")
        layout.addLayout(h_layout)
        layout.addLayout(r_layout)
        layout.addWidget(self.lock_checkbox)

class ControlPanel(QtWidgets.QWidget):
    scale_changed = QtCore.pyqtSignal(str, float, float)
    stream_changed = QtCore.pyqtSignal(dict)
    time_config_changed = QtCore.pyqtSignal(float, int)
    signal_visibility_changed = QtCore.pyqtSignal(str, bool)
    signal_lock_changed = QtCore.pyqtSignal(str, bool)
    
    # Nowe sygnały sterujące
    connection_requested = QtCore.pyqtSignal(bool) # True=Connect, False=Disconnect
    pause_requested = QtCore.pyqtSignal(bool)      # True=Pause, False=Resume

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)

        # Serial Group
        grp_serial = QtWidgets.QGroupBox("Serial Connection")
        l_serial = QtWidgets.QGridLayout(grp_serial)
        self.port_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("⟳")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])
        l_serial.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        l_serial.addWidget(self.port_combo, 0, 1)
        l_serial.addWidget(self.refresh_btn, 0, 2)
        l_serial.addWidget(QtWidgets.QLabel("Baud:"), 1, 0)
        l_serial.addWidget(self.baud_combo, 1, 1, 1, 2)
        layout.addWidget(grp_serial)

        # Time Group
        grp_time = QtWidgets.QGroupBox("Time Window")
        l_time = QtWidgets.QGridLayout(grp_time)
        self.sample_period_edit = QtWidgets.QDoubleSpinBox()
        self.sample_period_edit.setRange(1, 1000)
        self.sample_period_edit.setValue(50.0)
        self.sample_period_edit.setSuffix(" ms")
        self.sample_count_edit = QtWidgets.QSpinBox()
        self.sample_count_edit.setRange(10, 10000)
        self.sample_count_edit.setValue(200)
        
        self.sample_period_edit.valueChanged.connect(self._emit_time_config)
        self.sample_count_edit.valueChanged.connect(self._emit_time_config)

        l_time.addWidget(QtWidgets.QLabel("Period:"), 0, 0)
        l_time.addWidget(self.sample_period_edit, 0, 1)
        l_time.addWidget(QtWidgets.QLabel("Samples:"), 1, 0)
        l_time.addWidget(self.sample_count_edit, 1, 1)
        layout.addWidget(grp_time)

        # Stream Selector
        grp_payload = QtWidgets.QGroupBox("Stream Type")
        l_payload = QtWidgets.QVBoxLayout(grp_payload)
        self.stream_loader = StreamConfigLoader("streams.json")
        self.payload_combo = QtWidgets.QComboBox()
        for sid, s in self.stream_loader.list_streams().items():
            self.payload_combo.addItem(s["name"], sid)
        self.payload_combo.currentIndexChanged.connect(self._on_stream_changed)
        l_payload.addWidget(self.payload_combo)
        layout.addWidget(grp_payload)

        # --- CONNECTION & CONTROL BUTTONS ---
        btn_layout = QtWidgets.QHBoxLayout()
        
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setStyleSheet("""
            QPushButton:checked { background-color: #C62828; } 
            QPushButton { background-color: #2E7D32; font-weight: bold; }
        """)
        self.connect_btn.toggled.connect(self._on_connect_toggled)

        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False) # Domyślnie nieaktywny
        self.pause_btn.toggled.connect(self._on_pause_toggled)

        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.pause_btn)
        layout.addLayout(btn_layout)
        
        # Apply Scale Btn
        self.apply_btn = QtWidgets.QPushButton("Apply Scales")
        self.apply_btn.clicked.connect(self._apply_scales)
        layout.addWidget(self.apply_btn)

        # Signals List
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.signals_container = QtWidgets.QWidget()
        self.signals_layout = QtWidgets.QVBoxLayout(self.signals_container)
        self.signals_layout.setSpacing(5)
        self.signals_layout.addStretch()
        scroll.setWidget(self.signals_container)
        
        grp_sigs = QtWidgets.QGroupBox("Signals")
        l_sigs = QtWidgets.QVBoxLayout(grp_sigs)
        l_sigs.addWidget(scroll)
        layout.addWidget(grp_sigs, 1)

        self.y_controls = []
        self._accordion_groups = []
        self.refresh_ports()

    def refresh_ports(self):
        self.port_combo.clear()
        for p in list_ports.comports():
            self.port_combo.addItem(f"{p.device}", p.device)

    def _emit_time_config(self):
        p = self.sample_period_edit.value()
        s = self.sample_count_edit.value()
        self.time_config_changed.emit(p, s)

    def _on_connect_toggled(self, checked):
        if checked:
            self.connect_btn.setText("Disconnect")
            self.pause_btn.setEnabled(True)
            self.connection_requested.emit(True)
        else:
            self.connect_btn.setText("Connect")
            self.pause_btn.setChecked(False)
            self.pause_btn.setEnabled(False)
            self.connection_requested.emit(False)

    def _on_pause_toggled(self, checked):
        if checked:
            self.pause_btn.setText("Resume")
            self.pause_btn.setStyleSheet("background-color: #F57F17; color: black; font-weight: bold;")
        else:
            self.pause_btn.setText("Pause")
            self.pause_btn.setStyleSheet("")
        self.pause_requested.emit(checked)

    def _on_stream_changed(self, idx):
        sid = self.payload_combo.itemData(idx)
        if not sid: return
        cfg = self.stream_loader.get_stream(sid)
        self._rebuild_signal_list(cfg)
        self.stream_changed.emit(cfg["signals"])

    def _rebuild_signal_list(self, cfg):
        while self.signals_layout.count():
            item = self.signals_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        
        self.y_controls = []
        self._accordion_groups = []
        groups = {}

        for gid, gdata in sorted(cfg["groups"].items(), key=lambda x: x[1]["order"]):
            grp_widget = CollapsibleGroup(gdata["label"])
            grp_widget.expanded.connect(self._on_group_expanded)
            self.signals_layout.addWidget(grp_widget)
            groups[gid] = grp_widget
            self._accordion_groups.append(grp_widget)

        for sid, sdata in cfg["signals"].items():
            w = YAxisControlWidget(sdata["label"], sdata["color"])
            w.signal_id = sid
            w.min_edit.setValue(sdata["y_range"]["min"])
            w.max_edit.setValue(sdata["y_range"]["max"])
            w.enable_checkbox.toggled.connect(lambda c, s=sid: self.signal_visibility_changed.emit(s, c))
            w.lock_checkbox.toggled.connect(lambda c, s=sid: self.signal_lock_changed.emit(s, c))
            
            target_group = groups.get(sdata["group"])
            if target_group:
                target_group.content_layout.addWidget(w)
                line = QtWidgets.QFrame()
                line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
                line.setStyleSheet("background-color: #333;")
                target_group.content_layout.addWidget(line)
            self.y_controls.append(w)

        self.signals_layout.addStretch()
        if self._accordion_groups:
            self._accordion_groups[0].toggle.setChecked(True)

    def _on_group_expanded(self, sender):
        for g in self._accordion_groups:
            if g is not sender: g.toggle.setChecked(False)

    def _apply_scales(self):
        for w in self.y_controls:
            self.scale_changed.emit(w.signal_id, w.min_edit.value(), w.max_edit.value())


class PlotArea(QtWidgets.QWidget):
    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.mode = PlotMode.LIVE
        self.signal_views = {}
        self.last_packet = None      # Strumień "żywy" (zawsze aktualny)
        self.analysis_packet = None  # Strumień "zamrożony" do analizy na pauzie
        self.signal_colors = {} 
        self.anchor_time = None 
        self.anchor_values = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)

        self.plot = self.graphics.addPlot()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "Time [s]")
        self.plot.getAxis("left").setVisible(False)

        # --- CURSORS ---
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#888', width=1))
        self.vLine.setAcceptHoverEvents(False)
        self.plot.addItem(self.vLine, ignoreBounds=True)
        
        self.anchorLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('y', style=QtCore.Qt.PenStyle.DashLine, width=2))
        self.anchorLine.setAcceptHoverEvents(False)
        self.anchorLine.setVisible(False)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)

        self.label = pg.TextItem(anchor=(0, 0))
        self.label.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self.label.setAcceptHoverEvents(False)
        self.plot.addItem(self.label, ignoreBounds=True)

        self.plot.scene().sigMouseMoved.connect(self.mouse_moved_handler)
        self.plot.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        self.plot.sigRangeChanged.connect(self.update_hud_position)

    @QtCore.pyqtSlot(dict)
    def on_data_ready(self, packet: dict):
        # Zawsze trzymamy najświeższy pakiet pod ręką (na wypadek Resume)
        self.last_packet = packet
        
        # Jeśli jesteśmy w trybie analizy, kompletnie ignorujemy nowe dane w GUI
        if self.mode == PlotMode.ANALYSIS:
            return
        
        time = packet["time"]
        signals = packet["signals"]
        if len(time) == 0: return

        for sig_id, y in signals.items():
            if sig_id in self.signal_views:
                self.signal_views[sig_id]["curve"].setData(time, y)

        if self.mode == PlotMode.LIVE:
            self.plot.setXRange(time[0], time[-1], padding=0)
            self.update_hud_position()

    @QtCore.pyqtSlot(bool)
    def set_paused(self, paused: bool):
        if paused:
            self.mode = PlotMode.ANALYSIS
            # KLUCZOWE: Robimy migawkę danych w momencie pauzy
            # Od teraz kursor i delta działają TYLKO na tych danych
            import copy
            self.analysis_packet = copy.deepcopy(self.last_packet)
        else:
            self.mode = PlotMode.LIVE
            self.analysis_packet = None
            self.anchor_time = None
            self.anchorLine.setVisible(False)
            self.anchor_values = {}
            self.label.setHtml("")

    def mouse_moved_handler(self, pos):
        vb = self.plot.vb
        if vb.sceneBoundingRect().contains(pos):
            mousePoint = vb.mapSceneToView(pos)
            self._process_mouse_movement(mousePoint.x())

    def _process_mouse_movement(self, x_raw):
        # Wybieramy źródło danych: zamrożone (pauza) lub żywe (live)
        data_source = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        
        if not data_source: return
        time_arr = data_source["time"]
        if len(time_arr) < 2: return

        # Kursor nie ucieknie, bo time_arr jest zamrożone w czasie!
        x_clamped = float(np.clip(x_raw, time_arr[0], time_arr[-1]))
        self.vLine.setPos(x_clamped)
        self.update_tooltip(x_clamped)

    def on_mouse_clicked(self, evt):
        if self.mode != PlotMode.ANALYSIS: return
        if evt.button() != QtCore.Qt.MouseButton.LeftButton: return
        
        vb = self.plot.vb
        if vb.sceneBoundingRect().contains(evt.scenePos()):
            mousePoint = vb.mapSceneToView(evt.scenePos())
            self.anchor_time = mousePoint.x()
            self.anchorLine.setPos(self.anchor_time)
            self.anchorLine.setVisible(True)
            
            # Pobieramy wartości z zamrożonego pakietu
            self._capture_anchor_values(self.anchor_time)
            self._process_mouse_movement(self.anchor_time)

    def _capture_anchor_values(self, t_anchor):
        if not self.analysis_packet: return
        time_arr = self.analysis_packet["time"]
        raw_data = self.analysis_packet["raw"]
        
        self.anchor_values = {}
        for sig_id, values in raw_data.items():
            self.anchor_values[sig_id] = np.interp(t_anchor, time_arr, values)

    def update_hud_position(self):
        vb = self.plot.getViewBox()
        xr, yr = vb.viewRange()
        x_text = xr[0] + 0.01 * (xr[1] - xr[0])
        y_text = yr[1] - 0.02 * (yr[1] - yr[0])
        self.label.setPos(x_text, y_text)

    def update_tooltip(self, x_pos):
        # Wybieramy źródło danych: zamrożone (pauza) lub żywe (live)
        data_source = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        
        if not data_source: return
        time_arr = data_source["time"]
        raw_data = data_source["raw"]
        
        current_time = float(x_pos)
        
        dt = time_arr[1] - time_arr[0]
        idx = int(np.clip((current_time - time_arr[0]) / dt, 0, len(time_arr) - 1))
        self.cursor_moved.emit(f"Cursor: {current_time:.3f} s | Index: {idx}")

        html_str = '<div style="background-color: rgba(0, 0, 0, 0.7); padding: 6px; font-family: Consolas, monospace; border: 1px solid #444;">'
        html_str += f'<b style="color: white; font-size: 12px;">T: {current_time:.3f} s</b>'
        
        if self.anchor_time is not None:
            delta_t = current_time - self.anchor_time
            html_str += f' <span style="color: #FFD700; font-size: 11px;">(Δ {delta_t:+.3f} s)</span>'
        
        html_str += "<br><hr style='margin: 4px 0;'>"

        for sig_id, values in raw_data.items():
            if sig_id in self.signal_views and not self.signal_views[sig_id]["curve"].isVisible():
                continue
            
            val = float(np.interp(current_time, time_arr, values))
            color = self.signal_colors.get(sig_id, "#FFF")
            row = f'<span style="color: {color};">{sig_id}: <b>{val:>8.4f}</b>'
            
            if self.anchor_time is not None and sig_id in self.anchor_values:
                dy = val - self.anchor_values[sig_id]
                row += f' <span style="color: #aaa; font-size: 10px;">(Δ {dy:+.4f})</span>'
            html_str += row + '</span><br>'
        
        html_str += "</div>"
        self.label.setHtml(html_str)
        self.update_hud_position()

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        for sig_id in list(self.signal_views.keys()):
            old_vb = self.signal_views[sig_id]["viewbox"]
            self.plot.scene().removeItem(old_vb)
        
        self.plot.clear()
        self.signal_views.clear()
        self.signal_colors.clear()

        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)
        self.plot.addItem(self.label, ignoreBounds=True)
        
        base_vb = self.plot.getViewBox()
        for sig_id, sig in signals_cfg.items():
            self.signal_colors[sig_id] = sig["color"]
            vb = pg.ViewBox()
            vb.setMouseEnabled(x=False, y=False)
            vb.enableAutoRange(pg.ViewBox.YAxis, False)
            vb.setYRange(0.0, 1.0) 
            self.plot.scene().addItem(vb)
            vb.setXLink(base_vb)
            
            pen = pg.mkPen(color=sig["color"], width=2)
            curve = pg.PlotDataItem(pen=pen, skipFiniteCheck=True)
            vb.addItem(curve)
            self.signal_views[sig_id] = {"viewbox": vb, "curve": curve}
        base_vb.sigResized.connect(self._update_views)
    
    def _update_views(self):
        rect = self.plot.getViewBox().sceneBoundingRect()
        for s in self.signal_views.values():
            s["viewbox"].setGeometry(rect)

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id, visible):
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["curve"].setVisible(visible)
            # Odśwież HUD używając odpowiedniego źródła danych
            if self.mode == PlotMode.ANALYSIS and self.analysis_packet:
                self._process_mouse_movement(self.vLine.value())
            elif self.last_packet:
                self._process_mouse_movement(self.vLine.value())

    @QtCore.pyqtSlot(str, bool)
    def set_signal_lock(self, sig_id, locked):
        pass

# -----------------------------
# Main Window
# -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DiffBot Telemetry Viewer (Pro)")
        self.resize(1280, 800)

        # --- STATUS BAR ---
        self.status_bar = self.statusBar()
        self.lbl_status = QtWidgets.QLabel("Ready")
        self.lbl_cursor = QtWidgets.QLabel("")
        self.status_bar.addWidget(self.lbl_status)
        self.status_bar.addPermanentWidget(self.lbl_cursor)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        self.panel = ControlPanel()
        self.plot = PlotArea()
        
        splitter.addWidget(self.panel)
        splitter.addWidget(self.plot)
        splitter.setSizes([350, 930])

        # --- Worker Setup ---
        init_period = self.panel.sample_period_edit.value()
        init_samples = self.panel.sample_count_edit.value()

        self.worker = TelemetryWorker(init_period, init_samples)
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(lambda: self.lbl_status.setText("Worker Thread Started"))
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._cleanup)

        # Wiring
        self.panel.scale_changed.connect(self.worker.update_scale)
        self.panel.stream_changed.connect(self.worker.configure_signals)
        self.panel.time_config_changed.connect(self.worker.update_time_config)
        
        # Connect / Pause Logic wiring
        self.panel.connection_requested.connect(self._handle_connection)
        self.panel.pause_requested.connect(self._handle_pause)

        self.panel.stream_changed.connect(self.plot.configure_signals)
        self.panel.signal_visibility_changed.connect(self.plot.set_signal_visible)
        self.panel.signal_lock_changed.connect(self.plot.set_signal_lock)

        self.worker.data_ready.connect(self.plot.on_data_ready)
        self.plot.cursor_moved.connect(self.lbl_cursor.setText) # Aktualizacja status bara

        self.thread.start()
        self.panel._on_stream_changed(self.panel.payload_combo.currentIndex())

    def _handle_connection(self, connect: bool):
        if connect:
            # Używamy invokeMethod, aby wywołać slot w wątku Workera (bezpiecznie)
            QtCore.QMetaObject.invokeMethod(self.worker, "start_working", QtCore.Qt.ConnectionType.QueuedConnection)
            self.lbl_status.setText("Connected: Receiving Data")
            self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")
        else:
            QtCore.QMetaObject.invokeMethod(self.worker, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection)
            self.lbl_status.setText("Disconnected")
            self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")

    def _handle_pause(self, paused: bool):
        self.plot.set_paused(paused)
        if paused:
            self.lbl_status.setText("PAUSED - Click chart to set Delta Anchor")
            self.lbl_status.setStyleSheet("color: #FF9800; font-weight: bold;")
        else:
            self.lbl_status.setText("Connected: Receiving Data")
            self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")

    def _cleanup(self):
        self.thread.quit()
        self.thread.wait()

def main():
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()