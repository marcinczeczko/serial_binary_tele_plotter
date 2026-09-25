"""
What the dashboard remembers between runs (R5.3), in `QSettings`.

- The port and baud rate, per config file (a device profile, R8.2), falling back to the
  last ones used with any file.
- Per config file (stream and panel keys only mean something within one file): the shown
  stream, each stream's signal visibility and lane moves, and each panel's parameter
  values, presets and Live mode (R6.4).
- The window's layout: dock positions and sizes (R6.1), whatever the config file.

Visibility and lane moves are kept as overrides of streams.json and applied on top of it
(`apply_view_overrides`), so the file itself is only changed from the Configuration tab.
View → "Reset view to the profile" forgets a stream's overrides.

Values are stored as JSON strings: QSettings' own typing differs between backends (INI
files return strings, the macOS plist returns numbers).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from PyQt6 import QtCore

from core.types import StreamConfig

KEY_PORT = "connection/port"
KEY_BAUD = "connection/baud"
KEY_WINDOW_STATE = "window/state"
KEY_WINDOW_GEOMETRY = "window/geometry"


def _key(part: str) -> str:
    """A settings key segment: '/' and '\\' would otherwise start groups."""
    return part.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C")


class UiState:
    def __init__(self, settings: QtCore.QSettings, config_path: str | Path) -> None:
        self.settings = settings
        self._scope = ""
        self.set_config(config_path)

    def set_config(self, config_path: str | Path) -> None:
        """Remembers things for another config file (a profile switch) from now on."""
        resolved = str(Path(config_path).expanduser().resolve())
        self._scope = "ui/" + hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:12]
        # Which file the scope is for, for someone reading the settings file.
        self.settings.setValue(f"{self._scope}/config_path", resolved)

    # --- plain values ---

    def _get_json(self, key: str, default: Any) -> Any:
        raw = self.settings.value(key, "", type=str)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    def _set_json(self, key: str, value: Any) -> None:
        self.settings.setValue(key, json.dumps(value))

    # --- connection ---

    def connection(self, fallback: bool = True) -> tuple[str, int] | None:
        """This file's port and baud; else (with `fallback`) the last ones used anywhere."""
        port = self.settings.value(f"{self._scope}/{KEY_PORT}", "", type=str)
        baud = self._get_json(f"{self._scope}/{KEY_BAUD}", 0)
        if port and isinstance(baud, int):
            return port, int(baud)
        if not fallback:
            return None
        port = self.settings.value(KEY_PORT, "", type=str)
        baud = self._get_json(KEY_BAUD, 0)
        return (port, int(baud)) if port and isinstance(baud, int) else None

    def set_connection(self, port: str, baud: int) -> None:
        for prefix in (f"{self._scope}/", ""):
            self.settings.setValue(prefix + KEY_PORT, port)
            self._set_json(prefix + KEY_BAUD, int(baud))

    # --- stream shown ---

    def stream(self) -> str | None:
        key = self.settings.value(f"{self._scope}/stream", "", type=str)
        return key or None

    def set_stream(self, key: str) -> None:
        self.settings.setValue(f"{self._scope}/stream", key)

    # --- per-stream view overrides ---

    def _stream_key(self, stream: str, what: str) -> str:
        return f"{self._scope}/streams/{_key(stream)}/{what}"

    def visibility(self, stream: str) -> dict[str, bool]:
        raw = self._get_json(self._stream_key(stream, "visible"), {})
        return {str(k): bool(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def set_visible(self, stream: str, signal: str, visible: bool) -> None:
        current = self.visibility(stream)
        current[signal] = visible
        self._set_json(self._stream_key(stream, "visible"), current)

    def lanes(self, stream: str) -> dict[str, tuple[str, str]]:
        """{signal: (lane key, lane label)}."""
        raw = self._get_json(self._stream_key(stream, "lanes"), {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(sid): (str(v[0]), str(v[1]))
            for sid, v in raw.items()
            if isinstance(v, list) and len(v) == 2
        }

    def set_lane(self, stream: str, signal: str, lane: str, label: str) -> None:
        current = {sid: list(v) for sid, v in self.lanes(stream).items()}
        current[signal] = [lane, label]
        self._set_json(self._stream_key(stream, "lanes"), current)

    def reset_view(self, stream: str) -> None:
        for what in ("visible", "lanes"):
            self.settings.remove(self._stream_key(stream, what))

    def has_view_overrides(self, stream: str) -> bool:
        return bool(self.visibility(stream) or self.lanes(stream))

    # --- panel values ---

    def panel_values(self, panel: str) -> dict[str, dict[str, float]]:
        raw = self._get_json(f"{self._scope}/panels/{_key(panel)}", {})
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, float]] = {}
        for col, params in raw.items():
            if isinstance(params, dict):
                out[str(col)] = {
                    str(k): float(v)
                    for k, v in params.items()
                    if isinstance(v, int | float) and not isinstance(v, bool)
                }
        return out

    def set_panel_values(self, panel: str, values: dict[str, dict[str, float]]) -> None:
        self._set_json(f"{self._scope}/panels/{_key(panel)}", values)

    # --- presets and Live mode (R6.4) ---

    def presets(self, panel: str) -> dict[str, dict[str, dict[str, float]]]:
        raw = self._get_json(f"{self._scope}/presets/{_key(panel)}", {})
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, dict[str, float]]] = {}
        for name, columns in raw.items():
            if not isinstance(columns, dict):
                continue
            out[str(name)] = {
                str(col): {
                    str(k): float(v)
                    for k, v in params.items()
                    if isinstance(v, int | float) and not isinstance(v, bool)
                }
                for col, params in columns.items()
                if isinstance(params, dict)
            }
        return out

    def save_preset(self, panel: str, name: str, values: dict[str, dict[str, float]]) -> None:
        current = self.presets(panel)
        current[name] = values
        self._set_json(f"{self._scope}/presets/{_key(panel)}", current)

    def delete_preset(self, panel: str, name: str) -> None:
        current = self.presets(panel)
        if current.pop(name, None) is not None:
            self._set_json(f"{self._scope}/presets/{_key(panel)}", current)

    def panel_live(self, panel: str) -> bool:
        return self._get_json(f"{self._scope}/live/{_key(panel)}", False) is True

    def set_panel_live(self, panel: str, live: bool) -> None:
        self._set_json(f"{self._scope}/live/{_key(panel)}", bool(live))

    # --- window layout (R6.1) ---

    def window_state(self) -> tuple[QtCore.QByteArray | None, QtCore.QByteArray | None]:
        """(geometry, dock state) as saved by `set_window_state`."""
        geometry = self.settings.value(KEY_WINDOW_GEOMETRY)
        state = self.settings.value(KEY_WINDOW_STATE)
        return (
            geometry if isinstance(geometry, QtCore.QByteArray) else None,
            state if isinstance(state, QtCore.QByteArray) else None,
        )

    def set_window_state(self, geometry: QtCore.QByteArray, state: QtCore.QByteArray) -> None:
        self.settings.setValue(KEY_WINDOW_GEOMETRY, geometry)
        self.settings.setValue(KEY_WINDOW_STATE, state)


def apply_view_overrides(
    cfg: StreamConfig, visible: dict[str, bool], lanes: dict[str, tuple[str, str]]
) -> StreamConfig:
    """A copy of the stream with remembered visibility and lane moves applied."""
    if not visible and not lanes:
        return cfg
    out = cast(dict[str, Any], copy.deepcopy(cfg))
    signals: dict[str, Any] = out.get("signals") or {}
    groups: dict[str, Any] = out.setdefault("groups", {})
    for sid, shown in visible.items():
        if isinstance(signals.get(sid), dict):
            signals[sid]["visible"] = shown
    for sid, (lane, label) in lanes.items():
        if not isinstance(signals.get(sid), dict):
            continue
        signals[sid]["group"] = lane
        if lane and lane not in groups:
            groups[lane] = {"label": label}
    if not groups:
        del out["groups"]
    return cast(StreamConfig, out)
