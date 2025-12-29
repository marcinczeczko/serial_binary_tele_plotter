"""
Module responsible for loading and managing telemetry stream configurations.

This module loads telemetry stream definitions from a mandatory JSON file.
If the configuration file is missing or invalid, the application must fail fast.
"""

import json
from pathlib import Path


class StreamConfigLoader:
    """
    Manages the configuration for data streams.

    The configuration file is REQUIRED.
    Missing or malformed configuration is treated as a fatal application error.
    """

    def __init__(self, path: str):
        self.path = Path(path)

        if not self.path.exists():
            raise FileNotFoundError(f"Required configuration file not found: {self.path.resolve()}")

        self._load()

        # Basic sanity validation
        if "streams" not in self.data or not isinstance(self.data["streams"], dict):
            raise ValueError("Invalid streams.json: missing or invalid 'streams' section")

    def _load(self):
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
