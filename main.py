"""
Application Entry Point.

This script serves as the bootstrap for the DiffBot Telemetry Viewer.
It initializes the Qt Application context, applies the global visual theme,
sets up signal handling for graceful termination (e.g., via Ctrl+C), and
launches the main window.
"""

import signal
import sys

from PyQt6 import QtCore, QtWidgets

from styles import apply_dark_theme
from ui.main_window import MainWindow


def main():
    """
    Main execution function.

    Steps:
    1. Initializes the QApplication.
    2. Applies the custom dark theme defined in styles.py.
    3. Instantiates and shows the MainWindow.
    4. Configures system signal handling (SIGINT) to allow terminal termination.
    5. Starts the Qt Event Loop.
    """
    app = QtWidgets.QApplication(sys.argv)

    # Apply the global dark theme
    apply_dark_theme(app)

    # Initialize and display the main UI
    win = MainWindow()
    win.show()

    # Handle Ctrl+C (SIGINT) to gracefully quit the application from the terminal
    signal.signal(signal.SIGINT, lambda *args: app.quit())

    # Create a dummy timer that fires every 500ms.
    # This wakes up the Python interpreter periodically, allowing it to process
    # system signals (like Ctrl+C) which are otherwise blocked by the C++ Qt event loop.
    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    # Enter the main event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
