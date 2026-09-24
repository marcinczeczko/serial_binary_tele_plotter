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
- Multiple named streams, each with its own frame layout and signal set. **All streams are
  decoded at the same time**; the selector only chooses which one to show, so switching
  keeps each stream's history and never interrupts acquisition. Streams may share a
  `stream_id`: they're told apart by payload size, and identical layouts are decoded once.
- Built-in configuration editor — edit frame fields and signal definitions in-app, save to
  `streams.json` without restarting.
- Optional PID tuning panel: send controller gains to the MCU over the same serial connection.
  Values may be negative (e.g. a reverse `Rps` setpoint).
- IMU calibration panel (placeholder: its buttons are disabled until the protocol defines an
  IMU command packet).
- Virtual device simulator (`VIRTUAL` port) for UI development without hardware.
- A time axis per stream, from a frame field and the stream's configured period. Counter
  wraps are unwrapped. A device reset starts a new segment, so time never runs backwards.
  Lost frames are drawn as gaps, never bridged by a line.
- Adjustable ring-buffer window size, and a per-stream **Period** that can be overridden for
  the session.
- Link statistics in the status bar: throughput, samples/s, CRC errors, lost frames
  (`loop_cntr` gaps) and bytes dropped while re-syncing. Hover for the full breakdown.

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
uv run python main.py                          # last used config, else the bundled streams.json
uv run python main.py --config ~/robot.json    # a specific config (remembered for next time)
```

The config file is never looked up in the current working directory, so the app can be
started from anywhere.

## Development Commands

```bash
# Run all tests (Qt tests run headless on the offscreen platform)
uv run pytest

# Machines where Qt can't load (e.g. missing libEGL): skip the real-Qt tests
uv run pytest -p no:pytest-qt

# Parser/storage micro-benchmark
uv run python tools/bench_pipeline.py

# Lint (check only)
uv run ruff check .

# Lint + auto-fix
uv run ruff check --fix .

# Format
uv run ruff format .

# Type-check
uv run mypy .
```

CI (`.github/workflows/ci.yml`) runs lint, format check, mypy and the tests on every PR.

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
6. **Period** shows the time between two frames of the shown stream, from its `time` block
   in `streams.json`. Changing it re-times that stream's whole history, for this session
   only; it turns orange while it differs from the file. Set it in the **Configuration**
   tab (Time Base) to keep it. **Samples** sets how much history every stream keeps.
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
| `time` | Optional time base: `field` (default `loop_cntr`), `scale_s` (seconds per tick of that field, default `0.005`), `step` (its increase per frame, default `1`) |
| `signals` | Map of signal IDs to display config (label, color, visibility, line style and width). Keys you add yourself are kept when the in-app editor saves |

**`loop_cntr` is mandatory** in every frame. It's the loop counter used to detect lost
frames, and by default it's also the X axis. It should be a `u32` and the first field.

**Time axis.** Time in seconds is `unwrapped(time.field) × time.scale_s`:
- For a loop counter, `scale_s` is the MCU loop period, for example
  `"time": {"scale_s": 0.001}` for a 1 kHz loop.
- For a microsecond timestamp field, use
  `"time": {"field": "t_us", "scale_s": 1e-6, "step": 1000}`.
- An integer time field that wraps (for example a `u32` µs timestamp, every 71 minutes) is
  unwrapped.
- If the field jumps backwards (the MCU restarted), a new segment starts right after the
  old one, and the status bar says so.
- A jump of more than 1.5 × `step` is drawn as a gap: frames were lost.

**Validation.** `streams.json` is checked when it's loaded and before the editor saves it:
- Errors leave a stream out of the stream list, and the status bar reports them (hover for
  details). Errors are:
  - an unknown field type or a duplicate field name
  - no `loop_cntr` field
  - a payload over 255 B
  - `stream_id` outside 0–255
  - a signal whose `field` isn't in the frame
  - a `time.field` that isn't in the frame, or a `time.scale_s`/`time.step` that isn't a
    positive number
- Warnings don't block loading. They cover:
  - an unknown `panel_type`
  - a `loop_cntr` that isn't `u32` or isn't first
  - a `time.field` other than `loop_cntr` without a `time.step`
  - unknown `time` keys

The editor refuses to save a file with errors. A signal with no data (a field the source
doesn't send) is drawn as a gap and reads `n/a` in the cursor readout. It is never plotted
as zero.

**Supported field types:** `u8`, `i8`, `u16`, `i16`, `u32`, `i32`, `u64`, `i64`, `f32`, `f64`

**Line styles:** `solid`, `dashed`, `dotted`

**Line width:** pixels, default `1`. Wider lines draw much more slowly with many samples.

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
      "time": {"field": "loop_cntr", "scale_s": 0.01, "step": 1},
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

- **Threading:** a dedicated reader thread does blocking serial reads and parses them, so
  the OS buffer is drained however busy the GUI is. `TelemetryEngine` runs in its own
  `QThread`. The GUI talks to it only through Qt signals and queued calls.
- **Data flow:** `SerialTransport` → `ReaderThread` → `FrameParser` (sync, CRC, all IDs) →
  `StreamRouter` (numpy batch decode per layout) → one `SampleStore` per stream (a versioned
  ring buffer shared between threads). `LiveFeed` then pulls snapshots of the
  visible signals into `TelemetryPlot` at up to 30 FPS, only when there's new data, and
  backs off when frames are expensive. See `docs/adr/0002-target-acquisition-pipeline.md`.
- **Performance:** hidden signals are never copied or drawn. Pausing freezes one full
  snapshot, so signals shown while paused still have data. The tooltip reuses a single
  `searchsorted` result across all signals per mouse event.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No serial ports appear | OS permission denied | Add user to `dialout` group (Linux) or grant Terminal serial access (macOS) |
| Plot is flat / no data | `stream_id` or frame layout mismatch | Check the status-bar tooltip: "Frames with unconfigured stream IDs" means no stream in `streams.json` uses the ID the MCU sends; "Size mismatches" means the field list doesn't match the firmware struct |
| Data looks corrupted | Baud rate mismatch | CRC errors and dropped bytes climb in the status bar; make firmware and UI baud rates identical |
| Gaps in traces | Frames lost | The status bar shows "lost N" (`loop_cntr` gaps); each loss is drawn as a gap in the trace |
| Time axis runs too fast or slow | `time.scale_s` doesn't match the MCU loop period | Correct **Period** on the dashboard to check, then set it in Configuration → Time Base |
| "Time counter went backwards" | The MCU restarted (or the time field reset) | Expected after a reset; the new data continues on a new segment after a gap |
| `uv run pytest` picks up wrong Python | Anaconda or system `pytest` in PATH | Always use `uv run pytest`, never bare `pytest` |
