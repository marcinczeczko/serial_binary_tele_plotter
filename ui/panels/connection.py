"""
Connection Panel Module.

Handles Serial Port selection and connection state management.
Restored styling for Connect/Disconnect and Pause/Resume buttons.
"""

from PyQt6 import QtCore, QtWidgets
from serial.tools import list_ports


class ConnectionPanel(QtWidgets.QGroupBox):
    """
    Handles Serial Port selection and connection state.
    """

    connection_requested = QtCore.pyqtSignal(str, int)
    pause_requested = QtCore.pyqtSignal(bool)

    def __init__(self):
        super().__init__("Serial Connection")
        layout = QtWidgets.QGridLayout(self)
        layout.setSpacing(8)

        # Port Selection
        self.port_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("⟳")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.clicked.connect(self.refresh_ports)

        # Baud Rate
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])

        layout.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1)
        layout.addWidget(self.refresh_btn, 0, 2)
        layout.addWidget(QtWidgets.QLabel("Baud:"), 1, 0)
        layout.addWidget(self.baud_combo, 1, 1, 1, 2)

        # Buttons Layout
        btn_layout = QtWidgets.QHBoxLayout()

        # Connect Button with default style
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setStyleSheet(
            """
            /* --- STAN DOMYŚLNY (Rozłączony) - ZIELONY --- */
            QPushButton { 
                background-color: #2E7D32; 
                font-weight: bold; 
                color: white; 
                border-radius: 3px;
                padding: 5px;
                border: 1px solid #1b5e20;
            }
            
            /* Najazd myszką (Rozłączony) - Jaśniejszy zielony */
            QPushButton:hover { 
                background-color: #388E3C; 
            }

            /* --- STAN WCIŚNIĘTY (Połączony) - CZERWONY --- */
            QPushButton:checked { 
                background-color: #C62828; 
                border: 1px solid #b71c1c;
            }
            
            /* Najazd myszką (Połączony) - Jaśniejszy czerwony */
            /* TO JEST KLUCZ DO ROZWIĄZANIA PROBLEMU */
            QPushButton:checked:hover { 
                background-color: #E53935; 
            }
        """
        )
        self.connect_btn.toggled.connect(self._on_connect_toggled)

        # Pause Button
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.toggled.connect(self._on_pause_toggled)

        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.pause_btn)

        # Add buttons to the main grid (row 2, spanning 3 columns)
        layout.addLayout(btn_layout, 2, 0, 1, 3)

        self.refresh_ports()

    def refresh_ports(self):
        """Refreshes the list of available COM ports."""
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItem("VIRTUAL", "VIRTUAL")
        for p in list_ports.comports():
            self.port_combo.addItem(f"{p.device}", p.device)

        # Try to restore previous selection
        idx = self.port_combo.findText(current)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)

    def _on_connect_toggled(self, checked: bool):
        """Updates UI state and styling based on connection status."""
        if checked:
            self.connect_btn.setText("Disconnect")
            # Styl jest ustawiony w CSS (QPushButton:checked), ale dla pewności przycisku Pause:
            self.pause_btn.setEnabled(True)

            port = self.port_combo.currentText()
            baud_text = self.baud_combo.currentText()
            # Zabezpieczenie przed pustym baudrate (rzadki przypadek)
            baud = int(baud_text) if baud_text else 115200

            self.connection_requested.emit(port, baud)
        else:
            self.connect_btn.setText("Connect")
            self.pause_btn.setChecked(False)
            self.pause_btn.setEnabled(False)
            self.connection_requested.emit("STOP", 0)

    def _on_pause_toggled(self, checked: bool):
        """Updates Pause button styling."""
        self.pause_btn.setText("Resume" if checked else "Pause")

        if checked:
            self.pause_btn.setStyleSheet(
                "background-color: #F57F17; color: black; font-weight: bold;"
            )
        else:
            # Reset to default stylesheet defined in styles.py or empty
            self.pause_btn.setStyleSheet("")

        self.pause_requested.emit(checked)
