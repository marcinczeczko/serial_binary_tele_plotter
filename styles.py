"""
Application Styling Module.

This module provides a centralized function to apply a consistent dark theme
to the entire PyQt6 application. It leverages the 'Fusion' style engine as a base
and overrides the color palette and stylesheets to create a modern, high-contrast
dark interface suitable for telemetry visualization.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets


def apply_dark_theme(app: QtWidgets.QApplication) -> None:
    """
    Applies a custom dark theme to the given QApplication instance.

    This function performs two main styling operations:
    1. Sets the application style to "Fusion" and configures the QPalette
       to use dark backgrounds (black/gray) and light text (white).
    2. Applies a global QSS (Qt Style Sheet) to fine-tune specific widgets
       like checkboxes, spinboxes, group boxes, and buttons, ensuring a
       consistent look and feel across the application.

    Args:
        app (QtWidgets.QApplication): The main application instance to style.
    """
    # Set the base style engine
    app.setStyle("Fusion")

    # --- Palette Configuration ---
    palette = app.palette()
    palette.setColor(palette.ColorRole.Window, QtCore.Qt.GlobalColor.black)
    palette.setColor(palette.ColorRole.WindowText, QtCore.Qt.GlobalColor.white)
    palette.setColor(palette.ColorRole.Base, QtCore.Qt.GlobalColor.black)
    palette.setColor(palette.ColorRole.AlternateBase, QtCore.Qt.GlobalColor.darkGray)
    palette.setColor(palette.ColorRole.Text, QtCore.Qt.GlobalColor.white)
    palette.setColor(palette.ColorRole.Button, QtCore.Qt.GlobalColor.darkGray)
    palette.setColor(palette.ColorRole.ButtonText, QtCore.Qt.GlobalColor.white)
    palette.setColor(palette.ColorRole.Highlight, QtCore.Qt.GlobalColor.blue)
    app.setPalette(palette)

    # --- Stylesheet Overrides ---
    app.setStyleSheet(
        """  
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border: 1px solid #777;
            border-radius: 3px;
            background: transparent;
        }

        QCheckBox::indicator:checked {
            background-color: gray;
        }
  
        QDoubleSpinBox, QSpinBox, QLineEdit {
            padding: 2px; background-color: #1e1e1e; border: 1px solid #333; color: #fff;
        }

        QGroupBox {
            border: 1px solid #333; margin-top: 6px; padding-top: 10px;
            font-weight: bold; color: #aaa;
        }
        QGroupBox::title {
            subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px;
        }

        QPushButton {
            background-color: #333; border: 1px solid #555;
            border-radius: 3px; padding: 5px; color: white;
        }
        QPushButton:hover { background-color: #444; }
        QPushButton:pressed { background-color: #222; }
        QPushButton:checked { background-color: #555; border: 1px solid #888; }
        
        QComboBox { background-color: #1e1e1e; border: 1px solid #333; color: white; padding: 4px; }
        
        QStatusBar { color: #888; }

        /* --- Tabs Styling (FIX for grey background) --- */
        QTabWidget::pane { 
            border: 1px solid #333; 
            background-color: black; /* Force black background for content area */
        }
        QTabBar::tab {
            background: #222;
            color: #888;
            padding: 8px 20px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            border: 1px solid #333;
            margin-right: 2px;
        }
        QTabBar::tab:selected {
            background: #444;
            color: white;
            border-bottom: 2px solid #4FC3F7; /* Blue highlight for active tab */
        }
        QTabBar::tab:hover {
            background: #333;
        }
        """
    )
