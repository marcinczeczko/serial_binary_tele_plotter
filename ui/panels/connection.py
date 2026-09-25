"""
Connection Panel Module.

This module provides the `ConnectionPanel`: the top bar's first three items (R9.2) and
their logic. It owns the widgets; the main window places them in the bar. Responsible for:
0. Picking the device profile (R8.2): what the device sends, and which streams to show.
   Only while disconnected; the main window lists the profiles and does the switch.
1. Enumerating available Serial Ports (COM).
2. Selecting communication speed (Baudrate).
3. Managing the connection state (Connect/Disconnect).
4. Controlling the data stream flow: the RUN/STOP box (today's Pause/Resume).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from core.config import Profile, ProfileEntry
from ui.panels.top_bar import RunBox

VIRTUAL = "VIRTUAL"
BAUD_RATES = ("9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600")


class PortCombo(QtWidgets.QComboBox):
    """
    The port list, re-read from the system each time it opens (no refresh button). As wide
    as the chosen port, not the longest one the system lists; the list is as wide as it needs.
    """

    about_to_open = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentTextChanged.connect(lambda _t: self.updateGeometry())

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        hint = super().sizeHint()
        text = self.fontMetrics().horizontalAdvance(self.currentText())
        return QtCore.QSize(max(text + 34, self.minimumWidth()), hint.height())

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802
        return self.sizeHint()

    def showPopup(self) -> None:  # noqa: N802
        self.about_to_open.emit()
        view = self.view()
        if view is not None:
            view.setMinimumWidth(view.sizeHintForColumn(0) + 24)
        super().showPopup()


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
    profile_chosen = QtCore.pyqtSignal(str)  # a profile file's path
    new_profile_requested = QtCore.pyqtSignal()
    open_profile_requested = QtCore.pyqtSignal()
    edit_profile_requested = QtCore.pyqtSignal()

    def __init__(self) -> None:
        """Initializes the connection controls and styling."""
        super().__init__()

        # --- Device profile (R8.2) ---
        self.profile_btn = QtWidgets.QToolButton(self)
        self.profile_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.profile_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.profile_btn.setMinimumWidth(90)
        self.profile_menu = QtWidgets.QMenu(self.profile_btn)
        self.profile_btn.setMenu(self.profile_menu)
        self.profile_actions: dict[str, QtGui.QAction] = {}  # by resolved path

        # --- Port and baud: the baud only matters for a real serial port ---
        self.port_combo = PortCombo(self)
        self.port_combo.setToolTip("Serial port (VIRTUAL: the built-in simulator)")
        self.port_combo.setMinimumWidth(90)
        self.port_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.port_combo.about_to_open.connect(self.refresh_ports)
        self.port_combo.currentTextChanged.connect(lambda _t: self._show_baud())
        self.baud_combo = QtWidgets.QComboBox(self)
        self.baud_combo.addItems(BAUD_RATES)
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setToolTip("Baud rate")

        # Scope style (ADR-0012): `Connect` is lit (it's the action you want), `Disconnect` grey.
        self.connect_btn = QtWidgets.QPushButton("Connect", self)
        self.connect_btn.setCheckable(True)
        self.connect_btn.setFixedHeight(24)
        self.connect_btn.setStyleSheet(
            "QPushButton { background: #d6d6d6; color: #000; border: 1px solid #d6d6d6;"
            " font-weight: bold; padding: 0 10px; }"
            " QPushButton:hover { background: #ffffff; border-color: #ffffff; }"
            " QPushButton:checked { background: #222; color: #bdbdbd; border-color: #484848;"
            " font-weight: normal; }"
            " QPushButton:checked:hover { background: #2c2c2c; border-color: #6a6a6a; }"
        )
        self.connect_btn.toggled.connect(self._on_connect_toggled)

        # RUN/STOP. Always available: stopping freezes whatever is in the buffers, so a
        # finished replay or a stopped session can still be analysed.
        self.pause_btn = RunBox(self)
        self.pause_btn.toggled.connect(self._on_pause_toggled)

        # Populate ports immediately on startup
        self.refresh_ports()
        self._show_baud()

    def _set_connected_ui(self, checked: bool) -> None:
        """Updates UI state without emitting connection signals."""
        self.connect_btn.setText("Disconnect" if checked else "Connect")

    def set_connected(self, connected: bool) -> None:
        """Programmatically updates connection UI without emitting signals."""
        self.connect_btn.blockSignals(True)
        self.connect_btn.setChecked(connected)
        self._set_connected_ui(connected)
        self.connect_btn.blockSignals(False)
        self.pause_btn.set_connected(connected)
        # A profile says what the device sends: it can't change under a running session.
        self.profile_btn.setEnabled(not connected)
        self._profile_tooltip()

    def _profile_tooltip(self) -> None:
        profile = getattr(self, "_profile", None)
        what = f"{profile.name}: a {profile.format} profile" if profile is not None else ""
        hint = "Disconnect to switch profiles" if self.connect_btn.isChecked() else "Profiles"
        self.profile_btn.setToolTip(f"{what}\n{hint}".strip())

    def _show_baud(self) -> None:
        """The baud combo shows only for a real serial port (the simulator has no baud)."""
        self.baud_combo.setVisible(self.port_combo.currentText() != VIRTUAL)

    def set_profiles(self, entries: list[ProfileEntry], current: Path, profile: Profile) -> None:
        """Lists the profiles to switch to; `current` is the file in use."""
        self.profile_btn.setText(f"{profile.name} ▾")
        self._profile = profile
        menu = self.profile_menu
        menu.clear()
        self.profile_actions.clear()
        current_resolved = current.expanduser().resolve()
        for entry in entries:
            action = menu.addAction(f"{entry.name}   ·  {entry.format}")
            assert action is not None
            action.setCheckable(True)
            action.setChecked(entry.path == current_resolved)
            action.setToolTip(str(entry.path))
            action.triggered.connect(lambda _=False, p=str(entry.path): self.profile_chosen.emit(p))
            self.profile_actions[str(entry.path)] = action
        menu.addSeparator()
        for text, signal in (
            ("New profile…", self.new_profile_requested),
            ("Open profile file…", self.open_profile_requested),
            ("Edit profile…", self.edit_profile_requested),
        ):
            action = menu.addAction(text)
            assert action is not None
            action.triggered.connect(lambda _=False, s=signal: s.emit())
        self._profile_tooltip()

    def set_paused(self, paused: bool) -> None:
        """Shows the paused state (e.g. after a trigger capture) without emitting."""
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(paused)
        self.pause_btn.blockSignals(False)
        self.pause_btn.refresh()

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
        self.port_combo.addItem(VIRTUAL, VIRTUAL)

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
        """RUN/STOP: True freezes the view (analysis), False follows the data again."""
        self.pause_requested.emit(checked)
