"""Where the app remembers things between runs (QSettings names and keys)."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6 import QtCore

SETTINGS_ORG = "serial-bin-plotter"
SETTINGS_APP = "Serial Binary Plotter"

KEY_CONFIG_PATH = "config_path"
KEY_RECORD_ON_CONNECT = "recording/on_connect"
KEY_RECORDINGS_DIR = "recording/folder"
KEY_PROFILES_DIR = "profiles/folder"
KEY_RECENT_PROFILES = "profiles/recent"

DEFAULT_RECORDINGS_DIR = Path.home() / "telemetry-recordings"
DEFAULT_PROFILES_DIR = Path.home() / "telemetry-profiles"
MAX_RECENT_PROFILES = 8


def app_settings() -> QtCore.QSettings:
    return QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)


def profiles_dir(settings: QtCore.QSettings) -> Path:
    """The folder New profile saves into and the profile menu lists (R8.2)."""
    stored = settings.value(KEY_PROFILES_DIR, "", type=str)
    return Path(stored) if stored else DEFAULT_PROFILES_DIR


def recent_profiles(settings: QtCore.QSettings) -> list[str]:
    """Profile files opened from outside the profiles folder, newest first."""
    raw = settings.value(KEY_RECENT_PROFILES, "", type=str)
    try:
        paths = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []


def add_recent_profile(settings: QtCore.QSettings, path: str | Path) -> None:
    resolved = str(Path(path).expanduser().resolve())
    paths = [resolved] + [p for p in recent_profiles(settings) if p != resolved]
    settings.setValue(KEY_RECENT_PROFILES, json.dumps(paths[:MAX_RECENT_PROFILES]))
