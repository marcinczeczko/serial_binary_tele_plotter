# Roadmap: fix, improve, refactor

Source: [2026-09-24 architecture review](reviews/2026-09-24-architecture-review.md). Finding
IDs (C*, P*, A*, T*) point there. Target design: [ADR-0002](adr/0002-target-acquisition-pipeline.md).

How to use this file:
- Each work item has an ID (`R<phase>.<n>`), the findings it closes, and a "Done when" test.
- Tick the box in the same commit that finishes the item, and add a line to
  [`project-log.md`](project-log.md).
- Phases are ordered by risk. Phase 1 fixes user-visible data loss with small, local diffs
  and should land before any restructuring. Phase 2 is the only big refactor. Phases 3–5
  build on it.
- Scope changes are fine. Edit this file and record the reason in the log.

---

## Phase 0: safety net (do first, small)

These let every later change be checked and measured.

- [x] **R0.1 Real parser tests** (T2, C1)
  Property-style tests for `ProtocolHandler`: random chunk sizes (1 B … 16 KiB), random
  garbage between frames, corrupted header/payload CRC, frames split across the magic
  bytes, max-length (255 B) payloads, several stream IDs interleaved.
  *Done when:* the test for chunks over 4 KiB **fails on current code** (reproduces C1)
  and is marked `xfail(strict=True)` until R1.1 lands.
- [x] **R0.2 Pipeline benchmark in repo** (P2, P6)
  Move the review's Appendix A script to `tools/bench_pipeline.py`. Print parse
  throughput, snapshot cost for 2k/20k/100k × N signals, and bytes allocated per tick.
  *Done when:* `uv run python tools/bench_pipeline.py` runs in under 10 s and the baseline
  numbers are recorded in the project log.
- [x] **R0.3 Real-Qt test lane** (T2)
  Add a `qt` marker. Tests using real Qt run under `pytest-qt` with
  `QT_QPA_PLATFORM=offscreen`. Pure-logic tests keep running without Qt. Document
  `-p no:pytest-qt` for machines without `libEGL`.
  *Done when:* one real `QThread` + queued-signal engine test passes in CI.
- [x] **R0.4 CI** (T3)
  GitHub Actions on ubuntu: `uv sync`, `ruff check`, `ruff format --check`, `mypy`,
  `pytest` (with `libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3` installed).
  *Done when:* CI is green on `main`.
- [x] **R0.5 mypy honesty** (T1)
  Fix the 40 app-code errors. Most are PyQt6 `| None` returns and `CollapsableSection.layout`
  shadowing `QWidget.layout()`. Put tests under a relaxed `[mypy-tests.*]` section.
  *Done when:* `uv run mypy .` exits 0.
- [x] **R0.6 Hygiene** (T4, T5)
  Remove `streams.json.bak` from git and add `*.bak` to `.gitignore`. Translate the Polish
  comments and launch names. Drop "DiffBot" from generic strings (window title,
  `pyproject` description).

## Phase 1: stop data loss and fix correctness (small, local diffs)

No architectural change. Each item is a focused PR.

- [x] **R1.1 Fix the RX overflow guard** (C1)
  Parse first. Then trim only unsynchronised garbage, capping the buffer at
  `max(64 KiB, 4 × max_frame)` and keeping the tail that could still start a frame. Count
  every discarded byte.
  *Scope note:* no explicit cap was needed. The parser already guarantees the buffer holds
  less than one max-size frame (261 B) after each pass, and a test asserts that.
  *Done when:* the R0.1 xfail test passes (xfail removed). 5000 B chunks decode 20000/20000.
- [x] **R1.2 Enable downsampling for real** (P1)
  `setDownsampling(auto=True, mode="peak")`. Add a Qt-lane test asserting
  `plot.downsampleMode() == (…, True, "peak")`.
- [x] **R1.3 1 px pens by default** (P4)
  Default `width=1`. The editor keeps the width from the file instead of forcing 2 (C4c).
  Expose width in the editor.
- [x] **R1.4 Link statistics** (C13)
  `ProtocolHandler.stats`: bytes, frames per stream ID, header-CRC fails, payload-CRC fails,
  size mismatches, unknown IDs, discarded bytes, loop_cntr gaps. The engine emits a stats
  snapshot about once per second, and the status bar shows rates and error counters.
  *Done when:* feeding a corrupted fixture shows non-zero counters in a unit test.
- [x] **R1.5 Config validation** (C3, C11)
  A single `validate(config) -> list[Problem]` used by the loader and by the editor before
  saving. Checks: known types, unique field names and signal keys, `signal.field` present
  in the frame, time field present, payload ≤ 255 B, `stream_id` 0–255, known
  `panel_type`. Missing values are stored as `NaN`, not `0.0`.
  Either add `u64/i64/f64` to `STRUCT_TYPE_MAP` or remove them from the README.
- [x] **R1.6 Config editor data-safety** (C4)
  Commit the current stream on selection change. Round-trip every key the editor doesn't
  own (endianness, packed, width, unknown keys). Generate a unique signal key that doesn't
  depend on the field. Refresh the field combos when frame fields change. After save, keep
  the currently active stream selected. Add a round-trip test: load → save without edits
  → byte-identical JSON (modulo formatting).
- [x] **R1.7 Engine owns stream switching and lifecycle** (C5, C6, C12)
  Add an engine slot `select_stream(cfg)` that does stop → configure → restart-if-running
  on the worker thread. The GUI stops reading `engine.state` and mirrors state from a new
  `state_changed(EngineState)` signal. "Connected" is shown only after the port actually
  opens. Shutdown uses `BlockingQueuedConnection` stop, then `quit()`/`wait()`, and
  `terminate()` is removed. Time-config input is debounced (`editingFinished`).
- [x] **R1.8 Serial write timeout** (C9)
  Set `write_timeout=0.2`. On timeout, report it and keep acquiring.
- [x] **R1.9 Wire or hide dead features** (C7, C8)
  Either wire the IMU commands to a real packet or hide the panel. Add a `control` panel
  entry or remove it from `PANEL_TYPES`. Allow negative PID values with a configurable
  range and precision. Drop the unused `raw` packet key and `_render_busy`.
  *Partly done in R1.5:* `PANEL_TYPES` now lives in `core/config.py` as `none/pid/imu`.
  The editor no longer offers `control`, and validation warns about unknown panel types.
  *Decision:* the IMU buttons are **disabled with an explanatory tooltip**, not wired. The
  protocol has no IMU command packet, and inventing one would be a firmware-visible
  change. Commands come from config in R5.2. PID ranges and precision are per-parameter
  `ParamSpec`s in `ui/panels/pid.py` until R5.2 moves them into config.
- [x] **R1.10 Config path** (C10)
  Resolve `streams.json` via a `--config` CLI arg, then `QSettings`-remembered path, then
  the bundled default. One loader instance is shared by the panel and the configurator.

## Phase 2: acquisition pipeline re-architecture (A1, A2, A6, P2, P3, P5, C2)

This implements [ADR-0002](adr/0002-target-acquisition-pipeline.md). Do it as a sequence of
PRs that each keep the app working. Measure with `tools/bench_pipeline.py` before and after.

- [x] **R2.1 `Transport` interface**
  `open/close/read(timeout)->bytes/write(bytes)`, implemented by `SerialTransport`
  (pyserial, write timeout) and later by `ReplayTransport` and `SimTransport`. Reads run on
  a dedicated reader thread doing blocking reads (P5). No `QTimer` polling.
- [x] **R2.2 Multi-stream `FrameParser` + `StreamRouter`** (A1)
  The parser yields `(stream_id, payload, host_rx_time)` for *all* IDs. The router
  dispatches to one decoder per configured stream. Unknown IDs are counted, not dropped
  silently.
  *Scope notes:*
  - Frames carry no host timestamp yet; that arrives with recording (R4.1).
  - Streams sharing a `stream_id` are routed by payload size. Identical layouts are decoded
    once; an ambiguous same-size layout is a validation warning.
  - Stream selection in the GUI is now a view change: no engine restart, and history is
    kept per stream (`StreamStores`).
- [x] **R2.3 Vectorised decoder**
  A numpy structured dtype per stream, built from `frame.fields` + endianness. Payloads are
  decoded in batches (`np.frombuffer` over concatenated payloads) straight into the store.
  Target: 5× or better throughput vs. the R0.2 baseline.
  *Outcome:* 2.3–2.4× at 5000 B reads (66.8k → 152–159k frames/s) and 1.2× at 900 B reads
  (82–84k). Batches grow with read size, and the reader drains everything buffered, so
  heavier load gets bigger batches. The target isn't met because the rest is per-frame
  Python framing and payload CRC. Beating it would need a compiled CRC and framing loop
  (a C extension or numba, i.e. a new dependency). That isn't justified: this is about 20×
  what 921600 baud can deliver.
- [x] **R2.4 `SampleStore` per stream** (P2, P3)
  A double-write ring buffer, so the chronological window is always a contiguous zero-copy
  slice. It has a monotonic `version`, a short `threading.Lock` around write and read, and
  per-stream `time` computed at write time. Snapshots copy only requested signals and the
  requested X range.
  *Scope note:* the store is a column-major matrix (batch writes are a few slice
  assignments) with its own lock. Two parts are deferred. Time is still computed at
  snapshot time as `loop_cntr × period`; that moves to write time with R2.5. X-range
  clipping of snapshots comes with zoomable lanes in Phase 3, because live view always
  shows the whole window. One store per stream comes with R2.2 (multi-stream).
- [x] **R2.5 Time base** (C2)
  Per-stream config `time: {field: "loop_cntr", scale_s: 0.001}` (or a µs timestamp field).
  u32 unwrap, reset detection (new segment + status message), and NaN gap insertion when
  Δcounter is more than k × nominal. The UI "Period" control becomes a per-stream override
  that is saved to config, not a global runtime knob.
  *Done (ADR-0003):*
  - `time: {field, scale_s, step}`, with k = 1.5.
  - Wrap is unwrapped for any integer width. A reset starts a new segment after a NaN
    marker, with one status message.
  - Ticks are stored at write time; `scale_s` is applied at snapshot time, so a scale
    correction re-times the history consistently.
  - *Scope change:* the dashboard Period is a per-stream **session** override. It's saved
    through the Configuration tab's new Time Base fields, not written to the file directly,
    because the tab keeps its own unsaved copy of the document and two writers would race.
- [x] **R2.6 GUI pull model** (P3)
  A `PlotController` `QTimer` at 30–60 FPS calls `store.snapshot(visible, since_version)`,
  which returns `None` if nothing changed. Nothing crosses threads except small control
  signals and stats, so the queued-packet backlog goes away entirely.
- [x] **R2.7 Byte-level simulator** (A6)
  `SimTransport` generates *bytes* from any stream definition: per-field waveform spec
  (sine/step/noise/const/counter), plus an optional built-in DC-motor + PI plant for
  `pid` streams. Delete the name-substring logic.
  *Done when:* every stream in `streams.json` shows non-zero, plausible data on VIRTUAL,
  and the path exercises the parser end to end.
  *Done (ADR-0004):*
  - The `sim` block (per-field `wave` specs, `model: "pid_motor"`) is only ever a warning.
  - VIRTUAL is a `SimTransport` on the normal `ReaderThread`. It's paced at the stream's
    own period and applies PID commands to the model.
  - A parametrised test decodes 10 s of every repo stream with no errors and checks each
    signal is finite and varies. `*_aw_term` is exempt: it's zero until the output
    saturates, which has its own test.
  - `VirtualDevice` is deleted.

## Phase 3: visualisation for analysis (A4, P7)

- [x] **R3.1 Plot lanes**: `signals[*].lane` (or a `lanes` list per stream). Stacked
  `PlotItem`s with linked X, each with its own Y auto-range. Drag a signal between lanes
  in the UI.
  *Done (ADR-0005):* lanes reuse the keys streams.json already had: `signals[*].group`
  and `groups.<id>` (`label`, `order`, `y_range`). A lane shows while one of its signals is
  visible. *Scope change:* a lane selector per signal row (with "New lane") replaces
  drag-and-drop; the Configuration tab's Lane column saves it. The bundled PID streams got
  Speed / Error / Control / PWM lanes.
- [x] **R3.2 Range modes** per lane: `auto` (visible X window only), `auto-grow`, `manual`.
  In live mode X follows the head, but the user can zoom or pan Y without it being reset.
  Drop the forced "include zero" rule, or make it a per-lane option.
  *Done:* all three modes, set from config or the lane's context menu, with
  `include_zero` as an option (default off). Dragging or zooming Y switches the lane to
  manual. Paused `auto` fits only the zoomed X range.
- [x] **R3.3 Cursor/HUD**: `pg.SignalProxy(rateLimit=60)`, readout per lane, two-cursor
  Δ measurement, and a readout that stays correct across segments and gaps.
  *Done:*
  - A readout per lane; the top lane also shows T and Δt.
  - Paused, a click drops a Δ anchor that can be dragged (anchor + cursor are the two
    cursors). Every lane's anchor moves together.
  - Gap-aware interpolation reads "n/a" next to a gap.
  - The live readout is exact (`SampleStore.values_at`).
- [x] **R3.4 Render budget check**: with the bench fixture (34 signals × 100k samples ×
  1 kHz), the GUI holds ≥ 30 FPS and the reader drops 0 bytes. Record the numbers in the
  log.
  *Done:* `tools/bench_render.py` passes at **30.1 FPS**, 0 lost (offscreen software
  raster; was ~2.7 FPS). The fix was an incremental min/max level of detail in
  `SampleStore`, plus one paint per frame instead of ~1.6 (see ADR-0005 and the log).

## Phase 4: record, replay, analyse (A3)

- [x] **R4.1 Raw recorder**: append-only `.sbtp` file with a small header (config snapshot
  + schema version), then `[host_ts_ns, len, bytes]` chunks. Start/stop from the UI,
  optionally recording automatically on connect.
  *Done (ADR-0006): Recording menu (Ctrl+R), auto-record on connect and folder in
  `QSettings`; flushed each second; write errors stop the recording, not the session.*
- [x] **R4.2 Replay transport**: open a recording, play it at 1×/N×/max speed or step
  through it, through the same pipeline. Use recordings as regression fixtures in tests.
  *Done: `ReplayTransport` (1/2/5/10×/max, pause, step one read). It decodes with the
  current `streams.json` and warns if the recorded layouts differ.
  `tests/fixtures/pid_sim_300.sbtp` pins decoded count, CRC error and gaps.*
- [x] **R4.3 Export**: current window or selection → CSV / Parquet (optional dependency),
  one file per stream, time column first.
  *Done: "Export shown stream" (the paused view's range, else the buffer) and "Export all
  streams" (whole buffers). Parquet via the `parquet` extra (`pyarrow`).*
- [x] **R4.4 Trigger capture**: oscilloscope-style trigger (signal crosses level,
  rising/falling, pre/post samples) that freezes a capture around a setpoint step.
  *Done: rising/falling/either, pre/post in seconds, single shot. It checks every stored
  sample (not the drawn min/max). The capture opens paused, with Δ anchored at the
  interpolated crossing.*
- [x] **R4.5 Step-response metrics** (optional): rise time, overshoot, settling time,
  steady-state error for a chosen setpoint/measurement pair on a captured step. Overlay
  runs from before and after a gain change.
  *Done: computed per capture and listed next to the previous capture's, whose traces
  are overlaid (dashed, aligned at the trigger). Only one previous run is kept.*

## Phase 5: generic commands and config model (A5, C8)

- [x] **R5.1 Typed config model**: dataclasses (or pydantic) with `schema_version`,
  `load/validate/save`, and migration from the current format. The editor binds to the
  model, not to raw dicts.
  *Done (ADR-0007): `core/config` package with load → migrate → validate → save
  (`save_document`: validated, `.bak`, atomic write) and `schema_version: 2`. Version 1
  files are migrated in memory. Scope change: streams stay plain JSON objects
  (`StreamConfig`), because the editor round-trips them losslessly (C4). Commands and
  panels are the dataclasses, and the editor saves through the document model.*
- [x] **R5.2 Command definitions in config**: `commands: {name, packet_id, fields[], ui:
  {min,max,step,decimals,default}}`. A generic parameter panel is generated from them,
  and a generic encoder reuses the frame dtype code. The DiffBot PID panel becomes one
  config entry. The 20-argument signals are removed.
  *Done, with the UI split out: `commands` hold the packet layout (field values from a
  constant, a button or a panel parameter), and `panels` hold parameters by column plus
  buttons. A stream picks one with `controls`. Packets are byte-identical to the old
  hard-coded ones. The engine takes finished packets (`send_packet(bytes)`). The IMU
  placeholder is gone: it's a config entry now if firmware defines the packet.*
- [x] **R5.3 Persist UI state**: last port, baud, stream, lane layout, visibility and PID
  values via `QSettings` or per-config sidecar.
  *Done: `QSettings`, per config file. Visibility and lanes are overrides on top of
  streams.json, and View → "Reset view to streams.json" forgets them.*

## Phase 6: dashboard UX (layout concept A, ADR-0008)

Added after the UX spike (2026-09-25): the sidebar held everything in one column, the most
used control (the signal list) got two rows, and the control panel didn't show what the
device had last received.

- [x] **R6.1 Toolbar and stream tabs**: port, baud, Connect, Pause, Record and Trigger in a
  toolbar, with the link statistics. Streams as tabs above the plot, each with its live
  rate or "no data". Period and history behind one button. Docks can be moved, floated
  and closed, and their layout is remembered. The stream editor opens in its own window.
  *Done.*
- [x] **R6.2 Signals dock**: signals grouped by lane, with a filter, lane check boxes and
  counts. It is also the legend and the cursor readout (value, Δ). The readout text on the
  plot is removed.
  *Done: lane moves by drag and drop (onto a lane, onto a signal of it, or below the list
  for a new lane) or the context menu.*
- [x] **R6.3 Controls dock**: the stream's panel with edited vs last sent (highlight,
  count, Revert) and rows linked across columns. A numbered send log with "what changed"
  and "Send again", and a dashed marker on the plot per send.
  *Done.*
- [x] **R6.4 Live mode and presets**: Live sends a column after its values settle (150 ms
  debounce, at most every 100 ms). Named presets per panel. Ctrl+Enter presses the main
  button.
  *Done. Scope: A/B is two presets plus the trigger's overlay, not a separate toggle.
  Dragging a number parameter's label changes it (Shift ×10, Alt ×0.1); Esc reverts
  unsent values (2026-09-25 follow-up).*
- [x] **R6.5 Trigger on the plot**: the Trigger toolbar button shows its state and opens the
  setup. While armed, the level is a draggable dashed line. A capture shades the time
  before the trigger, and the Step response dock compares metrics with the previous
  capture in a table.
  *Done.*

## Phase 7: configuration editor (ADR-0009)

Added after the editor spike (2026-09-25): fields and signals were two lists joined by a
drop-down, the byte layout wasn't visible, the editor didn't look like the scope, and a
stream couldn't be started from the firmware's struct.

- [x] **R7.1 Editor in the scope's look**: stream tabs, one row of stream settings, the
  frame drawn like a bus decode, the signals by lane (with the fields not plotted), and a
  form for the selected field. Edits go through a Qt-free `StreamDraft`, so an untouched
  stream saves byte-identically and switching streams can't lose an edit.
  *Done when:* the C4 round-trip and save tests pass on the new editor, and plotting,
  renaming, moving between lanes and removing work from both the frame and the tree.
  *Done.*
- [x] **R7.2 From C struct…**: paste a struct (or its member lines) and get a stream with
  every field in place, labels = field names; or replace the fields of the shown stream,
  keeping the settings of fields that stay. Padding of an unpacked struct is kept as
  `_pad` fields; what can't be read is listed, never guessed.
  *Done when:* a pasted struct saves as a valid stream, and a replacement keeps the
  signals of unchanged fields.
  *Done.*
- [x] **R7.3 Copy as C struct**: any stream as a packed struct with its ID and a size
  check.
  *Done when:* every bundled stream round-trips through it.
  *Done.*

## Phase 8: device profiles and text-line streams (ADR-0010)

Added after the CSV spike (2026-09-25): many boards print text lines (CSV or any other
layout) instead of binary frames. Binary or text is a property of the device, so the
choice lives in a named **device profile** picked before connecting, and each text stream
is a **line pattern** inferred from pasted console output.

**R8.3 and R8.4 are done.** Both were specified in detail (config shape, pattern grammar,
matching, inference, editor screens, acceptance tests) in
[`docs/specs/phase8-text-lines.md`](specs/phase8-text-lines.md).

- [x] **R8.1 Decoder slot**: the receive path's byte-to-records step becomes a
  `LinkDecoder` interface. Today's `FrameParser` + `StreamRouter` sit behind it as
  `BinaryFrameDecoder`; the engine only knows the interface.
  *Done when:* no behaviour change (every test passes unchanged in what it checks), and
  `bench_pipeline` shows no regression against main in interleaved runs.
  *Done.*
- [x] **R8.2 Device profiles**: a top-level `profile` block (name, format, default baud)
  in a schema 3 file, one file per profile in a profiles folder, a profile picker first
  in the dashboard toolbar (switch only while disconnected), New profile (name, format,
  baud, empty or a copy), and baud remembered per profile. A schema 2 file loads as a
  binary profile named after the file.
  *Done when:* two profiles can be switched between, each keeps its own streams, port
  and baud, and a recording replays with its profile's format.
  *Done. The profiles folder defaults to `~/telemetry-profiles`; the menu also lists the
  bundled `streams.json` (now the `diffbot` profile) and files opened from elsewhere.
  A profile's baud beats the last baud used with another profile.*
- [x] **R8.3 Text-line decoder**: `TextLineDecoder` splits lines (bounded length) and
  matches each against the streams' patterns (`IMU,{ms},{ax}`: fixed text plus number
  slots; `nan`, `inf` and exponents accepted; an empty slot is a gap). Lines matching no
  pattern, and overlong lines, are counted in the link statistics. Validation for text
  profiles (a pattern per stream, its slots are the fields, `loop_cntr` optional: without
  a counter the X axis is the line number). VIRTUAL prints lines for text profiles.
  *Done when:* a text profile plots from VIRTUAL and from a recording, and every
  dropped line is counted.
  *Done. The text counters are in the status bar's link readout (where the binary ones
  are), not in the Period/History popup the spec named. About 45k lines/s with 34 values
  per line.*
- [x] **R8.4 Editor for text profiles**: the profile row (name, format, baud), a
  **Pattern** field and the Line view (fixed text and value blocks) instead of ID and
  byte order, "From console output…" (paste raw lines or listen for a few seconds;
  infers patterns, names values from the text next to them or `v1`…, suggests a rising
  integer as the X axis, ignores lines seen once), and "Copy as printf".
  *Done when:* pasting mixed console output creates one stream per repeated line layout
  that decodes those lines.
  *Done, in two PRs: the profile row, Pattern, X axis, Line view and value form; then
  "From console output…" (paste or listen, `core/config/infer_lines.py`) and "Copy as
  printf".*
- [x] **R8.5 Text commands**: a terminal for text profiles: type a line, Enter sends it
  with the profile's line ending; the board's replies (lines no pattern matches) are
  shown. (Replaces the earlier idea of command templates in config.)
  *Done when:* a text profile sends a typed line from VIRTUAL, the send is numbered and
  marked on the plot, and the reply is shown. Spec:
  [`docs/specs/r8.5-text-commands.md`](specs/r8.5-text-commands.md), ADR-0011.
  *Done.*

---

## Phase 9: the scope view (ADR-0012)

Added 2026-09-25 after a design review of the main view: the plot reads like a scope, the
window around it doesn't. Phase 9 adopts a bench scope's visual language (one status bar of
boxed labels, square flat controls, letters instead of icons, readouts in trace colour, an
instrument font) and makes both side panes collapsible from edge tabs. Approved on the canvas
(<https://claude.ai/artifact/C7HPkY66MjUYJHUyPfRzPM>); the spec is
[`docs/specs/phase9-scope-view.md`](specs/phase9-scope-view.md), with open questions to settle
in each item's PR.

- [x] **R9.1 Look**: square, flat QSS (no rounded corners, flat greys, black plot), B612 and
  B612 Mono bundled (OFL) and loaded at start-up, the scope palette in the bundled
  `streams.json`, numbers with a dot separator and significant digits whatever the locale.
  *Done when:* no widget has rounded corners, every number shows `0.12` not `0,1200`, and the
  app starts with B612 on a machine without it installed.
- [x] **R9.2 Top bar**: one 36 px bar replaces the toolbar, the stream-tabs row and the status
  bar: profile, port + Connect/Disconnect (baud only for serial, no ⟳), RUN/STOP box, stream
  tabs with a data square, transient message, `H` window, rate/points, `T` trigger label, REC,
  link health (errors only when non-zero).
  *Done when:* every piece of state the three rows showed is in the bar or its tooltips, and
  the window has no `QToolBar` or `QStatusBar`.
- [x] **R9.3 Panes and edge tabs**: a splitter with the Signals pane, the plot and the right
  pane (Tune / Step, or the terminal); always-visible edge tabs `SIGNALS`, `TUNE`, `STEP`;
  keys `[`, `]`, `\`; open state, view and widths kept per profile.
  *Done when:* each pane opens and closes from its tab and its key, `\` gives a plot-only
  window, and the state comes back after a restart and a profile switch.
- [x] **R9.4 Signals pane**: one row per L/R pair, swatches as toggles (R swatch and R trace
  dashed), no counts, filter on Ctrl+F, the stopped readout in trace colour with an A/B/ΔT
  header.
  *Done when:* the bundled profile's 34 signals show as 17 rows, and a cursor readout shows
  each shown signal's value at A in its colour.
- [x] **R9.5 Tune and Step panes**: Manual/Live segmented, `Presets ▼` menu, one `L=R` toggle,
  square inputs without spin arrows, `Send` per column, `Revert N` only when edited, the send
  log as plain lines (double-click resends); Step metrics as Now / Prev / change.
  *Done when:* the pane has no always-visible disabled button, and every ADR-0008 behaviour
  (edited vs sent, Live, presets, numbered sends, resend) still works.
- [x] **R9.6 Graticule and markers**: framed lanes, 10 dotted divisions, ticked centre
  crosshair, the X unit on the last tick (no `Time [s]`), orange `T` markers for trigger level
  and position, `A`/`B` cursor flags.
  *Done when:* the markers show as designed and `bench_render.py` stays at one paint per frame
  and ≥ 30 FPS, interleaved against `main`.

---

## Suggested order and sizing

| Order | Items | Size | Why this order |
|---|---|---|---|
| 1 | R0.1–R0.4 | S | Can't safely refactor without parser tests, a benchmark and CI |
| 2 | R1.1, R1.2, R1.3 | XS each | Biggest user-visible wins (data loss, render speed) for a few lines each |
| 3 | R1.4–R1.8 | S each | Correctness and diagnostics |
| 4 | R0.5, R0.6, R1.9, R1.10 | S | Clean-up before the big move |
| 5 | R2.1 → R2.7 | L | Core refactor, one PR per item |
| 6 | R3.* | M | Needs the R2.4 store API |
| 7 | R4.* | M | Needs the R2.1 transport interface |
| 8 | R5.* | M | Can run in parallel with R3/R4 once R1.5 exists |
| 9 | R6.* | M | UX, once the config model (R5) defines the panels |
| 10 | R7.* | M | The editor, once the dashboard (R6) sets the look |
| 11 | R8.1 → R8.4 | M | Decoder slot first (no behaviour change), then profiles, then text |
| 12 | R9.1 → R9.6 | M | Look first (everything inherits it), then the frame (panes, top bar), then the pieces |
