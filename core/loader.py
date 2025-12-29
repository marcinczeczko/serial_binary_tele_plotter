"""
Module responsible for loading and managing telemetry stream configurations.

This module handles the initialization of stream definitions, including signal names,
colors, grouping, and value ranges. It supports loading from a JSON file or falling
back to a default configuration if the file is missing.
"""

import json
from pathlib import Path


class StreamConfigLoader:
    """
    Manages the configuration for data streams.

    Attempts to load configuration from a provided JSON file path. If the file
    does not exist, it initializes with a default PID controller schema containing
    standard signals like setpoint, measurement, error, and PID terms.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        if not self.path.exists():
            self.data = {
                "streams": {
                    "pid_control": {
                        "name": "PID Controller",
                        "groups": {
                            "main": {"label": "Main Variables", "order": 1},
                            "terms": {"label": "PID Terms", "order": 2},
                            "out": {"label": "Output", "order": 3},
                        },
                        "signals": {
                            "setpoint": {
                                "label": "Setpoint",
                                "color": "#00FF00",
                                "group": "main",
                                "y_range": {"min": -1.5, "max": 1.5},
                            },
                            "measurement": {
                                "label": "Measurement",
                                "color": "#FF0000",
                                "group": "main",
                                "y_range": {"min": -1.5, "max": 1.5},
                            },
                            "error": {
                                "label": "Error",
                                "color": "#FFFF00",
                                "group": "main",
                                "y_range": {"min": -0.5, "max": 0.5},
                            },
                            "p_term": {
                                "label": "P Term",
                                "color": "#00FFFF",
                                "group": "terms",
                                "y_range": {"min": -50, "max": 50},
                            },
                            "i_term": {
                                "label": "I Term",
                                "color": "#FF00FF",
                                "group": "terms",
                                "y_range": {"min": -10, "max": 10},
                            },
                            "output": {
                                "label": "Motor Output",
                                "color": "#FFFFFF",
                                "group": "out",
                                "y_range": {"min": -100, "max": 100},
                            },
                        },
                    }
                }
            }
        else:
            self._load()

    def _load(self):
        """Internal method to load and parse the JSON configuration file."""
        with self.path.open("r", encoding="utf-8") as f:
            self.data = json.load(f)

    def list_streams(self):
        """
        Retrieves a dictionary of all available streams.

        Returns:
            dict: A dictionary containing stream definitions keyed by stream ID.
        """
        return self.data["streams"]

    def get_stream(self, stream_id: str):
        """
        Retrieves the configuration for a specific stream.

        Args:
            stream_id (str): The unique identifier of the stream to retrieve.

        Returns:
            dict: The configuration dictionary for the requested stream.
        """
        return self.data["streams"][stream_id]
