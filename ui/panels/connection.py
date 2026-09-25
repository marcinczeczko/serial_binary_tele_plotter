"""
Connection Panel Module.

This module provides the `ConnectionPanel` widget, one row in the main toolbar (R6.1),
responsible for:
1. Enumerating available Serial Ports (COM).
2. Selecting communication speed (Baudrate).
3. Managing the connection state (Connect/Disconnect).
4. Controlling the data stream flow (Pause/Resume).
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets
from serial.tools import list_ports


class ConnectionPanel(QtWidgets.QWidget):
    """
    A specific control panel for managing Serial Port connections.

    It emits signals to the main container when connection attempts are made
    or when the user wants to pause the visualization.

    Attributes:
        connection_requested (pyqtSignal): Emitted when Connect/Disconnect is clicked.
            Payload: (port_name: str, baudrate: int).
            If disconnecting, payload is ("STOP", 0).
        pause_requested (pyqtSignal): Emitted when Pause/Resume is clicked.
            Payload: (is_paused: bool).
    """

    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)

    def __init__(self) -> None:
        """Initializes the connection controls and styling."""
        super().__init__()

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # --- Port Selection Controls ---
        self.port_combo = QtWidgets.QComboBox()

        self.refresh_btn = QtWidgets.QPushButton("⟳")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.setToolTip("Refresh Port List")
        self.refresh_btn.clicked.connect(self.refresh_ports)

        # --- Baud Rate Selection ---
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])

        # Grid Placement
        self.port_combo.setToolTip("Serial port (VIRTUAL: the built-in simulator)")
        self.port_combo.setMinimumWidth(130)
        self.baud_combo.setToolTip("Baud rate")
        layout.addWidget(self.port_combo)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.baud_combo)

        # --- Action Buttons Layout ---
        btn_layout = QtWidgets.QHBoxLayout()

        # 1. Connect Button
        # We need extensive styling here to handle the visual state when the button
        # is both Checked (Connected) AND Hovered.
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setStyleSheet(
            """
            /* --- DEFAULT STATE (Disconnected) - GREEN --- */
            QPushButton { 
                background-color: #2E7D32; 
                font-weight: bold; 
                color: white; 
                border-radius: 3px;
                padding: 5px;
                border: 1px solid #1b5e20;
            }
            
            /* Hover (Disconnected) - Lighter Green */
            QPushButton:hover { 
                background-color: #388E3C; 
            }

            /* --- CHECKED STATE (Connected) - RED --- */
            QPushButton:checked { 
                background-color: #C62828; 
                border: 1px solid #b71c1c;
            }
            
            /* Hover (Connected) - Lighter Red */
            /* KEY FIX: Specific rule for checked+hover to prevent reverting to green */
            QPushButton:checked:hover { 
                background-color: #E53935; 
            }
            """
        )
        self.connect_btn.toggled.connect(self._on_connect_toggled)

        # 2. Pause Button. Always available: pausing freezes whatever is in the buffers, so
        # a finished replay or a stopped session can still be analysed.
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause_toggled)

        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.pause_btn)

        btn_layout.setSpacing(6)
        self.pause_btn.setToolTip("Freeze the view for analysis; acquisition continues (Space)")
        layout.addLayout(btn_layout)

        # Populate ports immediately on startup
        self.refresh_ports()

    def _set_connected_ui(self, checked: bool) -> None:
        """Updates UI state without emitting connection signals."""
        self.connect_btn.setText("Disconnect" if checked else "Connect")

    def set_connected(self, connected: bool) -> None:
        """Programmatically updates connection UI without emitting signals."""
        self.connect_btn.blockSignals(True)
        self.connect_btn.setChecked(connected)
        self._set_connected_ui(connected)
        self.connect_btn.blockSignals(False)

    def set_paused(self, paused: bool) -> None:
        """Shows the paused state (e.g. after a trigger capture) without emitting."""
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(paused)
        self.pause_btn.blockSignals(False)
        self._style_pause(paused)

    def select(self, port: str, baud: int) -> None:
        """
        Pre-selects a remembered port and baud rate (R5.3). A port that isn't present now
        (a USB adapter unplugged) is left unselected rather than invented.
        """
        idx = self.port_combo.findText(port)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)
        if baud > 0:
            if self.baud_combo.findText(str(baud)) < 0:
                self.baud_combo.addItem(str(baud))
            self.baud_combo.setCurrentText(str(baud))

    def refresh_ports(self) -> None:
        """
        Refreshes the list of available COM ports via PySerial.

        Preserves the currently selected item if it still exists after refresh.
        Always adds 'VIRTUAL' as a testing option.
        """
        current_selection = self.port_combo.currentText()
        self.port_combo.clear()

        # Add Simulation option
        self.port_combo.addItem("VIRTUAL", "VIRTUAL")

        # Add Physical Ports
        # list_ports.comports() returns ListPortInfo objects
        for p in list_ports.comports():
            # Use device name (e.g., COM3 or /dev/ttyUSB0) as both text and data
            self.port_combo.addItem(f"{p.device}", p.device)

        # Try to restore previous selection to improve UX
        idx = self.port_combo.findText(current_selection)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)

    def _on_connect_toggled(self, checked: bool) -> None:
        """
        Slot handling the Connect/Disconnect toggle.

        Args:
            checked (bool): True if button is pressed (Connecting), False otherwise.
        """
        self._set_connected_ui(checked)

        if checked:
            port = self.port_combo.currentText()
            baud_text = self.baud_combo.currentText()

            # Safe conversion (fallback to 115200)
            baud = int(baud_text) if baud_text.isdigit() else 115200

            self.connection_requested.emit(port, baud)
        else:
            # Emit STOP signal (0 baud indicates disconnect)
            self.connection_requested.emit("STOP", 0)

    def _on_pause_toggled(self, checked: bool) -> None:
        """
        Slot handling the Pause/Resume toggle.

        Args:
            checked (bool): True if paused (Analysis Mode), False if Live.
        """
        self._style_pause(checked)
        self.pause_requested.emit(checked)

    def _style_pause(self, paused: bool) -> None:
        self.pause_btn.setText("Resume" if paused else "Pause")
        # Highlight the button while paused, to show the view is not live.
        self.pause_btn.setStyleSheet(
            "background-color: #F57F17; color: black; font-weight: bold;" if paused else ""
        )
