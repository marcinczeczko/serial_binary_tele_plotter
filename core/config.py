"""
Module responsible for loading and managing telemetry stream configurations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.types import StreamConfig

class StreamConfigLoader:
    """
    Manages the configuration for data streams.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._streams: dict[str, StreamConfig] = {}
        self.data: dict[str, Any] = {}

        if not self.path.exists():
            raise FileNotFoundError(f"Required configuration file not found: {self.path.resolve()}")

        self.load()

    def load(self) -> None:
        """Loads and parses the JSON configuration file."""
        try:
            with self.path.open("r", encoding="utf-8") as f:
                self.data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {self.path}: {e}") from e
        self._validate_loaded_data()
        self._streams = dict(self.data["streams"])

    def _validate_loaded_data(self) -> None:
        """Validates the loaded configuration and applies defaults."""
        if "streams" not in self.data or not isinstance(self.data["streams"], dict):
            raise ValueError("Invalid streams.json: missing or invalid 'streams' section")

        for val in self.data["streams"].values():
            if not isinstance(val, dict):
                raise ValueError("Invalid streams.json: each stream entry must be an object")
            if "panel_type" not in val:
                val["panel_type"] = "none"

    def list_streams(self) -> dict[str, StreamConfig]:
        """Returns all available stream definitions."""
        return self._streams

    def get_stream(self, stream_id: str) -> StreamConfig:
        """Returns configuration for a specific stream."""
        try:
            return self._streams[stream_id]
        except KeyError as e:
            raise KeyError(f"Stream '{stream_id}' not found in streams.json") from e
