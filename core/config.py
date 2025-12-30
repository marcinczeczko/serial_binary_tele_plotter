"""
Module responsible for loading and managing telemetry stream configurations.
"""

import json
from pathlib import Path


class StreamConfigLoader:
    """
    Manages the configuration for data streams.
    """

    def __init__(self, path: str):
        self.path = Path(path)

        if not self.path.exists():
            raise FileNotFoundError(f"Required configuration file not found: {self.path.resolve()}")

        self.data = {}
        self.load()

        # Basic sanity validation
        if "streams" not in self.data or not isinstance(self.data["streams"], dict):
            raise ValueError("Invalid streams.json: missing or invalid 'streams' section")

        # --- FIX: Ensure panel_type exists ---
        for key, val in self.data["streams"].items():
            if "panel_type" not in val:
                val["panel_type"] = "none"

    def load(self):
        """Loads and parses the JSON configuration file."""
        try:
            with self.path.open("r", encoding="utf-8") as f:
                self.data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {self.path}: {e}") from e

    def list_streams(self) -> dict:
        """Returns all available stream definitions."""
        return self.data["streams"]

    def get_stream(self, stream_id: str) -> dict:
        """Returns configuration for a specific stream."""
        try:
            return self.data["streams"][stream_id]
        except KeyError:
            raise KeyError(f"Stream '{stream_id}' not found in streams.json")
