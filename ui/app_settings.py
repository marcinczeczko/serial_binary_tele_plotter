"""Where the app remembers things between runs (QSettings names and keys)."""

from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore

SETTINGS_ORG = "serial-bin-plotter"
SETTINGS_APP = "Serial Binary Plotter"

KEY_CONFIG_PATH = "config_path"
KEY_RECORD_ON_CONNECT = "recording/on_connect"
KEY_RECORDINGS_DIR = "recording/folder"

DEFAULT_RECORDINGS_DIR = Path.home() / "telemetry-recordings"


def app_settings() -> QtCore.QSettings:
    return QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)
