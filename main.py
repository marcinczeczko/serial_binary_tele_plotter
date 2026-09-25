"""
Application Entry Point.

This script serves as the bootstrap for the Serial Binary Plotter.
It initializes the Qt Application context, applies the global visual theme,
sets up signal handling for graceful termination (e.g., via Ctrl+C), and
launches the main window.
"""

from __future__ import annotations

import argparse
import signal
import sys

from PyQt6 import QtCore, QtWidgets

from core.config import resolve_config_path
from styles import apply_dark_theme
from ui.app_settings import KEY_CONFIG_PATH, app_settings
from ui.main_window import MainWindow


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parses our options and leaves unknown ones (e.g. Qt's -platform) for QApplication."""
    parser = argparse.ArgumentParser(description="Real-time plotter for binary serial telemetry.")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="profile file to use (default: the last used one, else the bundled streams.json)",
    )
    return parser.parse_known_args(argv)


def main() -> int:
    """
    Main execution function.

    Steps:
    1. Initializes the QApplication.
    2. Applies the custom dark theme defined in styles.py.
    3. Instantiates and shows the MainWindow.
    4. Configures system signal handling (SIGINT) to allow terminal termination.
    5. Starts the Qt Event Loop.
    """
    args, qt_args = parse_args(sys.argv[1:])
    app = QtWidgets.QApplication(sys.argv[:1] + qt_args)

    # Numbers read and write with a dot whatever the system locale (ADR-0012), then the scope
    # look: square flat styling and the bundled B612 fonts.
    QtCore.QLocale.setDefault(QtCore.QLocale.c())
    apply_dark_theme(app)

    # Pick the config file: --config, then the last used one, then the bundled default (C10).
    settings = app_settings()
    remembered = settings.value(KEY_CONFIG_PATH, None, type=str)
    config_path = resolve_config_path(args.config, remembered)
    try:
        win = MainWindow(config_path, settings)
    except (OSError, ValueError) as e:
        QtWidgets.QMessageBox.critical(None, "Cannot load configuration", str(e))
        return 2
    settings.setValue(KEY_CONFIG_PATH, str(config_path))
    win.show()

    # Handle Ctrl+C (SIGINT) to gracefully quit the application from the terminal
    signal.signal(signal.SIGINT, lambda *args: app.quit())

    # Create a dummy timer that fires every 500ms.
    # This wakes up the Python interpreter periodically, allowing it to process
    # system signals (like Ctrl+C) which are otherwise blocked by the C++ Qt event loop.
    timer = QtCore.QTimer(app)
    timer.start(500)
    timer.timeout.connect(lambda: None)

    # Enter the main event loop
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
