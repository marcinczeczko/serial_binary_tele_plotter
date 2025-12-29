import json
import math
import random
import signal
import struct
import sys
from collections import deque
from enum import Enum
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import serial
from PyQt6 import QtCore, QtWidgets
from serial.tools import list_ports


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

    app.setStyleSheet(
        """
        QCheckBox { spacing: 6px; color: #ddd; }
        QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #666; background-color: #111; }
        QCheckBox::indicator:checked { background-color: #4FC3F7; border: 1px solid #4FC3F7; }
        QDoubleSpinBox, QSpinBox, QLineEdit { padding: 2px; background-color: #1e1e1e; border: 1px solid #333; color: #fff; }
        QGroupBox { border: 1px solid #333; margin-top: 6px; padding-top: 10px; font-weight: bold; color: #aaa; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px; }
        QPushButton { background-color: #333; border: 1px solid #555; border-radius: 3px; padding: 5px; color: white; }
        QPushButton:hover { background-color: #444; }
        QPushButton:pressed { background-color: #222; }
        QPushButton:checked { background-color: #555; border: 1px solid #888; }
        QComboBox { background-color: #1e1e1e; border: 1px solid #333; color: white; padding: 4px; }
        QStatusBar { color: #888; }
    """
    )


# -----------------------------
# Data & Logic Worker
# -----------------------------
class TelemetryWorker(QtCore.QObject):
    data_ready = QtCore.pyqtSignal(dict)
    status_msg = QtCore.pyqtSignal(str)

    def __init__(self, sample_period_ms: float, max_samples: int):
        super().__init__()
        self.sample_period_s = sample_period_ms / 1000.0
        self.max_samples = max_samples
        self.sample_index = 0
        self.buffers = {}
        self.scale = {}

        self.timer = None
        self.serial_port = None
        self.is_running = False

        self.MAGIC_0 = 0xAA
        self.MAGIC_1 = 0x55
        self.RTP_PID = 0x01
        self.RTP_REQ_PID = 0x10

    def calculate_crc8(self, data):
        crc = 0x00
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x07) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    @QtCore.pyqtSlot(str, int)
    def start_working(self, port_name, baudrate):
        self.is_running = True

        if port_name == "VIRTUAL":
            if self.timer is None:
                self.timer = QtCore.QTimer(self)
                self.timer.timeout.connect(self._step)
            self.timer.start(int(self.sample_period_s * 1000))
        else:
            try:
                # Otwieramy port
                self.serial_port = serial.Serial(port_name, baudrate, timeout=0.1)
                self.serial_port.reset_input_buffer()
                # Zamiast blokującej pętli while, uruchamiamy pierwszy krok
                QtCore.QTimer.singleShot(0, self._serial_read_step)
            except Exception as e:
                self.status_msg.emit(f"Serial Error: {e}")
                self.is_running = False

    @QtCore.pyqtSlot()
    def stop_working(self):
        """Ta funkcja jest wywoływana przez sygnał z GUI"""
        self.is_running = False

        if self.timer:
            self.timer.stop()

        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None

    def _serial_read_step(self):
        """
        Funkcja wykonuje jeden cykl odczytu i planuje swoje kolejne wywołanie.
        Dzięki temu Event Loop nie jest zablokowany.
        """
        if not self.is_running or not self.serial_port:
            return

        try:
            # Czytamy wszystko co jest w buforze, żeby nie robić opóźnień
            # Ale w pętlach max np. 10-20 iteracji, żeby nie zamrozić UI przy floodzie danych
            iterations = 0
            while self.serial_port and self.serial_port.in_waiting >= 5 and iterations < 50:
                iterations += 1

                # Szukamy nagłówka (bezpieczniej czytać po 1 bajcie przy szukaniu synchro)
                b = self.serial_port.read(1)
                if not b or ord(b) != self.MAGIC_0:
                    continue

                b = self.serial_port.read(1)
                if not b or ord(b) != self.MAGIC_1:
                    continue

                # Czytamy resztę nagłówka
                h_data = self.serial_port.read(3)
                if len(h_data) < 3:
                    continue

                p_type, p_len, h_crc = struct.unpack("BBB", h_data)

                # CRC nagłówka
                header_check = struct.pack("BBBB", self.MAGIC_0, self.MAGIC_1, p_type, p_len)
                if self.calculate_crc8(header_check) != h_crc:
                    continue

                # Payload
                payload = self.serial_port.read(p_len)
                if len(payload) != p_len:
                    continue

                # CRC payloadu
                p_crc_raw = self.serial_port.read(1)
                if len(p_crc_raw) < 1:
                    continue
                p_crc = ord(p_crc_raw)

                if self.calculate_crc8(payload) == p_crc:
                    self._handle_payload(p_type, payload)

        except Exception as e:
            # Jeśli port został nagle zamknięty lub wyrwany
            print(f"Read Loop Error: {e}")
            self.stop_working()
            return

        # Jeśli nadal mamy działać, planujemy kolejne wywołanie "natychmiast" (po obsłużeniu zdarzeń)
        if self.is_running:
            QtCore.QTimer.singleShot(0, self._serial_read_step)

    def _handle_payload(self, p_type, payload):
        if p_type == self.RTP_PID:
            try:
                d = struct.unpack("<IBfffffff", payload)
                t = d[0] * self.sample_period_s
                values = {
                    "setpoint": d[2],
                    "measurement": d[3],
                    "error": d[4],
                    "p_term": d[5],
                    "i_term": d[6],
                    "output": d[8],
                }
                self._update_buffers(values, t)
            except struct.error:
                pass

    @QtCore.pyqtSlot(int, float, float, float)
    def send_pid_config(self, motor_id, kp, ki, kff):
        if not self.serial_port or not self.serial_port.is_open:
            return
        try:
            payload = struct.pack("<Bfff", motor_id, kp, ki, kff)
            h_base = struct.pack("BBBB", self.MAGIC_0, self.MAGIC_1, self.RTP_REQ_PID, len(payload))
            h_crc = self.calculate_crc8(h_base)
            p_crc = self.calculate_crc8(payload)
            full_frame = h_base + struct.pack("B", h_crc) + payload + struct.pack("B", p_crc)
            self.serial_port.write(full_frame)
        except Exception as e:
            print(f"Write Error: {e}")

    def _step(self):
        """Generator demo danych"""
        t = self.sample_index * self.sample_period_s
        setpoint = math.sin(t * 0.5)
        measurement = setpoint + random.uniform(-0.05, 0.05)
        error = setpoint - measurement
        values = {
            "setpoint": setpoint,
            "measurement": measurement,
            "error": error,
            "p_term": error * 20.0,
            "i_term": math.sin(t * 0.2) * 5.0,
            "output": max(min(error * 30.0, 100), -100),
        }
        self._update_buffers(values, t)
        self.sample_index += 1

    def _update_buffers(self, values, current_time):
        # 1. Dodaj nowe dane
        for k, v in values.items():
            if k in self.buffers:
                self.buffers[k].append(v)

        if not self.buffers:
            return

        # 2. Migawka (Snapshot) - zabezpieczenie przed modyfikacją w trakcie rysowania
        # Pobieramy tylko te bufory, które mają dane
        snapshot_raw = {}
        active_lengths = []

        for sig_id, buf in self.buffers.items():
            data_list = list(buf)
            if len(data_list) > 0:
                snapshot_raw[sig_id] = np.array(data_list)
                active_lengths.append(len(data_list))

        # Jeśli brak danych lub za mało próbek, wychodzimy
        if not active_lengths or max(active_lengths) < 2:
            return

        # 3. Synchronizacja długości (bierzemy minimum z aktywnych)
        min_len = min(active_lengths)
        for sig_id in snapshot_raw:
            if len(snapshot_raw[sig_id]) > min_len:
                snapshot_raw[sig_id] = snapshot_raw[sig_id][:min_len]

        # 4. Oś czasu
        time_axis = np.linspace(
            current_time - min_len * self.sample_period_s, current_time, min_len
        )

        # 5. Normalizacja
        out_signals_norm = {}
        for sig_id, arr in snapshot_raw.items():
            if sig_id in self.scale:
                ymin, ymax = self.scale[sig_id]
                scale = max(ymax - ymin, 1e-12)
                out_signals_norm[sig_id] = np.clip((arr - ymin) / scale, 0.0, 1.0)

        self.data_ready.emit({"time": time_axis, "signals": out_signals_norm, "raw": snapshot_raw})

    @QtCore.pyqtSlot(float, int)
    def update_time_config(self, period_ms, max_samples):
        self.sample_period_s = period_ms / 1000.0
        self.max_samples = max_samples
        for k in self.buffers.keys():
            self.buffers[k] = deque(maxlen=self.max_samples)
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
    def update_scale(self, sig_id, ymin, ymax):
        if sig_id in self.scale:
            self.scale[sig_id] = (ymin, ymax)


# -----------------------------
# Config Loader
# -----------------------------
class StreamConfigLoader:
    def __init__(self, path: str):
        self.path = Path(path)
        if not self.path.exists():
            self.data = {
                "streams": {
                    "pid_control": {
                        "name": "PID Controller",
                        "groups": {
                            "main": {"label": "Main Variables", "order": 1},
                            "terms": {"label": "PID Terms", "order": 2},
                            "out": {"label": "Output", "order": 3},
                        },
                        "signals": {
                            "setpoint": {
                                "label": "Setpoint",
                                "color": "#00FF00",
                                "group": "main",
                                "y_range": {"min": -1.5, "max": 1.5},
                            },
                            "measurement": {
                                "label": "Measurement",
                                "color": "#FF0000",
                                "group": "main",
                                "y_range": {"min": -1.5, "max": 1.5},
                            },
                            "error": {
                                "label": "Error",
                                "color": "#FFFF00",
                                "group": "main",
                                "y_range": {"min": -0.5, "max": 0.5},
                            },
                            "p_term": {
                                "label": "P Term",
                                "color": "#00FFFF",
                                "group": "terms",
                                "y_range": {"min": -50, "max": 50},
                            },
                            "i_term": {
                                "label": "I Term",
                                "color": "#FF00FF",
                                "group": "terms",
                                "y_range": {"min": -10, "max": 10},
                            },
                            "output": {
                                "label": "Motor Output",
                                "color": "#FFFFFF",
                                "group": "out",
                                "y_range": {"min": -100, "max": 100},
                            },
                        },
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
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)

    def _on_toggled(self, checked: bool):
        self.content.setVisible(checked)
        self.toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        )
        if checked:
            self.expanded.emit(self)


class YAxisControlWidget(QtWidgets.QWidget):
    def __init__(self, name: str, color: str):
        super().__init__()
        self.signal_id = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 8)
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
    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)
    pid_config_sent = QtCore.pyqtSignal(int, float, float, float)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)

        # Serial Connection
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

        # PID Tuning
        grp_pid = QtWidgets.QGroupBox("PID Tuning")
        l_pid = QtWidgets.QGridLayout(grp_pid)
        self.motor_selector = QtWidgets.QComboBox()
        self.motor_selector.addItem("Left Motor", 0)
        self.motor_selector.addItem("Right Motor", 1)
        self.motor_selector.addItem("Both Motors", 2)
        self.kp_sb = self._make_sb(1.0)
        self.ki_sb = self._make_sb(0.1)
        self.kff_sb = self._make_sb(0.5)
        self.pid_update_btn = QtWidgets.QPushButton("Update PID Parameters")
        self.pid_update_btn.clicked.connect(self._on_pid_update)
        l_pid.addWidget(QtWidgets.QLabel("Motor:"), 0, 0)
        l_pid.addWidget(self.motor_selector, 0, 1)
        l_pid.addWidget(QtWidgets.QLabel("Kp:"), 1, 0)
        l_pid.addWidget(self.kp_sb, 1, 1)
        l_pid.addWidget(QtWidgets.QLabel("Ki:"), 2, 0)
        l_pid.addWidget(self.ki_sb, 2, 1)
        l_pid.addWidget(QtWidgets.QLabel("Kff:"), 3, 0)
        l_pid.addWidget(self.kff_sb, 3, 1)
        l_pid.addWidget(self.pid_update_btn, 4, 0, 1, 2)
        layout.addWidget(grp_pid)

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

        # Connection Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setStyleSheet(
            "QPushButton:checked { background-color: #C62828; } QPushButton { background-color: #2E7D32; font-weight: bold; }"
        )
        self.connect_btn.toggled.connect(self._on_connect_toggled)
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.pause_btn)
        layout.addLayout(btn_layout)

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

    def _make_sb(self, val):
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(0, 1000)
        sb.setDecimals(4)
        sb.setValue(val)
        return sb

    def refresh_ports(self):
        self.port_combo.clear()
        self.port_combo.addItem("VIRTUAL", "VIRTUAL")
        for p in list_ports.comports():
            self.port_combo.addItem(f"{p.device}", p.device)

    def _on_pid_update(self):
        motor_id = self.motor_selector.currentData()
        self.pid_config_sent.emit(
            motor_id, self.kp_sb.value(), self.ki_sb.value(), self.kff_sb.value()
        )

    def _emit_time_config(self):
        self.time_config_changed.emit(
            self.sample_period_edit.value(), self.sample_count_edit.value()
        )

    def _on_connect_toggled(self, checked):
        if checked:
            self.connect_btn.setText("Disconnect")
            self.pause_btn.setEnabled(True)
            self.connection_requested.emit(
                self.port_combo.currentText(), int(self.baud_combo.currentText())
            )
        else:
            self.connect_btn.setText("Connect")
            self.pause_btn.setChecked(False)
            self.pause_btn.setEnabled(False)
            self.connection_requested.emit("STOP", 0)

    def _on_pause_toggled(self, checked):
        self.pause_btn.setText("Resume" if checked else "Pause")
        self.pause_btn.setStyleSheet(
            "background-color: #F57F17; color: black; font-weight: bold;" if checked else ""
        )
        self.pause_requested.emit(checked)

    def _on_stream_changed(self, idx):
        sid = self.payload_combo.itemData(idx)
        if not sid:
            return
        cfg = self.stream_loader.get_stream(sid)
        self._rebuild_signal_list(cfg)
        self.stream_changed.emit(cfg["signals"])

    def _rebuild_signal_list(self, cfg):
        while self.signals_layout.count() > 1:
            item = self.signals_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.y_controls = []
        self._accordion_groups = []
        groups = {}
        for gid, gdata in sorted(cfg["groups"].items(), key=lambda x: x[1]["order"]):
            grp = CollapsibleGroup(gdata["label"])
            grp.expanded.connect(self._on_group_expanded)
            self.signals_layout.insertWidget(self.signals_layout.count() - 1, grp)
            groups[gid] = grp
            self._accordion_groups.append(grp)
        for sid, sdata in cfg["signals"].items():
            w = YAxisControlWidget(sdata["label"], sdata["color"])
            w.signal_id = sid
            w.min_edit.setValue(sdata["y_range"]["min"])
            w.max_edit.setValue(sdata["y_range"]["max"])
            w.enable_checkbox.toggled.connect(
                lambda c, s=sid: self.signal_visibility_changed.emit(s, c)
            )
            target = groups.get(sdata["group"])
            if target:
                target.content_layout.addWidget(w)
            self.y_controls.append(w)
        if self._accordion_groups:
            self._accordion_groups[0].toggle.setChecked(True)

    def _on_group_expanded(self, sender):
        for g in self._accordion_groups:
            if g is not sender:
                g.toggle.setChecked(False)

    def _apply_scales(self):
        for w in self.y_controls:
            self.scale_changed.emit(w.signal_id, w.min_edit.value(), w.max_edit.value())


class PlotArea(QtWidgets.QWidget):
    cursor_moved = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.mode = PlotMode.LIVE
        self.signal_views = {}
        self.last_packet = None
        self.analysis_packet = None
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

        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self.vLine.setAcceptHoverEvents(False)
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.anchorLine = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("y", style=QtCore.Qt.PenStyle.DashLine, width=2)
        )
        self.anchorLine.setAcceptHoverEvents(False)
        self.anchorLine.setVisible(False)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)

        self.label = pg.TextItem(anchor=(0, 0))
        self.label.setAcceptHoverEvents(False)
        self.plot.addItem(self.label, ignoreBounds=True)

        self.plot.scene().sigMouseMoved.connect(self.mouse_moved_handler)
        self.plot.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        self.plot.sigRangeChanged.connect(self.update_hud_position)

    @QtCore.pyqtSlot(dict)
    def on_data_ready(self, packet: dict):
        self.last_packet = packet
        if self.mode == PlotMode.ANALYSIS:
            return
        time = packet["time"]
        signals = packet["signals"]
        if len(time) == 0:
            return
        for sig_id, y in signals.items():
            if sig_id in self.signal_views:
                self.signal_views[sig_id]["curve"].setData(time, y)
        self.plot.setXRange(time[0], time[-1], padding=0)
        self.update_hud_position()

    @QtCore.pyqtSlot(bool)
    def set_paused(self, paused: bool):
        if paused:
            self.mode = PlotMode.ANALYSIS
            import copy

            self.analysis_packet = copy.deepcopy(self.last_packet)
        else:
            self.mode = PlotMode.LIVE
            self.analysis_packet = None
            self.anchor_time = None
            self.anchorLine.setVisible(False)
            self.label.setHtml("")

    def mouse_moved_handler(self, pos):
        vb = self.plot.vb
        if vb.sceneBoundingRect().contains(pos):
            mousePoint = vb.mapSceneToView(pos)
            self._process_mouse_movement(mousePoint.x())

    def _process_mouse_movement(self, x_raw):
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return
        x_clamped = float(np.clip(x_raw, ds["time"][0], ds["time"][-1]))
        self.vLine.setPos(x_clamped)
        self.update_tooltip(x_clamped)

    def on_mouse_clicked(self, evt):
        if self.mode != PlotMode.ANALYSIS:
            return
        if evt.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        vb = self.plot.vb
        if vb.sceneBoundingRect().contains(evt.scenePos()):
            mousePoint = vb.mapSceneToView(evt.scenePos())
            self.anchor_time = mousePoint.x()
            self.anchorLine.setPos(self.anchor_time)
            self.anchorLine.setVisible(True)
            self._capture_anchor_values(self.anchor_time)
            self._process_mouse_movement(self.anchor_time)

    def _capture_anchor_values(self, t_anchor):
        if not self.analysis_packet:
            return
        t_arr = self.analysis_packet["time"]
        raw = self.analysis_packet["raw"]
        self.anchor_values = {s: np.interp(t_anchor, t_arr, v) for s, v in raw.items()}

    def update_hud_position(self):
        vb = self.plot.vb
        xr, yr = vb.viewRange()
        self.label.setPos(xr[0] + 0.01 * (xr[1] - xr[0]), yr[1] - 0.02 * (yr[1] - yr[0]))

    def update_tooltip(self, x_pos):
        ds = self.analysis_packet if self.mode == PlotMode.ANALYSIS else self.last_packet
        if not ds or len(ds["time"]) < 2:
            return

        t_arr = ds["time"]
        raw = ds["raw"]
        cur_t = float(x_pos)
        dt = t_arr[1] - t_arr[0]

        # Wyliczamy indeks (do statusu)
        idx = int(np.clip((cur_t - t_arr[0]) / dt, 0, len(t_arr) - 1))
        self.cursor_moved.emit(f"Cursor: {cur_t:.3f} s | Index: {idx}")

        html = f'<div style="background-color: rgba(0, 0, 0, 0.7); padding: 6px; font-family: Consolas, monospace; border: 1px solid #444;">'
        html += f'<b style="color: white; font-size: 12px;">T: {cur_t:.3f} s</b>'
        if self.anchor_time:
            html += f' <span style="color: #FFD700; font-size: 11px;">(Δ {cur_t - self.anchor_time:+.3f} s)</span>'
        html += "<br><hr style='margin: 4px 0;'>"

        for sid, vals in raw.items():
            if sid in self.signal_views and self.signal_views[sid]["curve"].isVisible():
                # DODATKOWE ZABEZPIECZENIE:
                # Jeśli z jakiegoś powodu długości się różnią, bierzemy mniejszą
                data_len = min(len(t_arr), len(vals))
                if data_len < 2:
                    continue

                # Interpolacja na przyciętych danych (snapshotach)
                v = float(np.interp(cur_t, t_arr[:data_len], vals[:data_len]))

                color = self.signal_colors.get(sid, "#FFF")
                row = f'<span style="color: {color};">{sid}: <b>{v:>8.4f}</b>'
                if self.anchor_time and sid in self.anchor_values:
                    row += f' <span style="color: #aaa; font-size: 10px;">(Δ {v - self.anchor_values[sid]:+.4f})</span>'
                html += row + "</span><br>"

        self.label.setHtml(html + "</div>")
        self.update_hud_position()

    @QtCore.pyqtSlot(dict)
    def configure_signals(self, signals_cfg: dict):
        for sid in list(self.signal_views.keys()):
            self.plot.scene().removeItem(self.signal_views[sid]["viewbox"])
        self.plot.clear()
        self.signal_views.clear()
        self.signal_colors.clear()
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.anchorLine, ignoreBounds=True)
        self.plot.addItem(self.label, ignoreBounds=True)
        base_vb = self.plot.getViewBox()
        for sid, sig in signals_cfg.items():
            self.signal_colors[sid] = sig["color"]
            vb = pg.ViewBox()
            vb.setMouseEnabled(x=False, y=False)
            vb.setYRange(0.0, 1.0)
            self.plot.scene().addItem(vb)
            vb.setXLink(base_vb)
            c = pg.PlotDataItem(pen=pg.mkPen(color=sig["color"], width=2), skipFiniteCheck=True)
            vb.addItem(c)
            self.signal_views[sid] = {"viewbox": vb, "curve": c}
        base_vb.sigResized.connect(self._update_views)

    def _update_views(self):
        rect = self.plot.getViewBox().sceneBoundingRect()
        for s in self.signal_views.values():
            s["viewbox"].setGeometry(rect)

    @QtCore.pyqtSlot(str, bool)
    def set_signal_visible(self, sig_id, visible):
        if sig_id in self.signal_views:
            self.signal_views[sig_id]["curve"].setVisible(visible)


# -----------------------------
# Main Window
# -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DiffBot Telemetry Viewer (Pro)")
        self.resize(1280, 800)

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

        self.worker = TelemetryWorker(
            self.panel.sample_period_edit.value(), self.panel.sample_count_edit.value()
        )
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.start()

        self.panel.scale_changed.connect(self.worker.update_scale)
        self.panel.stream_changed.connect(self.worker.configure_signals)
        self.panel.time_config_changed.connect(self.worker.update_time_config)
        self.panel.connection_requested.connect(self._handle_connection)
        self.panel.pause_requested.connect(self._handle_pause)
        self.panel.stream_changed.connect(self.plot.configure_signals)
        self.panel.signal_visibility_changed.connect(self.plot.set_signal_visible)
        self.panel.pid_config_sent.connect(self.worker.send_pid_config)

        self.worker.data_ready.connect(self.plot.on_data_ready)
        self.worker.status_msg.connect(self.lbl_status.setText)
        self.plot.cursor_moved.connect(self.lbl_cursor.setText)
        self.panel._on_stream_changed(self.panel.payload_combo.currentIndex())

    def _handle_connection(self, port, baud):
        if port != "STOP":
            QtCore.QMetaObject.invokeMethod(
                self.worker,
                "start_working",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, port),
                QtCore.Q_ARG(int, baud),
            )
            self.lbl_status.setText(f"Connected to {port}")
            self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")
        else:
            QtCore.QMetaObject.invokeMethod(
                self.worker, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
            )
            self.lbl_status.setText("Disconnected")
            self.lbl_status.setStyleSheet("color: #F44336; font-weight: bold;")

    def _handle_pause(self, paused):
        self.plot.set_paused(paused)
        self.lbl_status.setText("PAUSED" if paused else "Connected")

    def closeEvent(self, event):
        # --- FIX: Używamy invokeMethod zamiast bezpośredniego wywołania ---
        # To wrzuca polecenie "stop_working" do kolejki zdarzeń Wątku Roboczego.
        # Dzięki temu wątek sam zatrzyma swój timer w swoim kontekście.
        QtCore.QMetaObject.invokeMethod(
            self.worker, "stop_working", QtCore.Qt.ConnectionType.QueuedConnection
        )

        # Mówimy wątkowi: "Jak skończysz przetwarzać obecne zdarzenia (w tym stop_working), to wyjdź"
        self.thread.quit()

        # Czekamy na bezpieczne zakończenie (z timeoutem 2s, żeby nie zawiesić okna na zawsze)
        if not self.thread.wait(2000):
            print("Wątek nie odpowiedział, wymuszam zamknięcie...")
            self.thread.terminate()
            self.thread.wait()

        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)

    # App theme
    apply_dark_theme(app)

    win = MainWindow()
    win.show()

    signal.signal(signal.SIGINT, lambda *args: app.quit())

    # Every 500ms QTimer allows python interpreter to check systen signals
    # It is to allow Ctrl+C to work in terminal
    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
