# Architecture, Performance & Correctness Review — 2026-09-24

Reviewed revision: `637f7ba` (branch `main`, "Fix vscode for uv").
Scope: all of `core/`, `ui/`, `main.py`, `styles.py`, `streams.json`, tests, tooling.
Method: full read of the code, plus measurements
(`uv run pytest`, `ruff`, `mypy`, and a parser/storage micro-benchmark, reproduced in
[Appendix A](#appendix-a--benchmark-script)).

Finding IDs (`C*` correctness, `P*` performance, `A*` architecture, `T*` tooling) are stable.
[`docs/roadmap.md`](../roadmap.md) and future commits refer to them.

---

## 1. What the app is and how it works today

Goal: a desktop tool that connects to an embedded board over serial, decodes binary telemetry
frames (PID terms, IMU, etc.), plots them live, and lets the user pause, measure and tune
the controller (send PID gains back).

```
 GUI thread                                   Engine QThread
 ───────────                                  ──────────────
 MainWindow ── invokeMethod / signals ──────▶ TelemetryEngine
   │                                            ├─ serial_timer (10 ms) → pyserial.in_waiting/read
   │                                            │     → ProtocolHandler.add_data/process_available_frames
   │                                            │         → FrameDecoder (struct.unpack → dict)
   │                                            │             → SignalDataManager.store_frame (numpy ring)
   │                                            ├─ VirtualDevice QTimer → dict frame → store_frame
   │                                            └─ gui_update_timer (100 ms) → get_plot_data()
   │                                                  (copies ALL signals, computes min/max)
   ◀──────────── data_ready(dict of numpy arrays) ────┘
 TelemetryPlot.on_data_ready → setData() per curve, setX/YRange every 200 ms
```

Some of the basic choices are good and worth keeping:

- The wire format (magic, type, len, header CRC, payload, payload CRC) is simple and
  self-synchronising. Validating the header CRC before trusting `LEN` is the right call.
- Frame layouts come from `streams.json`, not code. That fits a generic tool.
- Using the MCU's `loop_cntr` as the X axis, instead of host arrival time, is correct.
  Host timestamps would show USB/OS batching jitter as signal jitter.
- Numpy ring buffers, a separate acquisition thread, and cross-thread signals are a sound
  starting point.
- There's a virtual device, so you can work on the app without a board.

The problems are concentrated in five areas:

1. The receive path loses data under realistic load.
2. The render path copies and draws much more than it needs to.
3. Time-base handling is fragile.
4. The config editor can silently corrupt configuration.
5. The architecture handles only one stream at a time, can't record or replay, and puts
   every signal on one Y axis. That limits it as an analysis tool.

---

## 2. Measurements

| What | Result |
|---|---|
| `uv run pytest -p no:pytest-qt` | 22 passed (Qt is **stubbed** in `tests/conftest.py`; no real widget is exercised) |
| `uv run pytest` (headless container) | INTERNALERROR: `pytest-qt` imports QtGui → `libEGL.so.1` missing |
| `uv run ruff check .` | clean |
| `uv run mypy core ui main.py` | **40 errors** in 5 files (`strict = True` is configured but not enforced); 125 incl. tests |
| Parse + store, `pid` stream (140 B payload, 146 B frame), 900 B chunks | **66.8k frames/s, 9.8 MB/s**. Fine for the current single-stream load |
| CRC-8 (pure Python table) on 140 B | 3.6 µs/frame |
| Same byte stream fed in **5000 B chunks** | **0 / 20000 frames decoded** (see C1) |
| `get_plot_data`, 34 signals, 2 000 samples | 0.7 ms per tick, 0.5 MB packet |
| `get_plot_data`, 34 signals, 20 000 samples | 4.6 ms per tick, 5.4 MB packet |
| `get_plot_data`, 34 signals, 100 000 samples (UI max) | **26 ms per tick, 27 MB packet** → ~270 MB/s of allocation at 10 Hz |

---

## 3. Correctness findings

### C1 — Critical: RX buffer guard throws away all data when a read exceeds 4 KiB
`core/protocol/handler.py:95-100`. `process_available_frames()` checks `len(rx_buffer) > 4096`
*before* parsing and then clears the whole buffer, including complete, valid frames.
`add_data()` has just appended the entire `in_waiting` chunk, so any single read over
4 KiB is discarded without being parsed. Measured: 5000-byte chunks decode 0 of 20 000 frames.

When it triggers: the reader polls every 10 ms. At 921 600 baud (92 kB/s), a stall of
about 45 ms is enough. That can be a GC pause, a 26 ms `get_plot_data` holding the GIL,
or GUI contention. On USB-CDC boards (STM32 native USB, RP2040, ESP32-S3) the "baud" is
ignored and throughput can reach MB/s, so the app will lose data **continuously**.
The only log message is behind the `TRACE_DECODE` flag, so it fails silently.

Fix: parse first. Afterwards, if the unconsumed remainder is larger than
`max_frame_len` (5 + 255 + 1 = 261 B) and still has no sync, drop only the garbage prefix.
Count every drop in link statistics (C13).

### C2 — High: time axis comes from a UI setting and breaks on counter reset
`core/acquisition/storage.py:95`: `time = loop_cntr × sample_period_s`, where the period
is whatever the user typed in the *UI* (default 5 ms).
- If the UI period differs from the MCU loop period, every time readout, Δt and slope is
  wrong. Nothing warns the user.
- Changing the period while running rescales the whole history retroactively, because
  time is computed at emit time.
- When the MCU resets or reflashes, `loop_cntr` restarts at 0. Time goes non-monotonic,
  curves draw a line back to the left, and the cursor readout
  (`np.searchsorted` in `ui/charts/telemetry_plot.py:317`) returns garbage because it
  assumes sorted time. u32 wrap has the same effect.
- Dropped frames leave gaps in `loop_cntr` (fine), but the gaps are drawn as straight
  interpolated lines. You can't see that data is missing.

Fix: the time base should be a per-stream config property (`time_field`, `time_scale`,
e.g. µs → s). Detect a counter reset or wrap and either unwrap or start a new segment
(clear, with a status message). Insert NaN at gaps larger than N periods and use
`connect="finite"` so gaps show.

### C3 — High: missing fields silently read as 0, and the config isn't validated
`core/acquisition/storage.py:79`: `decoded_frame.get(field, 0.0)`. A signal whose `field`
doesn't exist in the frame plots as a flat zero line, which looks like real data.
- The virtual PID simulator (`core/acquisition/virtual.py:181-195`) doesn't produce
  `left_u_ff`, `left_u_pi`, `left_u_virtual`, `left_u_sat`, `left_aw_term`, `left_pwm_cmd`
  (and the right-side equivalents) that the `pid` stream plots. On VIRTUAL, those traces
  are fake zeros.
- `core/config.py` checks only that `streams` is a dict of dicts. Nothing checks that
  `signal.field` is in `frame.fields`, that `loop_cntr` exists and is `u32` (the README
  says it's mandatory), that field names and signal keys are unique, that `stream_id` is
  0-255, or that the payload is at most 255 B (LEN is one byte). A bad type only fails
  later, when the decoder is built on the worker thread. That error goes to the status bar
  and leaves the previous decoder active.

Fix: a real validation pass at load *and* before save, with actionable errors. Use NaN
for missing values.

### C4 — High: the config editor loses or corrupts data
`ui/config/tab.py`, `ui/config/stream_editor.py`:
- a) `on_stream_selected` (`tab.py:110-115`) loads the next stream without committing
  the one being edited. `save_current()` only runs on "Save to disk" and only for the
  selected stream. If you edit stream A, click stream B, then save, A's edits are gone.
- b) `get_data()` hard-codes `"endianness": "little"` and `"packed": True`
  (`stream_editor.py:194-199`). Opening and saving a big-endian stream silently turns it
  little-endian.
- c) Line `width` is always written as `2` (`stream_editor.py:220`), which overwrites any
  custom width.
- d) The signal key is derived from the field name (`stream_editor.py:213`). Two signals
  on the same field, for example with different labels, collide and the last one wins.
- e) The "Field Map" combo boxes are filled once when a row is created. They aren't
  refreshed when frame fields are added or renamed, so new fields can't be picked.
- f) Saving calls `MainWindow._reload_configuration`, which reloads the file twice
  (`main_window.py:138` and `container.py:191`). It also always jumps to stream index 0,
  so a running session switches streams even if you edited a different one.

### C5 — Medium: GUI thread calls into the worker thread directly, with a race
The README says "the main thread never calls worker methods directly". Two places do:
- `ui/main_window.py:161-163` (`_initial_stream_setup`) calls `engine.configure_signals` /
  `configure_frame` directly after `moveToThread`. It's safe today only because no timer
  is running yet.
- `ui/main_window.py:170` reads `engine.state` from the GUI thread to decide
  `was_running`. Race: click Connect (the `start_working` call is queued), then change
  the stream before the worker has run it. `was_running` is `False`, so no stop/start is
  queued, but `configure_signals` (`engine.py:158`) forces `state = CONFIGURED` while the
  serial timer is running. `_serial_read_step` then returns early forever: the port stays
  open, no data arrives, and the UI still shows "Connected".

Fix: the engine owns its state machine and handles `select_stream(cfg)` atomically
(stop → configure → restart if it was running). The GUI mirrors the state only from
engine signals.

### C6 — Medium: shutdown may not stop the engine before the thread quits
`ui/main_window.py:280-298`: `stop_working` is *queued*, then `quit()` is called right
away, so there's no guarantee the stop slot runs before the event loop exits. The
fallback `QThread.terminate()` can kill the thread while it holds the GIL or a pyserial
handle. Fix: `BlockingQueuedConnection` for stop (or wait for a `stopped` signal), then
`quit()`/`wait()`. Remove `terminate()`.

### C7 — Medium: dead or half-wired features
- The IMU panel buttons do nothing. `MainControlPanel.imu_command_sent`
  (`ui/panels/container.py:63,144`) is never connected in `MainWindow`, and
  `TelemetryEngine.send_imu_command` (`engine.py:111`) only prints a status message. It
  never sends a packet.
- The editor offers `panel_type = "control"` (`stream_editor.py:21`), but `panel_map`
  (`container.py:104`) has no entry for it. The stack is shown with an empty panel.
- `PlotPacketWithRaw["raw"]` is the same object as `["signals"]` and is never read.

### C8 — Medium: PID panel limits and hard-coded command protocol
- `ui/panels/pid.py:129`: every spin box has range `0.0 … 1000.0`. You can't command a
  negative `Rps` (reverse) or negative gains. Precision is fixed at 3 decimals, which is
  too coarse for small `Ki` values.
- The outbound packet layouts (`handler.py:200-285`, IDs `0x10/0x11`) are hard-coded for
  one robot (DiffBot, two motors). The signals carry 10 or 20 positional
  `int/float` arguments (`container.py:31-58`, `engine.py:170-303`), so adding a
  parameter means editing six files. This goes against the "generic plotter, define
  everything in JSON" promise.

### C9 — Medium: serial writes can block acquisition
`core/acquisition/engine.py:78` opens the port with no `write_timeout`. If the device
stops draining its RX buffer (USB-CDC back-pressure, a halted MCU), `serial_port.write`
blocks the worker thread, and with it all acquisition, indefinitely.

### C10 — Low: config path depends on the working directory
`"streams.json"` is opened relative to the CWD in two places (`container.py:78`,
`main_window.py:79`), by two different loaders (`StreamConfigLoader` and
`ConfiguratorTab.load_from_file`), which validate differently. Launching from another
directory, or through the `serial-bin-plotter` entry point, raises `FileNotFoundError`
before the window appears.

### C11 — Low: README drift
The README says `u64/i64/f64` are supported (`README.md:160`), but they're missing from
`STRUCT_TYPE_MAP`. It also says there are no direct cross-thread calls (see C5) and that
`loop_cntr` must be first (not enforced). `packed` is documented in `streams.json` but
ignored.

### C12 — Low: smaller UX issues
- `start_working` while already RUNNING reports "Worker not configured yet"
  (`engine.py:66-68`).
- `TimeConfigPanel` emits on every `valueChanged` (`ui/panels/timing.py:60-61`).
  Typing `100000` reallocates the ring buffers once per keystroke. Use
  `editingFinished`/debounce.
- The status label shows "Connected to X" as soon as the request is *queued*
  (`main_window.py:231`), before the port has opened.

### C13 — Low→High for users: no link diagnostics
Header CRC failures, payload CRC failures, size mismatches, unknown stream IDs, resyncs
and buffer drops are all silent. The only diagnostics are module-level `DEBUG_*` constants.
"Plot is flat" is the most common embedded-debugging complaint, and the app gives the
user nothing to work with. Needed: counters (bytes/s, frames/s per stream, CRC errors,
unknown IDs, drops, loop_cntr gaps) shown in the status bar.

---

## 4. Performance findings

### P1 — High: pyqtgraph downsampling is actually off
`ui/charts/telemetry_plot.py:63` calls `setDownsampling(mode="peak")`. In pyqtgraph 0.14,
`PlotItem.setDownsampling` only changes what you pass. `downsampleCheck` stays unchecked,
so `downsampleMode()` returns `ds=1, auto=False`. Every sample of every visible curve is
drawn each frame. Fix: `setDownsampling(auto=True, mode="peak")`. This is a one-line fix
with the biggest render win.

### P2 — High: the whole buffer is copied every 100 ms, even when nothing changed
`core/acquisition/storage.py:89-109`: each tick fancy-indexes all signals, *including hidden
ones*, into new arrays and computes `nanmin/nanmax` for each. It does this even when no new
sample arrived (device idle, or plot paused). Measured cost: 26 ms and 27 MB per tick at
100k samples × 34 signals. That time is spent holding the GIL, which competes directly
with the serial reader (C1) and the GUI.

Fix: keep a contiguous "double-write" ring (each sample written at `i` and `i + N`), so
the chronological window is always the zero-copy slice `buf[start:start+count]`. Keep a
`version` counter and skip ticks when it hasn't changed. Copy only visible signals, only
in the GUI thread, under a short lock. Compute bounds only for visible signals and the
visible X range.

### P3 — High: no back-pressure between engine and GUI
Each `data_ready` is a queued event that carries its own full copy. If the GUI can't keep
up at 10 Hz, for example with P1 + P4 at 100k samples, events pile up in the GUI event
queue. Memory grows by the packet size (up to 27 MB) per tick, and latency grows without
bound. The plot's throttle (`telemetry_plot.py:176-186`) discards packets only *after*
they've been produced, queued and delivered. Pausing only disconnects the signal. The
engine keeps building packets.

Fix: switch to a pull model. The GUI timer (30-60 FPS) asks the store for "anything newer
than version v?", so there's never more than one snapshot in flight.

### P4 — Medium-High: pen width 2 on every curve
The plot defaults to `width=2` (`telemetry_plot.py:121`), and the editor always saves `2`
(C4c). Qt's raster engine is much slower for pens wider than 1 px (pyqtgraph documents
this). Fix: use 1 px for live curves and allow wider pens only for highlighted curves.
Alternatively, enable `pg.setConfigOptions(segmentedLineMode="on")` or OpenGL, measured
before adopting.

### P5 — Medium: serial reads are polled from a busy thread
The 10 ms `QTimer` polls `in_waiting` on the same thread that runs the 100 ms snapshot
(P2), so a slow snapshot delays reads and feeds C1. A dedicated reader
(`threading.Thread` doing blocking `read(max(1, in_waiting))` with a short timeout, or
`QSerialPort.readyRead`) keeps the OS buffer drained no matter what the GUI or storage
is doing.

### P6 — Low (today): per-frame Python decoding
The per-frame path is `struct.unpack` → `dict` → a per-field dict loop into arrays. It
measures about 67k frames/s, which is plenty for one stream at 1 kHz. If rates or stream
counts grow a lot, batch-decode with `np.frombuffer(chunk, dtype=<structured dtype>)` and
store column-wise. Don't do this before the architecture work (A1/A2).

### P7 — Low: range handling and cursor readout
- In live mode, `setXRange/setYRange` every 200 ms overrides any user zoom or pan. There's
  no "follow X, manual Y" mode.
- Y bounds are computed over the whole buffer, not the visible window.
- `sigMouseMoved` → `TextItem.setHtml` runs on every mouse event with no rate limit. Use
  `pg.SignalProxy(rateLimit=60)`.

### P8 — Low: dead code
`_render_busy` (`telemetry_plot.py:33,179-186`) can never be `True` on entry. The slot
runs on one thread and doesn't re-enter. It adds noise, not protection.

---

## 5. Architecture findings

### A1 — One active stream at a time
`ProtocolHandler._decode_payload` drops every frame whose type isn't the one selected
stream (`handler.py:171`). Real robots multiplex: a 1 kHz control loop, a 100 Hz IMU,
sporadic events and log text. Switching stream in the UI clears history. The target
design decodes **every configured stream at once** into per-stream stores, and the
selector becomes a view choice rather than a decoder switch.

### A2 — `TelemetryEngine` does too much
The engine mixes transport I/O, parsing, storage, GUI cadence, the simulator and
DiffBot-specific command encoding. A cleaner split:

```
Transport (Serial | Virtual | Replay file)  ──bytes──▶  FrameParser (sync/CRC, all stream IDs)
        │                                                   │ (stream_id, payload, rx_time)
        └─ optional RawRecorder (.bin + timestamps)          ▼
                                                    StreamRouter → Decoder per stream (numpy dtype)
                                                            │
                                                            ▼
                                                    SampleStore per stream (ring, lock, version)
                                                            ▲
 GUI thread: PlotController QTimer 30-60 FPS ── pull(visible signals, x-range, since_version)
 Commands: CommandSpec from config → encoder → Transport.write (with timeout)
```

Details are in [ADR-0002](../adr/0002-target-acquisition-pipeline.md).

### A3 — No recording, export or replay
When you disconnect, or the ring wraps, the data is gone. For PID tuning, the key workflow
is "run a step, capture it, compare with the previous gains", and today that's impossible.
Needed: raw-byte recording with host timestamps, replay through the *same* parser
(which also gives deterministic end-to-end tests and replaces most of the hand-written
simulator), and CSV/Parquet export of the current window or a selection.

### A4 — Every signal on one Y axis
PWM commands (hundreds or thousands), RPS (about 1) and errors (about 0.01) share one
auto-ranged axis. Small signals end up flat. The "always include zero" rule
(`telemetry_plot.py:217-221`) makes this worse for offset signals such as `acc_z ≈ 1 g`.
Needed: plot *lanes* (stacked `PlotItem`s with linked X), assigned to signals in config,
each auto-ranging on its own.

### A5 — Config model
Config is `TypedDict`s with ad hoc defaults, read by two different loaders, from a
CWD-relative path, with no schema version. It should be one typed model (dataclasses or
pydantic) with `load() → validate() → save()`, a `schema_version` for migrations, and a
path passed on the CLI or remembered in `QSettings`.

### A6 — The simulator bypasses the protocol
`VirtualDevice` emits Python dicts straight into `store_frame`. It never exercises the
parser, CRC, decoder or config field layout, which is why C3 goes unnoticed. It picks
the waveform by substring-matching the stream *name* (`virtual.py:102-108`). Instead, the
simulator should emit **bytes** built from the stream's frame definition, through the same
transport interface, with a per-field waveform (sine, step, noise, or a small PID plant
model).

---

## 6. Tooling and hygiene

- **T1**: `mypy` is configured with `strict = True`, but reports 40 errors in app code.
  Either fix them or relax deliberately. Right now the config claims a guarantee it
  doesn't deliver.
- **T2**: Tests replace PyQt6 and pyqtgraph with a hand-written stub (`tests/conftest.py`),
  so no real Qt behaviour is tested: threading, signals, queued connections. `pytest-qt`
  is a dependency but unused, and it crashes collection on headless machines without
  `libEGL`. There are no tests for chunk-boundary parsing, noise injection or the 4 KiB
  path, which is how C1 went unnoticed.
- **T3**: No CI.
- **T4**: `streams.json.bak`, an editor backup artifact, is committed.
- **T5**: Polish comments and launch-config names are mixed with English
  (`main_window.py:52,61,72,75`, `widgets.py:88-104`, `.vscode/launch.json`).
  "DiffBot" is hard-coded in the window title and `pyproject.toml` description.
- **T6**: `requires-python = ">=3.14,<3.15"` is narrower than it needs to be. Nothing
  appears to need 3.14 specifically; 3.12+ would widen the install base.

---

## 7. Severity summary

| ID | Severity | Area | One-liner |
|----|----------|------|-----------|
| C1 | Critical | RX | >4 KiB read → whole chunk discarded, silently |
| C2 | High | Time base | time from UI period; reset/wrap breaks plot and cursor |
| C3 | High | Config/data | missing fields plot as 0; no config validation |
| C4 | High | Editor | unsaved edits lost; endianness/width overwritten; key collisions |
| P1 | High | Render | downsampling configured but off |
| P2 | High | Storage | 26 ms / 27 MB copy every 100 ms regardless of change |
| P3 | High | Threading | no back-pressure; queued snapshots accumulate |
| C5 | Medium | Threading | GUI reads/calls engine directly; stream-switch race stalls acquisition |
| C6 | Medium | Lifecycle | stop not guaranteed before quit; `terminate()` |
| C7 | Medium | Features | IMU commands not wired; "control" panel missing |
| C8 | Medium | PID/Cmd | no negative values; hard-coded 20-arg command protocol |
| C9 | Medium | Serial | no write timeout, so writes can block acquisition |
| P4 | Med-High | Render | 2 px pens everywhere |
| P5 | Medium | Serial | polled reads on a busy thread |
| A1–A6 | Structural | Architecture | single stream, god engine, no record/replay, single Y axis, weak config model, simulator bypasses protocol |
| C10–C13, P6–P8, T1–T6 | Low | Various | see above |

---

## Appendix A — benchmark script

This is the script used for §2. It will be committed as `tools/bench_pipeline.py` in
roadmap Phase 0 so it can be re-run after each change.

```python
import json, struct, time
from core.protocol.handler import ProtocolHandler
from core.protocol.crc import calculate_crc8
from core.protocol.constants import STRUCT_TYPE_MAP
from core.acquisition.storage import SignalDataManager

cfg = json.load(open("streams.json"))["streams"]["pid"]
fields = cfg["frame"]["fields"]
fmt = "<" + "".join(STRUCT_TYPE_MAP[f["type"]][0] for f in fields)

def frame(i):
    vals = [i] + [(1 if STRUCT_TYPE_MAP[f["type"]][0] in "bBhHiI" else 1.5) for f in fields[1:]]
    p = struct.pack(fmt, *vals)
    h = bytes([0xAA, 0x55, 1, len(p)])
    return h + bytes([calculate_crc8(h)]) + p + bytes([calculate_crc8(p)])

N = 20000
blob = b"".join(frame(i) for i in range(N))

for chunk in (900, 5000):
    ph = ProtocolHandler(); ph.configure(cfg); n = 0
    for off in range(0, len(blob), chunk):
        ph.add_data(blob[off:off + chunk]); n += sum(1 for _ in ph.process_available_frames())
    print(f"chunk={chunk}: decoded {n}/{N}")

for ms in (2000, 20000, 100000):
    dm = SignalDataManager(ms); dm.configure(cfg["signals"])
    dm._loop_arr[:] = range(ms); dm._count = ms; dm._write_index = 17
    t = time.perf_counter()
    for _ in range(20): pk = dm.get_plot_data(0.005)
    print(ms, f"{(time.perf_counter() - t) / 20 * 1e3:.1f} ms/tick")
```
