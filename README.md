# DiffBot Telemetry Viewer

A PyQt6 desktop application for real-time telemetry visualization and tuning of a DiffDrive robot. It connects to a microcontroller over a serial port, parses a simple binary protocol, and plots multiple signals live. A built‑in virtual device lets you test the UI without hardware.

## Features

- Live multi-signal plotting with pyqtgraph, auto-ranging, cursor readout, and analysis pause mode.
- Serial connection management with port scanning and baud selection.
- Virtual device mode for simulation (`VIRTUAL` port).
- Stream selection driven by `streams.json` (frame layout + signal definitions).
- Built-in configuration editor tab for editing streams and signals.
- PID tuning panel for left/right/both motors.
- IMU calibration command panel.
- Adjustable sample period and buffer size.

## Requirements

- Python 3.9+ (tested with PyQt6)
- Packages: `PyQt6`, `pyqtgraph`, `numpy`, `pyserial`

## Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install PyQt6 pyqtgraph numpy pyserial
```

## Run

From the project root:

```bash
python main.py
```

## Usage

- Select a stream in the sidebar (driven by `streams.json`).
- Choose a serial port and baud rate, then click `Connect`.
- Use `Pause` to switch to analysis mode. The cursor shows interpolated values; click to set an anchor for delta readouts.
- Toggle signal visibility in the `Signals Visibility` list.
- Adjust `Period` and `Samples` to control sampling rate and history window.
- Use the `Configuration` tab to edit streams, signals, and frame layouts, then save to update `streams.json`.

### Virtual Device

Select the `VIRTUAL` port to run against the simulator in `core/acquisition/virtual.py`. The simulator switches its data pattern based on the stream name (PID, IMU, or control).

## Configuration: `streams.json`

`streams.json` defines all available telemetry streams. Each stream includes:

- `name`: Display name in the UI.
- `panel_type`: Which control panel to show (`none`, `pid`, `imu`).
- `frame`: Binary layout with `stream_id` and `fields`.
- `signals`: A flat map of plotted signals and their styles.

The time axis uses the `loop_cntr` field, multiplied by the configured sample period.

Example structure:

```json
{
  "streams": {
    "pid": {
      "name": "PID Telemetry",
      "panel_type": "pid",
      "frame": {
        "stream_id": 1,
        "endianness": "little",
        "fields": [
          {"name": "loop_cntr", "type": "u32"},
          {"name": "left_setpoint", "type": "f32"}
        ]
      },
      "signals": {
        "left_setpoint": {
          "label": "L: Setpoint",
          "field": "left_setpoint",
          "color": "#4FC3F7",
          "visible": true,
          "line": {"style": "dashed", "width": 2}
        }
      }
    }
  }
}
```

## Protocol Summary

Incoming telemetry frames are parsed by `core/protocol/handler.py` using:

- Magic bytes: `0xAA 0x55`
- Type byte: must match the configured `stream_id`
- CRC8 on header and payload

PID configuration packets are also produced by the same handler and sent over the serial connection.

## Project Structure

- `main.py`: Application entry point.
- `ui/`: Qt widgets (main window, panels, config editor, charts).
- `core/`: Telemetry engine, serial/virtual acquisition, protocol parsing.
- `streams.json`: Stream and signal definitions.
- `styles.py`: Global dark theme.

## Troubleshooting

- If no ports appear, verify your OS permissions for serial devices.
- If the plot is flat, confirm the selected stream matches the firmware `stream_id` and frame layout.
- Use `VIRTUAL` to validate UI and plotting independently of hardware.
