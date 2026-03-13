"""
Connection Panel Module.

This module provides the `ConnectionPanel` widget responsible for:
1. Enumerating available Serial Ports (COM).
2. Selecting communication speed (Baudrate).
3. Managing the connection state (Connect/Disconnect).
4. Controlling the data stream flow (Pause/Resume).
"""

from PyQt6 import QtCore, QtWidgets
from serial.tools import list_ports


class ConnectionPanel(QtWidgets.QGroupBox):
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

    def __init__(self):
        """Initializes the connection controls and styling."""
        super().__init__("Serial Connection")

        layout = QtWidgets.QGridLayout(self)
        layout.setSpacing(8)

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
        layout.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1)
        layout.addWidget(self.refresh_btn, 0, 2)
        layout.addWidget(QtWidgets.QLabel("Baud:"), 1, 0)
        layout.addWidget(self.baud_combo, 1, 1, 1, 2)

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

        # 2. Pause Button
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)  # Disabled until connected
        self.pause_btn.toggled.connect(self._on_pause_toggled)

        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.pause_btn)

        # Add buttons to the main grid (Row 2, spanning 3 columns)
        layout.addLayout(btn_layout, 2, 0, 1, 3)

        # Populate ports immediately on startup
        self.refresh_ports()

    def _set_connected_ui(self, checked: bool) -> None:
        """Updates UI state without emitting connection signals."""
        if checked:
            # Entering Connected State
            self.connect_btn.setText("Disconnect")
            self.pause_btn.setEnabled(True)
        else:
            # Entering Disconnected State
            self.connect_btn.setText("Connect")

            # Reset Pause button state
            self.pause_btn.setChecked(False)
            self.pause_btn.setEnabled(False)

    def set_connected(self, connected: bool) -> None:
        """Programmatically updates connection UI without emitting signals."""
        self.connect_btn.blockSignals(True)
        self.pause_btn.blockSignals(True)
        self.connect_btn.setChecked(connected)
        self._set_connected_ui(connected)
        self.pause_btn.blockSignals(False)
        self.connect_btn.blockSignals(False)

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
        self.pause_btn.setText("Resume" if checked else "Pause")

        if checked:
            # Highlight button when paused to indicate non-live state
            self.pause_btn.setStyleSheet(
                "background-color: #F57F17; color: black; font-weight: bold;"
            )
        else:
            # Reset to default style
            self.pause_btn.setStyleSheet("")

        self.pause_requested.emit(checked)
