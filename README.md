# Serial Binary Plotter

A PyQt6 desktop application for real-time visualization and troubleshooting of data streamed
from any microcontroller over serial. Define your binary frame layout in `streams.json`, connect
to your MCU, and plot any signal live. No firmware changes required beyond implementing the
simple binary protocol below.

Primary use cases:
- **Signal visualization** — plot any numeric value your MCU can emit (sensor readings, state
  variables, computed outputs) with configurable colors, line styles, and visibility toggles.
- **PID tuning** — send controller parameters back to the MCU while watching the response live.
- **Hardware-free development** — a built-in virtual device simulates the protocol so you can
  develop and test without a board attached.

## Features

- Live multi-signal plotting via pyqtgraph — auto-ranging, cursor readout, and analysis pause mode.
- Serial connection management with port scanning and baud rate selection.
- Analysis mode: pause the plot, scrub with the cursor, click to set an anchor for delta (Δ)
  readouts across all signals.
- Multiple named streams, each with its own frame layout and signal set, selectable at runtime.
- Built-in configuration editor — edit frame fields and signal definitions in-app, save to
  `streams.json` without restarting.
- Optional PID tuning panel: send controller gains to the MCU over the same serial connection.
- Optional IMU calibration command panel.
- Virtual device simulator (`VIRTUAL` port) for UI development without hardware.
- Adjustable sample period and ring-buffer window size.

## Requirements

- **Python 3.14** (enforced by `pyproject.toml`; `uv` selects this automatically)
- **[uv](https://docs.astral.sh/uv/)** — the only tool you need to install manually

## Setup

```bash
# Install uv (once, system-wide)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and enter the project
git clone <repo-url>
cd serial_bin_plotter

# Create the venv and install all runtime + dev dependencies
uv sync
```

`uv sync` installs everything — runtime deps (`PyQt6`, `numpy`, `pyqtgraph`, `pyserial`) and
dev tools (`pytest`, `ruff`, `mypy`) — into an isolated `.venv` in one step. No manual
`pip install` or `venv` activation needed.

## Running

```bash
uv run python main.py
```

## Development Commands

```bash
# Run all tests
uv run pytest

# Lint (check only)
uv run ruff check .

# Lint + auto-fix
uv run ruff check --fix .

# Format
uv run ruff format .

# Type-check
uv run mypy .
```

> **Why `uv run` and not activating the venv?**
> `uv run <cmd>` always executes inside the project venv regardless of your shell's active
> environment. This avoids the common trap where Anaconda or another system Python intercepts
> bare commands like `pytest` or `python`.

## Usage

1. Launch the app with `uv run python main.py`.
2. Select a **stream** from the sidebar (populated from `streams.json`).
3. Pick a **serial port** and **baud rate**, then click `Connect`. Use `VIRTUAL` for the
   built-in simulator.
4. Click `Pause` to enter analysis mode — the cursor shows interpolated values for all visible
   signals. Click on the plot to set an anchor point for delta (Δ) readouts.
5. Toggle individual signal visibility in the **Signals Visibility** panel.
6. Adjust **Period** (ms) and **Samples** to control the sampling rate and history window length.
7. Use the **Configuration** tab to add/edit streams, frame fields, and signal definitions, then
   save to update `streams.json` on disk.

## Connecting Your MCU

Two things are needed on the firmware side:

1. **Implement the binary protocol** — see the [Binary Protocol](#binary-protocol) section below.
2. **Define your frame layout** in `streams.json` — see [Configuration](#configuration-streamsjson).

No library is required on the MCU. The protocol is a simple packed struct with a header and two
CRC bytes, straightforward to implement in C/C++.

## Binary Protocol

Every frame sent by the MCU follows this structure:

```
┌────────┬────────┬──────┬─────┬────────┬─────────────────┬────────┐
│ 0xAA   │ 0x55   │ TYPE │ LEN │ H_CRC8 │ PAYLOAD (LEN B) │ P_CRC8 │
└────────┴────────┴──────┴─────┴────────┴─────────────────┴────────┘
```

| Field | Size | Description |
|-------|------|-------------|
| Magic | 2 B | `0xAA 0x55` — start-of-frame marker |
| Type | 1 B | Stream identifier; must match `stream_id` in `streams.json` |
| Len | 1 B | Payload length in bytes |
| H_CRC8 | 1 B | CRC-8 (poly `0x07`) computed over the 4-byte header |
| Payload | Len B | Packed struct — fields in the order defined in `streams.json` |
| P_CRC8 | 1 B | CRC-8 (poly `0x07`) computed over the payload |

**CRC-8 algorithm** (poly `0x07`, init `0x00`):
```c
uint8_t crc8(const uint8_t *data, size_t len) {
    uint8_t crc = 0x00;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x80) ? ((crc << 1) ^ 0x07) : (crc << 1);
    }
    return crc;
}
```

The plotter also sends PID configuration packets back to the MCU using the same framing when
the PID tuning panel is in use.

## Configuration: `streams.json`

`streams.json` is the single source of truth for all stream definitions. Edit it directly or
use the in-app **Configuration** tab.

Each stream entry:

| Key | Description |
|-----|-------------|
| `name` | Display name shown in the sidebar |
| `panel_type` | Control panel to show alongside the plot: `none`, `pid`, or `imu` |
| `frame.stream_id` | Packet type byte — must match the `TYPE` field sent by the MCU |
| `frame.endianness` | `"little"` or `"big"` — must match the MCU's byte order |
| `frame.fields` | Ordered list of `{name, type}` matching the C struct field order |
| `signals` | Map of signal IDs to display config (label, color, visibility, line style) |

**`loop_cntr` is mandatory** in every frame — it is the loop counter used as the X-axis
(`loop_cntr × sample_period_s = time in seconds`). It must be a `u32` and must be the first
field.

**Supported field types:** `u8`, `i8`, `u16`, `i16`, `u32`, `i32`, `u64`, `i64`, `f32`, `f64`

**Line styles:** `solid`, `dashed`, `dotted`

Example — a minimal stream definition:

```json
{
  "streams": {
    "my_sensor": {
      "name": "Sensor Data",
      "panel_type": "none",
      "frame": {
        "stream_id": 1,
        "endianness": "little",
        "fields": [
          {"name": "loop_cntr", "type": "u32"},
          {"name": "temperature",  "type": "f32"},
          {"name": "pressure",     "type": "f32"}
        ]
      },
      "signals": {
        "temperature": {
          "label": "Temperature (°C)",
          "field": "temperature",
          "color": "#FF5733",
          "visible": true,
          "line": {"style": "solid", "width": 2}
        },
        "pressure": {
          "label": "Pressure (hPa)",
          "field": "pressure",
          "color": "#4FC3F7",
          "visible": true,
          "line": {"style": "dashed", "width": 1}
        }
      }
    }
  }
}
```

## Project Structure

```
serial_bin_plotter/
├── main.py                    # Entry point, QApplication setup
├── streams.json               # Stream and signal definitions (source of truth)
├── styles.py                  # Global dark theme
├── core/
│   ├── types.py               # Shared TypedDicts and Enums
│   ├── config.py              # streams.json loader
│   ├── protocol/
│   │   ├── handler.py         # Frame sync, CRC validation, encode/decode
│   │   ├── decoder.py         # struct unpacking → Python dict
│   │   ├── crc.py             # CRC-8 (lookup-table implementation)
│   │   └── constants.py       # Magic bytes, type IDs, field names
│   └── acquisition/
│       ├── engine.py          # TelemetryEngine — worker QThread controller
│       ├── storage.py         # SignalDataManager — numpy ring buffers
│       └── virtual.py         # VirtualDevice — hardware-free simulator
└── ui/
    ├── main_window.py         # MainWindow — thread coordinator
    ├── charts/
    │   └── telemetry_plot.py  # Live pyqtgraph plot widget
    ├── panels/                # Connection, PID, IMU, signals, timing panels
    └── config/                # Stream configuration editor tab
```

## Architecture Notes

- **Threading:** `TelemetryEngine` runs in a `QThread`. All cross-thread communication uses Qt
  signals/slots with `QueuedConnection`. The main thread never calls worker methods directly.
- **Data flow:** Serial → `ProtocolHandler` → `SignalDataManager` (numpy ring buffers) →
  `TelemetryPlot` at ~10 FPS via a 100 ms `QTimer`.
- **Performance:** Y-axis bounds are computed on the worker thread and shipped with each data
  packet; the UI thread only does an O(num_signals) visibility filter. The tooltip reuses a
  single `searchsorted` result across all signals per mouse event.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No serial ports appear | OS permission denied | Add user to `dialout` group (Linux) or grant Terminal serial access (macOS) |
| Plot is flat / no data | `stream_id` or frame layout mismatch | Verify `stream_id`, field order, and types match the firmware struct exactly |
| Data looks corrupted | Baud rate mismatch | Ensure firmware and UI baud rates are identical |
| `uv run pytest` picks up wrong Python | Anaconda or system `pytest` in PATH | Always use `uv run pytest`, never bare `pytest` |
