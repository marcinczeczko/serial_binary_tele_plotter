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

- Live multi-signal plotting via pyqtgraph, in **lanes**: stacked plots on one time axis,
  each with its own Y axis. Each lane's Y range can be auto, auto-grow or manual. 34
  signals × 100k samples stay at 30 FPS.
- One window, laid out like a bench scope: one top bar (profile, port, RUN/STOP, the streams
  as tabs with a data indicator, messages, history, rate, trigger, REC, link health), a
  Signals pane (legend, visibility, lanes and the cursor readout in
  one list) and a Controls / Step response pane, each opened and closed from a tab on the
  window's edge. Which panes are open and their widths are remembered per profile.
- **Device profiles**: one file per device (`robot-1`, `esc-2`, …) with its wire format,
  baud rate and streams, picked first in the top bar. Port and baud are remembered per
  profile. The bundled `streams.json` is the `diffbot` profile.
- **Text lines**: a text profile plots boards that print lines (`IMU,120,0.51,-0.02`,
  `ENV t=24.5C h=41%`, any layout) instead of binary frames. Each stream is a line pattern.
- A bench-scope look: square flat controls, a pure black plot, scope colours on black and
  the bundled B612 / B612 Mono cockpit-display fonts (no install needed). Numbers use a dot
  and only the digits that matter (`0.12`, not `0,1200`) whatever the system locale.
- Serial connection management with port scanning and baud rate selection.
- Analysis mode: pause the plot, scrub with the cursor, click to set an anchor for delta (Δ)
  readouts across all signals.
- Multiple named streams, each with its own frame layout and signal set. **All streams are
  decoded at the same time**; the selector only chooses which one to show, so switching
  keeps each stream's history and never interrupts acquisition. Streams may share a
  `stream_id`: they're told apart by payload size, and identical layouts are decoded once.
- Built-in configuration editor (File → Edit profile), in the scope's look: the frame
  drawn byte by byte, its signals by lane, and a form per field. **Paste a C struct** to start
  a stream (or update one) and **copy any stream as a C struct** for the firmware.
- **Control panels defined in `streams.json`**: parameters (spin boxes, check boxes) and
  buttons that send command packets to the MCU over the same serial connection. The bundled
  PID tuning panel (two motors, signed values) is one such entry, and any other command can
  be added the same way, without code. Edited-but-unsent values are highlighted, columns
  can be linked, a Live mode sends as you tune, presets keep value sets, and every send is
  logged and marked on the plot.
- The dashboard remembers the port and baud rate, the shown stream, each stream's signal
  visibility and lane moves, and the panels' values between runs.
- Device simulator (`VIRTUAL` port). It generates real protocol frames for the shown
  stream, from its definition, so everything downstream is exercised as with hardware.
  Per-field waveforms and a PID motor model are configurable, and PID gains sent from a
  panel change the simulated response.
- A time axis per stream, from a frame field and the stream's configured period. Counter
  wraps are unwrapped. A device reset starts a new segment, so time never runs backwards.
  Lost frames are drawn as gaps, never bridged by a line.
- Adjustable ring-buffer window size, and a per-stream **Period** that can be overridden for
  the session.
- Link health at the right end of the top bar: the throughput, and in amber only when
  something was dropped (`CRC 3  LOST 12`: CRC errors, size mismatches, bytes dropped while
  re-syncing, lost frames from `loop_cntr` gaps, counter resets). Hover for the full
  breakdown and samples/s.
- **Recording and replay.** A session's raw bytes are saved to an `.sbtp` file, on demand or
  automatically on connect. A replay goes through the same decoding as a live session,
  errors included, at 1×–10× or maximum speed, and can be paused or stepped.
- **Export** of the shown stream (the paused view, or its whole buffer) or of every stream,
  to CSV or Parquet, with the time column first.
- **Trigger capture**, oscilloscope style: when a signal crosses a level (rising, falling or
  either), the time before and after is frozen for analysis. **Step-response metrics** (rise
  time, overshoot, settling time, steady-state error) are shown next to the previous
  capture's, whose traces are overlaid, e.g. before and after a gain change.

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

Parquet export needs `pyarrow`, an optional extra: `uv sync --extra parquet` (or
`--all-extras`). CSV export works without it.

## Running

```bash
uv run python main.py                          # last used profile, else the bundled streams.json
uv run python main.py --config ~/robot.json    # a specific profile file (remembered for next time)
```

The profile file is never looked up in the current working directory, so the app can be
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

The window has one **top bar**, left to right: the profile, the port and `Connect`, the
`RUN`/`STOP` box, the stream tabs, a short message, `H` (the history shown), the rate and
points, `T` (the trigger), `REC` and the link health. Below it, the **Signals** pane on the left and the **Tune** / **Step response** pane on the
right. The tabs on the window's edges (`SIGNALS`; `TUNE` and `STEP`, or `TERMINAL` for a
text profile) are always there: a lit tab closes its pane, an unlit one opens it. Keys: `[`
toggles Signals, `]` the right pane, `\` both (plot only); a text field you're typing in
keeps those keys. Open panes, the right pane's view and the pane widths are remembered per
profile; drag the edge between a pane and the plot to resize it.

1. Launch the app with `uv run python main.py`.
2. The top bar starts with the **device profile** (`diffbot ▾`): what the device sends and
   which streams to show (the format is in its tooltip and menu). Its menu lists the profiles in the profiles folder
   (default `~/telemetry-profiles`), the bundled `streams.json` and files opened from
   elsewhere, plus **New profile…** (name, format, baud; empty or a copy of the current
   profile), **Open profile file…** and **Edit profile…**. Profiles switch only while
   disconnected. Each profile keeps its own shown stream, view, panel values, port and
   baud; a profile's `baud` is used until you pick another one for it.
3. Pick a **serial port** (the list is re-read each time it opens) and, for a real port, the
   **baud rate**, then click `Connect`. Use `VIRTUAL` for the built-in simulator (it has no
   baud). Both are remembered for the next run (a port that's gone isn't selected). The bar's
   right end shows the link health. Messages (what was sent, a file saved) fade after a few
   seconds; warnings and errors stay until the next message.
4. Pick a **stream tab** in the top bar. Every stream is decoded all the time, so a tab is
   only a view. The square before each name is green while its frames arrive and hollow when
   none do (check its `stream_id` and layout); the rate is in the tab's tooltip. The shown
   stream's rate is also next to `H` (`200 Hz` over `2.00k pts`).
5. **Signals** pane: the shown stream's signals, grouped by lane (`SPEED rps`).
   - A left/right pair is one row: its name, then an **L** and an **R** swatch in the
     traces' colours. Pairs are found by the label prefix `L: ` / `R: `, else by the key
     prefix `left_` / `right_`, within a lane; any other signal has one swatch. In the
     bundled profile the right copy is drawn dashed (its `line.style`), and a dashed
     trace's swatch has a gap in the middle.
   - A swatch is the toggle: filled = shown, hollow = hidden. Right-click a lane header to
     show or hide all of it. Ctrl+F, or typing in the list, opens a filter; Esc closes it.
   - Hovering the plot shows each shown signal's value at the cursor (**A**) beside its
     swatch, in its colour, under a boxed header `A 0.42s  B 0.76s  ΔT +0.34s` (**B** is
     the Δ anchor, ΔT = B − A; each signal's Δ is in its swatch's tooltip). Values show 4
     significant digits, at most 3 decimals. Next to a gap (lost frames), a value reads
     `n/a` rather than being interpolated across the gap.
   - Drag a row onto another lane (or into the empty space below the list, for a new
     lane), or right-click it → **Move to lane** / **New lane**; both signals of a pair
     move.
   - Visibility and lane moves are remembered per stream (and per config file) between
     runs, on top of `streams.json`. **View → Reset view to the profile** forgets them for
     the shown stream. To change the file itself, use the stream editor (a signal's check
     box and lane).
   - Live, time follows the newest data and the mouse zooms or pans a lane's Y. That lane
     then holds its range. Right-click a lane → **Lane Y range** to choose Auto (fit the
     data in view), Auto-grow (only widens) or Manual, and whether zero is always included.
6. The `RUN` box (or Space) turns to `STOP` and enters analysis mode: zoom and pan freely (Auto lanes fit what's in
   view). A click drops the Δ anchor **B** (drag it to move it), and the Signals pane shows
   A, B and ΔT, and each signal's Δ in its tooltip. `STOP` again (or Space) returns to the live view; it works even after a session ended
   (disconnected, the box shows `—`).
7. The **`H`** item (`H 10.0 s`, the history shown: period × samples) opens the
   **Period** of the shown stream (the time between two frames, from its `time` block) and
   the **Samples** of history every stream keeps. Changing the period re-times that
   stream's whole history for this session; the value turns amber while it differs from
   the file. Set it in the stream editor (X axis × time per tick) to keep it.
8. **Tune** pane (Controls): the shown stream's control panel (e.g. PID Tuning for the `pid`
   streams), generated from `streams.json` `panels`. Its buttons send commands while
   connected; the top bar's message says what was sent, or why not.
   - After a send, a value that differs from what was last sent is highlighted and counted
     (`2 unsent`); **Revert** (or Esc) puts them back. (Before the first send, what the
     device holds is unknown, so nothing is marked.)
   - Drag a number parameter's label left or right to change it, like a knob: one step per
     4 px, ×10 with Shift, ×0.1 with Alt. A linked row stays equal; an unlinked row moves
     every column by the same amount.
   - With two or more columns, a row's **⇄** keeps its columns equal (e.g. the same Kp for
     both motors); rows whose values start equal start linked. **Link columns** does all.
   - **Live** sends a column 150 ms after its values stop changing (at most every 100 ms):
     tune by dragging a spin box. It sends to the device as you edit, so it's off by
     default; **Manual** sends only on a button. **Ctrl+Enter** presses the panel's main
     button (the one spanning the panel).
   - **Presets**: **Save as…** keeps the current values under a name; choosing one loads
     its values (then send them). Values, presets and the Live choice are remembered.
   - Under the panel, **Sent** lists every send, numbered, with what changed since the
     previous one (`kp 0.1 → 0.25`); refused sends are listed in red. The number is also a
     dashed marker on the plot at the stream time it was sent. **Send again** (or a double
     click) re-sends a row's exact packet.
9. **File → Edit profile…** (Ctrl+,) opens the current profile in the stream editor, laid
   out like the scope:
   - The profile row: its name, format (read-only: chosen at New profile) and baud.
   - Streams are tabs. One row holds the stream's key, name, ID, byte order, X axis and time
     per tick (`5 ms`, `1 µs`), step and **Controls** panel.
   - **Frame**: the payload as the device sends it, 32 bytes per row, each field in its
     signal's color (grey: not plotted). Click a field to select it; right-click to plot
     it, add a field after it, move it or remove it.
   - Below, the signals by lane, as in the Signals pane, and **Not plotted** for the fields
     without a signal. Drag a field onto a lane to plot it there, a signal onto another lane
     to move it, or onto Not plotted to remove it. The check box is "shown when the stream
     opens". Right-click a lane to rename it.
   - The form on the right edits the selected field (name, type) and its signal (label,
     lane: pick one or type a new name, color, line, shown).
   - **From C struct…** reads a pasted struct (or just its member lines, or a `.h` file)
     and draws its frame as you type: `uint8_t`…`int64_t`, `float`, `double`, `bool`,
     `char`/`short`/`int`, several names per line, arrays (`ticks[2]` → `ticks_0`,
     `ticks_1`), `__attribute__((packed))` and a `#define` with `ID` in its name. Without
     `packed`, the compiler's padding becomes `_pad` fields. What it can't read (`long`,
     pointers, bit-fields, nested structs) is listed, not guessed. It creates a new stream
     (every field plotted, labelled with its name), or **replaces the fields** of the
     shown stream: fields that keep their name keep their label, color and lane.
   - **Copy as C struct** puts the stream on the clipboard as a packed struct with its ID
     and a `_Static_assert` on its size.
   - In a **text profile**, **Pattern** replaces ID and byte order, and **Line** replaces
     the frame: the pattern's fixed text, and a block per value in its signal's color.
     Editing the pattern re-derives the values: one that keeps its name keeps its type and
     signal, a new one is a number, a removed one goes with its signal. An invalid pattern
     is outlined red, with the reason in the status line, and isn't applied (Esc drops
     it). Under the blocks is the last line: the newest one the stream matched while
     connected (else the newest unmatched one), with `✓ matches` or `✗ no match`. The X
     axis offers the integer values and `(line number)`. The form edits a value: name,
     type (number, integer, signed integer), position, and Add value after / Remove value,
     which edit the pattern. The C struct buttons are for binary profiles only.
   - **From console output…** (text profiles) finds the streams for you: paste what the
     board prints, or **Listen on the port for 5 s** while connected. Lines are split
     into numbers and the text between them; each layout seen at least twice becomes a
     pattern (a line seen once, like a boot banner, is listed and left out). A value is
     named from the text before it (`t=24.5` → `t`), else `v1`, `v2`…; whole numbers
     are integers (signed if a negative was seen), the rest numbers. The first integer
     that goes up by a steady step is the X axis (1 ms per tick when its name says `ms`).
     Untick a pattern or rename it, then **Create N streams**.
   - **Copy as printf** puts the C line that prints the stream's pattern on the
     clipboard, e.g. `printf("ENV t=%fC h=%lu%%\r\n", t, h);`.
   - Save (Ctrl+S) is orange while there are unsaved changes; the status line shows the
     stream's size and its first problem as you edit. Saving updates `streams.json` (the
     previous file is kept as `streams.json.bak`); a file with errors isn't saved.
     Commands and panels are edited in the file itself.
10. **Recording** menu (and the top bar's `REC`, which turns red with the elapsed time):
   - **Record** (Ctrl+R) saves everything the port delivers, until you stop it or
     disconnect. The file goes to the recordings folder (default `~/telemetry-recordings`),
     named after the shown stream and the time, e.g. `pid_20260924-201530.sbtp`. `REC`'s
     tooltip shows the file.
   - **Record automatically on connect** records every session (not replays).
   - **Replay a recording…** plays a file through the normal pipeline: every stream is
     decoded with the current `streams.json`, and the top bar warns if the recording was
     made with different frame layouts. Choose the **Replay speed**, **Pause replay** or
     **Step replay** (one recorded read at a time) from the same menu. When the file ends,
     the session stops with "Replay finished".
11. **File → Export shown stream…** writes every signal of the shown stream, hidden ones
    too. While paused, that's the time range in view; otherwise the whole buffer. **Export
    all streams…** writes each stream's buffer to its own file (`<name>_<stream>.csv`);
    streams without data are skipped. The time column (`time_s`) comes first. A value
    missing from a frame is an empty CSV field (a Parquet null), and gap markers are left
    out.
12. **Trigger** (`T` in the top bar):
    - Pick the trigger **Signal**, **Edge**, **Level**, and how much to keep **Before** and
      **After** the crossing, then click **Arm** (while connected). While armed, `T` turns
      orange and shows the source's colour, the edge and the level (`╲ 0.15 ARMED`; the
      tooltip says `↘ L: Target Setpoint < 0.15`), and the level is a
      dashed line on the plot: drag it to change the level.
    - At the crossing, the capture is frozen in analysis mode with the Δ anchor at the
      trigger time, and the time before it is shaded. Single shot: arm again for the next
      capture. If the buffer holds less than **Before**, the top bar says how much there
      was: raise **Samples** to keep more.
    - The **Step response** pane (it comes forward on a capture): choose the **Setpoint**
      and **Measurement** signals. The table shows rise time (10–90 %), overshoot, settling
      time (±2 %) and steady-state error for this capture and the previous one, with the
      change (green when smaller). With **Overlay the previous capture** on, the previous
      capture's traces are drawn dashed, lined up at its trigger. `Resume` removes the
      overlay.

## Connecting Your MCU

Two things are needed on the firmware side:

1. **Implement the binary protocol** — see the [Binary Protocol](#binary-protocol) section below.
2. **Define your frame layout** in `streams.json` — see [Configuration](#configuration-streamsjson).

No library is required on the MCU. The protocol is a simple packed struct with a header and two
CRC bytes, straightforward to implement in C/C++.

A board that prints text lines instead (`Serial.println`) needs no protocol at all: use a
text profile, see [Text lines](#text-lines).

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

Commands sent to the MCU use the same framing, with `TYPE` = the command's `packet_id` and
a payload laid out by its `fields` (see [Commands and control panels](#commands-and-control-panels)).
The bundled PID panel sends `0x10` (`motor_id: u8`, then `kp ki k1 k2 k3 k_aw alpha rps: f32`,
`use_ramp use_pi: u8`, 35 B) and `0x11` (those ten gains for the left motor, then the right,
68 B).

## Configuration: `streams.json`

A config file is a **device profile**: the single source of truth for one device's streams,
commands and panels. Edit it directly or use the in-app stream editor. The bundled
`streams.json` is one; File → New profile makes more. The document has these top-level keys:

| Key | Description |
|-----|-------------|
| `schema_version` | The file format version, currently `3` |
| `profile` | Optional: `name` (default: the file name), `format` (the wire format: `binary`, the default, or `text`, see [Text lines](#text-lines)) and `baud` (the baud rate to connect at, until you pick another for this profile). An unknown format makes the file unusable |
| `commands` | Optional: command packets the app can send (see [Commands and control panels](#commands-and-control-panels)) |
| `panels` | Optional: control panels that send those commands |
| `streams` | The telemetry streams, by key |

A file without `schema_version` is version 1 (before panels were configurable). It's read
as the current version: a stream's `panel_type: "pid"` becomes `controls: "diffbot_pid"`,
with the PID commands and panel added, and `"imu"` (which sent nothing) is dropped. A
version 2 file is read unchanged as a binary profile named after the file. The top bar
says so, and the file changes only when you save it from the stream editor. A file with a
newer version than the app knows is refused. Recordings keep the profile's name and format,
so a replay decodes with the format it was recorded with.

Each stream entry:

| Key | Description |
|-----|-------------|
| `name` | Display name, on the stream's tab |
| `controls` | Optional: the key of a panel in `panels` to show in the Tune pane for this stream |
| `frame.stream_id` | Packet type byte — must match the `TYPE` field sent by the MCU |
| `frame.endianness` | `"little"` or `"big"` — must match the MCU's byte order |
| `frame.fields` | Ordered list of `{name, type}` matching the C struct field order |
| `time` | Optional time base: `field` (default `loop_cntr`), `scale_s` (seconds per tick of that field, default `0.005`), `step` (its increase per frame, default `1`) |
| `sim` | Optional: what the `VIRTUAL` port generates for this stream (see [Simulator](#simulator-virtual-port)) |
| `groups` | Optional lanes: `{id: {label, order, y_range}}`. `label` is the lane's Y axis title. `y_range` is `{mode: auto\|auto-grow\|manual, min, max, include_zero}` |
| `signals` | Map of signal IDs to display config: label, color, visibility, line style and width, `group` (its lane; default: the main lane) and `y_range` (used as a manual lane's bounds when its group sets none). Keys you add yourself are kept when the in-app editor saves |

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
  old one, and the top bar says so.
- A jump of more than 1.5 × `step` is drawn as a gap: frames were lost.

**Validation.** `streams.json` is checked when it's loaded and before the editor saves it:
- Errors leave a stream out of the stream list, and the top bar reports them (hover for
  details). Errors are:
  - an unknown field type or a duplicate field name
  - no `loop_cntr` field
  - a payload over 255 B
  - `stream_id` outside 0–255
  - a signal whose `field` isn't in the frame
  - a `time.field` that isn't in the frame, or a `time.scale_s`/`time.step` that isn't a
    positive number
- Commands and panels are checked too. One with errors is left out (and a panel that uses
  it), never the telemetry. See [Commands and control panels](#commands-and-control-panels).
- Warnings don't block loading. They cover:
  - a `controls` panel that doesn't exist or has errors
  - a `loop_cntr` that isn't `u32` or isn't first
  - a `time.field` other than `loop_cntr` without a `time.step`
  - unknown `time` keys
  - anything wrong in a `sim` block, or in `groups`, a signal's `group` or `y_range`

The editor refuses to save a file with errors. A signal with no data (a field the source
doesn't send) is drawn as a gap and reads `n/a` in the cursor readout. It is never plotted
as zero.

### Text lines

A profile with `"format": "text"` reads lines of text. Each stream's `frame.pattern` is one
line layout: fixed text, and a `{name}` slot for each number. `frame.fields` gives each
slot a type, in the pattern's order.

```json
{
  "schema_version": 3,
  "profile": {"name": "arduino-imu", "format": "text", "baud": 115200},
  "streams": {
    "imu": {
      "name": "IMU",
      "frame": {
        "pattern": "IMU,{ms},{ax},{ay}",
        "fields": [{"name": "ms", "type": "u32"}, {"name": "ax", "type": "f32"},
                   {"name": "ay", "type": "f32"}]
      },
      "time": {"field": "ms", "scale_s": 0.001, "step": 10},
      "signals": {"ax": {"field": "ax", "label": "Acc X"}}
    },
    "env": {
      "name": "Environment",
      "frame": {
        "pattern": "ENV t={t}C h={h}%",
        "fields": [{"name": "t", "type": "f32"}, {"name": "h", "type": "u32"}]
      },
      "signals": {"t": {"field": "t", "label": "Temp"}}
    }
  }
}
```

For this profile the firmware prints, for example,
`printf("IMU,%lu,%f,%f\r\n", ms, ax, ay);` and `printf("ENV t=%.1fC h=%u%%\r\n", t, h);`.

**Pattern grammar.**
- `{name}` is a number slot. The name is letters, digits and `_`. `_line` is reserved.
- Everything else is fixed text, matched exactly. Write `{{` and `}}` for literal braces.
  A run of spaces matches any run of whitespace, so `%6.2f` padding is fine.
- Slots need fixed text between them: `{a}{b}` is an error.
- A slot takes a number: a sign, decimals, an exponent, `nan` or `inf` (`-.5e-3`, `1E3`).
  An empty slot (`1,,3`) or `nan` in a float field is a gap in the plot.
- An integer field (`u32`, `i32`, …) needs a whole number in its range. Anything else
  (`1.5`, `nan`, a negative `u32`) drops the line, and it's counted.

**Matching.** Lines end at `\n`. A trailing `\r` and surrounding whitespace are ignored,
and blank lines are skipped. Each line is tried against the streams in file order, and
the first full match wins. A line that matches no pattern (a boot banner, a debug print)
is counted as *unmatched*. A line longer than 1024 bytes is dropped and counted. The
link health's tooltip shows these counts for a text profile.

**X axis.** `time.field` works as for binary streams. Without one, it's `loop_cntr` if the
pattern has that slot, otherwise the stream's **line number** (`_line`): each matched
line is one tick, and `time.scale_s` is the time per line. `loop_cntr` is optional in a
text stream. An integer time slot is checked for gaps and resets like a binary
`loop_cntr`.

**Validation.** A text stream needs a valid `pattern` whose slots are its field names in
order. Two streams with the same pattern are a warning, because the second never
matches. `stream_id`, `endianness` and `packed` mean nothing for text and are ignored
with a warning. The 255 B payload limit doesn't apply. A `pattern` in a binary profile
is ignored, with a warning.

`VIRTUAL` prints the shown stream's lines (`\r\n` endings) at its period, plus a
`# sim tick` line once a second that no pattern matches, and answers each line it's sent
with `ok: <line>`. Recordings of a text profile replay as text.

**Terminal.** A text profile sends from the **Terminal** in the right pane, as in a
serial monitor: type a line and press Enter. It's sent with the line ending chosen next
to the input (`LF`, `CR LF`, `CR` or `none`, remembered per profile), numbered (`▲ 3`)
and marked on the plot like a panel send. Up/Down recall the lines sent this session,
Esc clears the input. Only ASCII is sent; anything else is refused, not changed. The
board's replies, the lines no stream pattern matches, appear in grey under what you sent
(about once a second, at most 100 at a time). A text profile's `commands` and `panels`
are ignored, with a warning: they would send binary packets.

### Simulator (`VIRTUAL` port)

`VIRTUAL` streams real frames for the shown stream at its configured period. The frames
have the same header, CRC and packed payload the MCU would send, so the parser, time axis
and link statistics all work as they do with hardware. What each field carries:

- The time field and `loop_cntr` count frames and wrap like the MCU's integers.
- Fields listed in `sim.fields` follow their spec:
  - `wave`: `sine`, `step` (a square wave), `noise`, `const` or `counter`
  - parameters: `amp`, `freq_hz`, `offset`, `phase_deg`, `noise` (standard deviation)
- `"model": "pid_motor"` fills `left_*`/`right_*` PID fields from a simulated
  feedforward + PI loop on a DC motor. PID gains sent from a panel change it: `kp`,
  `ki`, `k_aw`, `alpha` (measurement filter), `rps` (target amplitude), `use_ramp` and
  `use_pi`. `k1` is the feedforward gain and `k2` the friction offset; `k3` is ignored.
- Any other field gets a default sine, distinct per field.

```json
"sim": {
  "fields": {
    "acc_z": {"wave": "const", "offset": 1.0, "noise": 0.03},
    "gyro_z": {"wave": "step", "amp": 45, "freq_hz": 0.2}
  }
}
```

Problems in `sim` are only warnings: they affect `VIRTUAL`, never real data.

### Commands and control panels

A **command** is a packet the app sends: an ID and a packed payload, laid out like a frame.
A **panel** is a grid of parameters with buttons that send commands. A stream shows a panel
with `"controls": "<panel key>"`.

```json
"commands": {
  "set_speed": {
    "label": "Speed setpoint",
    "packet_id": 32,
    "endianness": "little",
    "fields": [
      {"name": "motor_id", "type": "u8"},
      {"name": "rps", "type": "f32", "param": "rps"},
      {"name": "ramp", "type": "u8", "param": "ramp"},
      {"name": "version", "type": "u8", "value": 1}
    ]
  },
  "zero_gyro": {"label": "Zero gyroscope", "packet_id": 33, "fields": []}
},
"panels": {
  "drive": {
    "title": "Drive",
    "columns": ["Left", "Right"],
    "parameters": {
      "rps": {"label": "Speed [rps]", "kind": "float", "default": 0.5,
              "min": -20, "max": 20, "step": 0.1, "decimals": 2},
      "ramp": {"label": "Ramp", "kind": "bool", "default": true}
    },
    "buttons": [
      {"label": "Send left", "command": "set_speed", "column": "Left", "values": {"motor_id": 0}},
      {"label": "Send right", "command": "set_speed", "column": "Right", "values": {"motor_id": 1}}
    ]
  },
  "imu": {"title": "IMU", "buttons": [{"label": "Zero gyroscope", "command": "zero_gyro"}]}
}
```

- **Command fields** are `{name, type}` like frame fields (payload ≤ 255 B, `LEN` may be
  0). A field's value comes from, in this order: the pressed button's `values`; the
  field's constant `value`; the panel parameter named by `param`, from the field's
  `column` or else the button's.
- **Parameters** have a `kind`: `float` (spin box with `decimals` and `step`), `int` or
  `bool` (check box, sent as 1/0), with `default`, `min` and `max` (defaults ±1000).
- **Columns** are optional. With them, each parameter has one value per column, and a
  button placed in a column sits under it and sends that column's values. A button with no
  column spans the panel; its command's fields then name their `column` (like the bundled
  `pid_both`, which sends both motors in one packet).
- A value that doesn't fit its field (300 in a `u8`, 1.5 in an integer) is refused with a
  message, never truncated or wrapped.
- Errors (unknown command, a field that gets no value, a bad type or ID) leave that command
  or panel out; the telemetry still loads. On `VIRTUAL`, the simulator decodes commands with
  these layouts, and the PID motor model applies any fields named like its gains (`kp`,
  `ki`, …, `use_pi`), for the motor in `motor_id` or with `left_`/`right_` prefixes.

**Supported field types:** `u8`, `i8`, `u16`, `i16`, `u32`, `i32`, `u64`, `i64`, `f32`, `f64`

**Line styles:** `solid`, `dashed`, `dotted`

**Line width:** pixels, default `1`. Wider lines draw much more slowly with many samples.

Example — a minimal stream definition:

```json
{
  "schema_version": 2,
  "streams": {
    "my_sensor": {
      "name": "Sensor Data",
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
      "groups": {
        "temp": {"label": "Temperature [°C]", "order": 1},
        "press": {"label": "Pressure [hPa]", "order": 2, "y_range": {"mode": "auto-grow"}}
      },
      "signals": {
        "temperature": {
          "label": "Temperature (°C)",
          "field": "temperature",
          "group": "temp",
          "color": "#FF5733",
          "visible": true,
          "line": {"style": "solid", "width": 2}
        },
        "pressure": {
          "label": "Pressure (hPa)",
          "field": "pressure",
          "group": "press",
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
serial_binary_tele_plotter/
├── main.py                    # Entry point, QApplication setup, --config
├── streams.json               # Stream and signal definitions (source of truth)
├── styles.py                  # The scope look: square flat QSS, palette, B612 fonts
├── assets/fonts/              # B612 and B612 Mono (SIL OFL 1.1, OFL.txt), loaded at start-up
├── core/                      # protocol/, transport/, simulation/ are Qt-free
│   ├── types.py               # Shared TypedDicts and Enums
│   ├── config/                # streams.json: document (load, migrate, validate, save),
│   │                          #   streams, controls (commands, panels), schema migrations,
│   │                          #   the editor's StreamDraft, C structs in and out,
│   │                          #   device profiles (the `profile` block, listing)
│   ├── protocol/              # Wire format: link (the LinkDecoder slot), CRC-8, FrameParser,
│   │                          #   RecordDecoder (numpy), StreamRouter (multi-stream), stats,
│   │                          #   commands (encoding)
│   ├── transport/             # Transport interface, SerialTransport, SimTransport,
│   │                          #   ReplayTransport, ReaderThread
│   ├── simulation/            # Frame synthesis from `sim` config, PID motor model
│   ├── recording/             # .sbtp raw recordings: writer and reader
│   ├── analysis/              # Export (CSV/Parquet), trigger detection, step-response metrics
│   └── acquisition/
│       ├── engine.py          # TelemetryEngine: lifecycle state machine (QThread)
│       ├── storage.py         # SampleStore: versioned ring buffer per stream
│       ├── lod.py             # Incremental min/max level of detail for live drawing
│       └── timebase.py        # Per-stream time: unwrap, resets, gap markers
├── ui/
│   ├── main_window.py         # Composition (top bar, panes), engine thread, wiring, menus
│   ├── app_settings.py        # QSettings keys (config path, recording options)
│   ├── ui_state.py            # Remembered port, stream, view overrides, panel values,
│   │                          #   presets, Live mode, window layout
│   ├── charts/                # TelemetryPlot (lanes, markers, trigger line), LiveFeed (pulls
│   │                          #   the store's overview), TriggerController, lanes/series
│   ├── panels/                # Top bar (connection, RUN/STOP, stream tabs, H/T/REC), Signals
│   │                          #   (legend + readout), control panels + send log, time window,
│   │                          #   trigger
│   └── config/                # Stream editor (File → Edit profile): frame view,
│                              #   lanes, field form, From C struct… dialog
├── tests/                     # pytest; `qt`-marked tests use real Qt
├── tools/                     # bench_pipeline.py (parser/storage), bench_render.py (GUI FPS)
└── docs/                      # Roadmap, project log, reviews, ADRs
```

## Architecture Notes

- **Threading:** a dedicated reader thread does blocking serial reads and parses them, so
  the OS buffer is drained however busy the GUI is. `TelemetryEngine` runs in its own
  `QThread`. The GUI talks to it only through Qt signals and queued calls.
- **Data flow:** `SerialTransport`, `SimTransport` or `ReplayTransport` → `ReaderThread` → a
  `LinkDecoder` (binary frames: `FrameParser` for sync, CRC and all IDs, then `StreamRouter`
  for a numpy batch decode per layout) → one `SampleStore` per stream (a versioned
  ring buffer shared between threads). `LiveFeed` then pulls snapshots of the
  visible signals into `TelemetryPlot` at up to 30 FPS, only when there's new data, and
  backs off when frames are expensive. See `docs/adr/0002-target-acquisition-pipeline.md`.
- **Recordings** keep the raw bytes of each read, with host timestamps, after a JSON header
  holding the stream config. So a replay reproduces the session, and old recordings can be
  decoded again after a config or decoder fix. Format: `docs/adr/0006-raw-recording-and-replay.md`.
- **Performance:** hidden signals are never copied or drawn. The store keeps an incremental
  min/max summary of its buffer, so a live frame reads ~1000 points per signal whatever the
  buffer size. The live cursor readout still asks the store for exact values. Pausing
  freezes every sample, so you can zoom into full resolution. `tools/bench_render.py`
  checks the budget: 34 signals × 100k samples at 1 kHz, ≥ 30 FPS, nothing lost.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No serial ports appear | OS permission denied | Add user to `dialout` group (Linux) or grant Terminal serial access (macOS) |
| Plot is flat / no data | `stream_id` or frame layout mismatch | Check the link health's tooltip (top bar, right): "Frames with unconfigured stream IDs" means no stream in `streams.json` uses the ID the MCU sends; "Size mismatches" means the field list doesn't match the firmware struct |
| Data looks corrupted | Baud rate mismatch | The link health turns amber with `CRC` and `SYNC` counts; make firmware and UI baud rates identical |
| Gaps in traces | Frames lost | The link health shows `LOST N` (`loop_cntr` gaps); each loss is drawn as a gap in the trace |
| Time axis runs too fast or slow | `time.scale_s` doesn't match the MCU loop period | Correct the **Period** (time window button) to check, then set it in the stream editor → Time Base |
| "Time counter went backwards" | The MCU restarted (or the time field reset) | Expected after a reset; the new data continues on a new segment after a gap |
| "recorded with different frame layouts" | `streams.json` changed since the recording | The replay decodes with the current config; restore the old layout (the recording's header has it) to decode it as recorded |
| Parquet isn't offered when exporting | `pyarrow` isn't installed | `uv sync --extra parquet` |
| `uv run pytest` picks up wrong Python | Anaconda or system `pytest` in PATH | Always use `uv run pytest`, never bare `pytest` |
