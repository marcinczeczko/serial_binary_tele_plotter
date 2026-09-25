# Project log

Newest first. Keep each entry short: what changed, why, measured numbers, and links to
roadmap items (`R*`), findings (`C*/P*/A*/T*`) and ADRs. See
[ADR-0001](adr/0001-record-architecture-decisions.md) for the conventions.

---

## 2026-09-25 — Phase 9 planned: the scope view (design approved)

- Design review of the main view: 19 findings (always-on zero counters, labels shown twice,
  rare actions always visible, dock chrome, generic app styling). Canvas with the current
  window pinned and an interactive proposal: <https://claude.ai/artifact/C7HPkY66MjUYJHUyPfRzPM>.
- Approved direction, modelled on Rigol DHO800 / Keysight InfiniiVision / Tek MSO screens:
  one top bar of boxed labels, square flat controls, letters instead of icons, readouts in
  trace colour, B612 / B612 Mono, and side panes collapsed from always-visible edge tabs.
- `docs/specs/phase9-scope-view.md`, ADR-0012 (proposed; supersedes ADR-0008 decisions 1–3
  once accepted), roadmap Phase 9 (R9.1–R9.6). No code changed.

---

## 2026-09-25 — R8.5 Text commands: the terminal

- A text profile's Controls dock is a **Terminal** (`ui/panels/terminal.py`, ADR-0011
  accepted): Enter sends the line with the profile's ending (LF / CR LF / CR / none, in
  `UiState`), numbered and marked on the plot; Up/Down history; ASCII only.
- Replies: `TextLineDecoder.replies` (a ring of 200 unmatched lines, now on the
  `LinkDecoder` protocol) and `LinkReport.replies` / `replies_dropped` (≤ 100 per ~1 Hz
  report): no per-line signal. VIRTUAL answers `ok: <line>`.
- A text profile's `commands`/`panels` are ignored with a warning. Fixed on the way: the
  Controls dock's first title ignored the shown controls (now `show_controls()`).
- Tests: 429 (+10). Phase 8 (R8.1–R8.5) is complete.

---

## 2026-09-25 — R8.5 spec drafted (for review): a terminal

- First draft (command templates in config) replaced on the owner's call: a text profile
  sends from a **terminal** in the Controls dock (Enter sends, Up/Down history, line
  ending per profile), and shows the board's replies (lines no pattern matches, at most
  100 per ~1 Hz report). No config keys; text profiles' `commands`/`panels` are ignored.
- `docs/specs/r8.5-text-commands.md`, ADR-0011 (proposed); canvas board
  `Dashboard: terminal on a text device`.

---

## 2026-09-25 — R8.4 (part 2): From console output… and Copy as printf

- `core/config/infer_lines.py`: splits lines into numbers and fixed text, groups by
  shape (seen once = not a pattern), names values from `name=`/`name:` else `v1`…,
  types them (u32 / i32 / f32), suggests a steadily rising integer as the X axis (1 ms
  per tick for an `ms` name). Each result is a stream `TextLineDecoder` decodes.
- `ui/config/console_dialog.py`: the paste box, Listen for 5 s (engine
  `listen_lines`/`stop_listening`, lines handed over once via `lines_heard`), a card per
  pattern (tick, name, values), Create N streams. The new streams' Line view shows a
  pasted line. "Copy as printf" (`printf_line`): `%lu`/`%ld`/`%f`, `%%`.
- Spec change: the Line view prefers a stream's pasted line over the newest unmatched
  one, so a stream just made from console output shows its own line.
- Tests: 419 (+14). R8.4 is done; Phase 8's text lines (R8.1–R8.4) are complete.

---

## 2026-09-25 — R8.4 (part 1): the editor on a text profile

- Profile row (all profiles): name, format (read-only), baud, edited into the `profile`
  block. Text profiles: **Pattern** replaces ID and byte order, `LineView` replaces the
  frame view (fixed text + value hexagons, shared `draw_block`), the X axis offers the
  integer values and `(line number)` (Step hidden), the form edits a value.
- `StreamDraft`: `set_pattern` (re-derives fields; kept slots keep type and signals),
  `add_value_after`, `remove_value`, renames edit the slot; `_line` is the default X axis
  of a counterless text stream. An invalid pattern isn't applied (red, status, Esc).
- The Line view's last line comes from the ~1 Hz `LinkReport` (`last_lines`,
  `last_unmatched`), set per chunk by `TextLineDecoder`: no per-line signal.
- Tests: 405 (+19): draft ops, pattern text, last lines, and 10 `qt` editor tests
  including an untouched text profile saving byte-identically (C4). Next: R8.4 part 2
  (From console output…, Copy as printf).

---

## 2026-09-25 — R8.4 canvas revised

- Canvas boards added for R8.4: the text stream editor's states (invalid pattern, no
  match, line-number X axis, no line yet), the dashboard on a text profile, and the
  Listen button's states; `PasteLine` gains an unticked card and an `i32` value.
- Trimmed for a clean look: no source/age on the last line, no hints beyond `✗ no match`,
  no helper text, no progress bar; tooltips instead. The spec records the decisions,
  including where the editor's last line comes from (the ~1 Hz `LinkReport`).

---

## 2026-09-25 — R8.3 Text-line decoder

- **Text profiles (ADR-0010).** `core/protocol/text_line.py`: the pattern grammar
  (`parse_pattern`, `format_line`) and `TextLineDecoder`, registered as `text` in
  `LINK_FORMATS`. Lines are bounded at 1024 B. Unmatched, overlong and unconvertible
  lines are counted in new `LinkStats` counters (C13). Each record carries `_line`, the
  X axis of a stream without a counter.
- Validation is format-aware: a text stream needs a pattern whose slots are its fields
  in order, `loop_cntr` is optional, and `stream_id`/`endianness` are ignored (warning).
  VIRTUAL prints pattern lines plus `# sim tick` once a second. A recording replays as text.
- Spec change: the text counters are in the status bar's link readout (`LinkReport`
  now carries `format`), since the Period/History popup never held link counters.
- Numbers (`bench_pipeline`, 3 interleaved runs each): binary 900 B reads ~155k frames/s
  on main and on the branch (no change); text lines ~45k lines/s with 34 values per line.
  Tests: 386 (+48). With Qt, 384 pass and 1 is skipped; without, 316 pass and 69 are
  skipped. The one failure in both, `test_serial_transport_over_a_real_pty`, also fails
  on main on macOS (`Inappropriate ioctl for device`); CI (Linux) isn't affected.

---

## 2026-09-25 — Handoff: R8.3/R8.4 spec, web session hook

- `docs/specs/phase8-text-lines.md`: the working spec for text-line streams (R8.3
  decoder, R8.4 editor). It covers the config shape, pattern grammar, matching and
  counting, the line-number X axis (`_line`), the simulator, the inference algorithm,
  the canvas screens, acceptance tests and a PR split. The roadmap and CLAUDE.md point
  to it, so a fresh session can pick up R8.3.
- CLAUDE.md gains a "How we work" section: one PR per item, merge on the owner's
  "Merge", spike before UI, interleaved benchmarks.
- `.claude/hooks/session-start.sh` (SessionStart, web only) installs the Qt libraries
  CI installs and runs `uv sync --all-extras`.

---

## 2026-09-25 — R8.2 Device profiles

- **Profiles (ADR-0010).** A config file is a device profile: schema 3 adds an optional
  `profile` block (`name`, `format`, `baud`; `core/config/profile.py`). A schema 2 file
  migrates by its version only and is a binary profile named after the file. The bundled
  `streams.json` is now the `diffbot` profile (binary, 115200).
- **Picker.** The toolbar starts with the profile menu: the profiles folder
  (`~/telemetry-profiles`), the bundled file and files opened from elsewhere, plus New
  profile (name, format, baud; empty or a copy), Open profile file and Edit profile.
  Switching (only while disconnected) reloads the loader, panels, editor and engine
  (`configure_profile` picks the decoder). Port and baud are remembered per profile; a
  profile's baud beats the last baud used with another profile (a test pins it).
- **Recordings** keep the profile's name and format; a replay decodes with the recorded
  format (old recordings are binary), and the next live session uses the profile's.
- Tests: 338 with Qt (+18: profile model, engine formats, the dashboard's switching),
  271 + 67 skipped without;
  one existing test changed on purpose (the baud order above). Ran the whole suite 10+
  times after one unexplained crash in a single run; it didn't recur.

---

## 2026-09-25 — Phase 8 planned (device profiles, text lines); R8.1 decoder slot

- **Plan (ADR-0010, roadmap Phase 8).** From the CSV spike: binary or text is decided by
  the device's firmware, so it lives in a named **device profile** (one JSON file per
  profile: format, baud, streams) picked before connecting. A text stream is a **line
  pattern** (`IMU,{ms},{ax}`, `ENV t={t}C h={h}%`) inferred from pasted console output,
  with no header or delimiter rules. Text commands are a later phase (R8.5).
- **R8.1.** The receive path's byte-to-records step is now a `LinkDecoder`
  (`core/protocol/link.py`); `BinaryFrameDecoder` wraps `FrameParser` + `StreamRouter`
  unchanged, and the engine only holds `engine.link`. No behaviour change.
  `bench_pipeline` now measures that path. Interleaved runs, 4 each at 900 B reads:
  main 124–145k frames/s, R8.1 116–165k (5000 B: 235–251k vs 237–309k): no regression.
  `bench_render` is the same on both (main 29.8/30.0 FPS, R8.1 29.9/29.7, interleaved);
  this container runs it just under the 30 FPS target either way.
- Tests: 320 with Qt (+4: the decoder matches parser + router byte for byte, reset,
  counting, unknown format); 259 + 61 skipped without.

---

## 2026-09-25 — Phase 7: configuration editor in the scope's look, C structs (R7.1–R7.3)

- **Editor (R7.1, ADR-0009).** Rebuilt after a design spike; denser drafts (live values,
  lane previews, hint cards) were rejected as too much information.
  - Streams as tabs, the stream's settings on one row (`× 5 ms` time per tick), the frame
    drawn like a bus decode (32 B per row, a hexagon per field in its signal's color), the
    signals by lane like the Signals dock (plus "Not plotted"), and a form for the field.
  - Drag a field onto a lane to plot it, a signal onto "Not plotted" to remove it. Save
    turns orange with unsaved changes; Revert; Ctrl+S; problems show in the status line.
  - Edits go through a Qt-free `StreamDraft` (`core/config/draft.py`) instead of rebuilding
    the stream from widgets. Found while testing: a label left in the form by the previous
    stream was applied to a newly plotted field; now only typed text is applied.
- **From C struct… (R7.2) and Copy as C struct (R7.3)** (`core/config/cstruct.py`): new
  stream or replace fields; unpacked padding kept as `_pad` fields; long, pointers,
  bit-fields and nested structs listed, not guessed. All bundled streams round-trip.
- Tests: 316 with Qt (+40: model, parser, editor); 255 + 61 skipped without. The old
  editor tests were rewritten for the new widgets, keeping what they check (byte-identical
  save, edits surviving a switch, refused invalid save, time base, lanes, schema 1).
  Plot and data path untouched (no benchmark change).

---

## 2026-09-25 — Dashboard UX follow-up: scrubbing, drag between lanes, Esc (R6.2, R6.4)

The three items ADR-0008 left out:
- **Drag a parameter's label** to change it (`ScrubLabel`): one step per 4 px, Shift ×10,
  Alt ×0.1, measured from where the drag started (dragging back restores the value
  exactly). A linked row stays equal, an unlinked row moves every column. It goes through
  the same path as typing, so edited marks and Live mode apply. Bool rows don't scrub.
- **Drag a signal to another lane** in the Signals dock (`SignalTree`): onto a lane, onto a
  signal of it, or below the list for a new lane. The move is applied after the drag
  loop ends (child timer), since it rebuilds the tree.
- **Esc** in a control panel reverts the values edited since the last send.
- Tests: +3 real-event tests (synthesized mouse moves with modifiers, a key press, and
  `QDropEvent`s). A mutation check confirmed the Esc test fails without the shortcut.
  276 with Qt; 226 + 50 skipped without. The plot and data path are untouched.

---

## 2026-09-25 — Phase 6: dashboard UX, layout concept A (R6.1–R6.5)

- **Layout (R6.1, ADR-0008).** Chosen from a design spike of three concepts, compared with
  a screenshot of the old UI.
  - A session toolbar: connection, Pause (Space), Record with the elapsed time, Trigger
    with its state, and the link statistics.
  - Streams are tabs over the plot, each with its live rate ("200 Hz" / "no data", from
    `SampleStore.latest_time_s` and its counts).
  - Period and history sit behind one button.
  - Signals dock on the left; Controls and Step response docks on the right, tabbed. The
    dock layout is saved in `QSettings`.
  - The stream editor opens in its own window (File → Edit streams.json).
  - `MainControlPanel` keeps its pieces and logic, but no longer lays them out.
- **Signals dock (R6.2).** Signals grouped by lane, with a filter, lane check boxes with
  counts, and a context menu to move a signal to another lane. It is also the legend and
  the cursor readout. The lane `TextItem` readout on the plot is gone: the plot emits
  `readout_changed`.
- **Controls (R6.3, R6.4).**
  - Edited vs last sent: highlight, "N unsent", Revert. Rows can be linked across columns.
  - Live mode: sends 150 ms after edits settle, at most every 100 ms. It's per panel and
    remembered.
  - Presets per panel, and Ctrl+Enter for the main button.
  - Numbered send log with "what changed" and Send again. Each send is a dashed marker on
    the plot, per stream, cleared on a new session.
- **Trigger (R6.5).** Toolbar popup and state. A draggable level line while armed. A capture
  shades the time before the trigger, and the metrics are a table comparing this capture
  with the previous one.
- Benchmarks, interleaved with main in this container:
  - `bench_render`: 30.3 FPS, 21–22 ms paint, ~5.3 ms pull and draw: unchanged. The
    benchmark doesn't hover, so the removed readout text item doesn't show.
  - `bench_pipeline`: 120–170k frames/s at 900 B reads for both: the data path is
    untouched.
- Tests: +6 (273 with Qt; 226 + 47 skipped without). They cover:
  - Live debounce and rate limit, presets and Ctrl+Enter
  - stream activity, the time button, the readout in the Signals dock
  - markers per stream and per session, and Send again
  - the editor window, the restored dock layout, edited vs sent, and linking
  - the trigger's toolbar state and draggable level, and the metrics table

---

## 2026-09-24 — Phase 5: config schema 2, commands and panels in config, remembered UI (R5.1–R5.3)

- **Schema 2 (R5.1, A5, ADR-0007).**
  - `streams.json` has a `schema_version`. `core/config` is a package: document (load →
    migrate → validate → save), streams, controls, migrate.
  - Version 1 files are migrated in memory: `panel_type: "pid"` becomes
    `controls: "diffbot_pid"` plus the PID commands and panel, and `"imu"` is dropped. The
    status bar says so.
  - Saving goes through `save_document`: it's validated, the old file is kept as `.bak`,
    and the new one is written to a temporary file and renamed. The bundled file was
    migrated with the code itself; `tests/fixtures/streams_v1.json` must migrate to it
    exactly.
- **Commands in config (R5.2, C8).**
  - `commands` (packet ID, fields; values from a button, a constant or a panel parameter)
    and `panels` (parameters by column, plus buttons). One generic encoder reuses
    `frame_dtype`, and it refuses values that don't fit instead of wrapping them.
  - `CommandPanel` replaces `PidTuningPanel` and the IMU placeholder. The 10- and
    20-argument signals are gone: the engine gets `send_packet(bytes)`.
  - The simulator decodes commands with the configured layouts, and the motor model
    applies fields by gain name.
  - **Firmware-visible: nothing.** A test pins the bundled `0x10`/`0x11` packets
    byte-for-byte against the old `struct` formats.
- **Remembered UI (R5.3).** `QSettings`, per config file:
  - globally: the port and baud rate
  - per config file: the shown stream, visibility and lane moves (overrides on top of
    streams.json, which View → "Reset view to streams.json" forgets), and panel values
- A broken command or panel only leaves itself out, never the telemetry.
- Benchmarks: the data path is unchanged. `bench_pipeline`: 104k frames/s at 900 B reads,
  207k at 5000 B. `bench_render`: 30.3 FPS, 0 lost.
- Tests: +42 (267 with Qt; 225 + 42 skipped without). They cover:
  - byte compatibility, encoding and range errors, value resolution, decoding
  - migration (fixture, idempotent, user commands kept, newer versions refused)
  - command and panel validation, saving (refused, `.bak`, byte-identical)
  - real Qt: the generated panel sending on VIRTUAL, refused values, state restored in a
    new window plus the reset, a missing port, and saving a version 1 file as version 2

---

## 2026-09-24 — Phase 4: record, replay, export, trigger, step response (R4.1–R4.5)

- **Recording (R4.1, A3, ADR-0006).**
  - `.sbtp` files hold the raw bytes of each transport read with a host timestamp, after a
    JSON header with the stream config. The recorder taps the engine's byte callback on the
    reader thread.
  - Recording menu with Ctrl+R, auto-record on connect, and a folder setting (`QSettings`,
    `ui/app_settings.py`).
  - Cost: 3.3 µs per 900 B read, against ~50 µs to parse and store it.
- **Replay (R4.2).** `ReplayTransport` runs through the same reader, parser and stores at
  1/2/5/10×/max, with pause and a one-read step. It decodes with the current
  `streams.json` and warns if the recorded layouts differ.
  - Fixture `tests/fixtures/pid_sim_300.sbtp` (55 kB): 300 frames, one corrupted. The
    replay pins 299 decoded, 1 CRC error, 1 sequence gap and 1 time gap.
  - Bug fixed before merge: a short replay could finish before the engine had registered
    it, and was then reported as a lost device.
- **Export (R4.3).** The shown stream (the paused view's range, else the buffer) or all
  streams, to CSV, or to Parquet with the new optional `parquet` extra (`pyarrow`; CI
  installs it). Rows that are only gap markers are left out.
- **Trigger and step response (R4.4, R4.5).**
  - `TriggerController` checks every stored sample since arming (`SampleStore.read_since`,
    not the drawn min/max) for a crossing: rising, falling or either, interpolated.
  - It then freezes pre/post seconds in analysis mode, with Δ anchored at the trigger.
  - Rise time, overshoot, settling time (±2 %) and steady-state error are shown next to
    the previous capture's, and the previous capture is overlaid dashed.
  - The pause button now stays enabled, so a view paused when a session ends can be
    resumed.
- **Test-only segfault found and fixed.** pytest-qt closes widgets with `deleteLater()`,
  which `processEvents()` doesn't run. Unreachable windows then waited for garbage
  collection, which could run on a later test's reader or engine thread. That destroyed
  them off the GUI thread and left their timers registered, so a later timer event crashed
  in `QObject::event`.
  - Symptom: only in some test orders with faulthandler. Adding one `np.median` call
    during a session made it appear, because it changes when garbage collection runs.
  - Fix: an autouse fixture flushes deferred deletes and collects garbage on the GUI
    thread after each `qt` test. The plot's `SignalProxy` and its timer are now parented.
- `bench_pipeline.py`: parse+store unchanged (103–116k frames/s at 900 B reads, the same
  as main in interleaved runs; ~200k at 5000 B). `bench_render.py`: 29.3–30.3 FPS, the same
  as main in interleaved runs. This container was busier than in Phase 3, so both
  sometimes land just under the 30 FPS target.
- Tests: +32 (225 with Qt; 188 + 37 skipped without). They cover the file format,
  truncation, pacing/speed/pause/step, record → replay equality, export incl. both Parquet
  branches, trigger/`read_since`/metrics, and real-Qt record-on-connect → replay, export,
  and live VIRTUAL trigger capture with metrics and overlay.

---

## 2026-09-24 — Phase 3: plot lanes, range modes, cursor, render budget (R3.1–R3.4)

- **Lanes (A4, ADR-0005).**
  - `TelemetryPlot` stacks one `PlotItem` per lane, from `signals[*].group` and `groups`.
    It uses a linked X axis and a fixed axis width, and shows only lanes with a visible
    signal.
  - Moving a signal: the Signals panel has a lane selector per row, including "New lane".
    The Configuration tab's Lane column saves it.
  - Lane settings are validated as warnings.
  - The bundled PID streams got Speed / Error / Control / PWM lanes.
- **Range modes (R3.2, P7).**
  - `auto`, `auto-grow` and `manual` per lane. Zero is no longer forced (`include_zero`
    option). A Y drag or zoom switches the lane to manual.
  - Live, X follows the head and the mouse controls Y only. Paused, `auto` fits the
    zoomed X range.
- **Cursor (R3.3).**
  - Readout per lane at 60 Hz (`SignalProxy`). Gap-aware interpolation reads "n/a" next
    to a gap.
  - Paused, a draggable Δ anchor.
  - The live readout is exact, from `SampleStore.values_at`.
- **Render budget (R3.4)**, `tools/bench_render.py`: 34 signals × 100k samples at 1 kHz,
  offscreen software raster. Went from ~2.7 FPS (112 ms tick + 265 ms paint) to **30.1 FPS**,
  0 lost, with 6–7 ms pull + draw and 22 ms paint per frame. Six signals: 30.3 FPS.
  Steps (FPS in the bench):
  - Min/max decimation to about the pixel width: 7.7 FPS.
  - A worker thread for decimation made it *worse* (8.4 FPS; PyQt holds the GIL during
    paint), so it was dropped.
  - An incremental store-side level of detail (`core/acquisition/lod.py`, updated lazily
    on read): live pull 1.1 ms instead of 6.6 ms for a full snapshot. 19 FPS.
  - One paint per frame instead of ~1.6: hidden empty HUD and idle cursor, and the view
    matrix applied before paint. 23 FPS.
  - pyqtgraph downsampling only when paused: 24 FPS.
  - Only restart the frame timer when its interval changes (setInterval restarted it every
    tick: 36.5 ms period). Plus 1000 buckets and a major-ticks-only grid (the full grid
    was 70 ms of paint): 30 FPS.
- `bench_pipeline.py`: parse+store unchanged (113k frames/s at 900 B reads, 211k at 5000
  B), because the level of detail costs the reader thread nothing. Live overview at 100k:
  1.07 ms for 34 signals, 0.24 ms for 6.
- Fixed a Qt crash: hidden lanes' plots were removed from the scene and destroyed later.
  Hidden lanes now stay in the layout.
- Tests (+30, 2 stubbed-Qt plot tests replaced; 193 with Qt, 161 + 32 skipped without):
  - lane layout and range policy
  - decimation, interpolation and bounds
  - the level of detail equals a brute-force reduction for any batching, wrap and
    oversized batch
  - overview, `values_at`, and lane warnings
  - real-Qt lanes, modes, a manual hold, auto-grow, paused fitting, the Δ anchor, lane
    moves, the panel selector, the editor Lane column, and live overview + exact readout

---

## 2026-09-24 — Byte-level simulator (R2.7, A6)

- `VIRTUAL` is now `core/transport/sim_transport.SimTransport`, read by the normal
  `ReaderThread`: simulated bytes go through the parser, router, time base and link
  statistics.
  - It's paced at the stream's own period, handing out bytes at most every 10 ms.
  - It skips frames past a 2000-frame lag, and the time base shows the gap.
  - `select_stream` retargets it without a restart.
- `core/simulation/`:
  - `synth.FrameSynth` packs real frames from the stream definition. An optional `sim`
    block in streams.json gives per-field waves (`sine|step|noise|const|counter`) and a
    model. Fields without a spec get distinct default sines. Bad specs only warn.
  - `pid_motor.PidMotorModel`: FF + PI with anti-windup on a first-order motor. PID
    commands written on VIRTUAL change its gains. The command layouts are now shared
    constants (`PID_SINGLE_FORMAT`, `PID_ALL_FORMAT`); the wire format is unchanged.
- Deleted `core/acquisition/virtual.py` (the name-substring waveform choice). The bundled
  streams.json has `sim` blocks: `pid_motor` for both PID streams, and IMU waves at 1 g on Z.
- Cost: 19–44 µs per `pid` frame, under 1% of a core at 200 Hz. The parse path is
  unchanged, so bench numbers are the same as R2.5.
- Tests (+14; 165 with Qt, 144 + 21 skipped without):
  - every repo stream decodes 10 s of simulated frames cleanly, with varying signals
  - counters wrap (u8, i16 timestamps)
  - wave shapes, integer clipping, and warnings with fallback for bad specs
  - the PID model tracks within 15%, and commands and saturation engage anti-windup
  - pacing, lag skip and retargeting with a fake clock
  - the engine's VIRTUAL path, including commands and the no-valid-stream guard

---

## 2026-09-24 — Per-stream time base (R2.5, C2)

- New `core/acquisition/timebase.py`, and an optional `time: {field, scale_s, step}` per
  stream (validated; stated explicitly in the bundled `streams.json`). See ADR-0003.
- `SampleStore` stores monotonic **ticks**:
  - Integer time fields are unwrapped at their modulus.
  - A backwards jump is a reset: a new segment after a NaN marker, plus a status message
    from the engine.
  - A jump of more than 1.5 × `step` gets a NaN gap marker.
  - `snapshot()` returns `ticks × scale_s`, so a scale change re-times the history.
- GUI:
  - The global Period is gone. The dashboard shows the shown stream's period, and an edit
    is a session override for that stream only (orange). It's applied through
    `engine.set_time_scale`, and buffer size through `engine.set_capacity`.
  - The Configuration tab has Time Base fields and stays lossless: keys are written only
    when present or non-default.
  - `LiveFeed` no longer takes a period.
- Perf: a vectorised time base cost about 40% of parse+store at 900 B reads (about 6
  frames per batch). It's now a Python loop: 2.5 µs per 6-frame batch, about 5%.
  `pid`, best of 3, same machine:
  - 900 B reads: 114–123k → 108–112k frames/s
  - 5000 B reads: 220–226k → 200–207k frames/s
  - Snapshots unchanged: 1.0 ms for 6 visible signals at 100k samples.
- Tests (+24; 151 with Qt, 130 + 21 skipped without): wrap (u32; u8 across batches), reset vs long wrap, gaps across batches,
  timestamp jitter, float fields, the store keeping time sorted across a reset, re-timing,
  config errors and warnings, the engine's reset message, the editor round trip, and a
  real-Qt per-stream override.

---

## 2026-09-24 — Multi-stream decoding + vectorised decoder (R2.2, R2.3)

- New `core/protocol/`:
  - `frame_parser.FrameParser`: sync and CRC for all IDs; memoised header CRC.
  - `record_decoder.RecordDecoder`: packed numpy dtype, `np.frombuffer` per batch.
  - `router.StreamRouter`: routes by ID and payload size; identical layouts decoded once;
    unknown IDs and size mismatches counted; counter gaps tracked per route.
  - `ProtocolHandler` is now the single-stream API plus command encoding, on top of
    `FrameParser`.
- Engine: `configure_streams(all valid streams)`. `StreamStores` holds one `SampleStore`
  per stream. `select_stream(key)` is view-only: no restart, and each stream's history
  survives switching. The GUI re-binds `LiveFeed` on `streams_configured`.
- `SampleStore.append_records`: one C-level cast of the unique fields to float64 (two
  signals may share a field).
- Validation warns when two streams share a `stream_id` with different layouts of the same
  size (ambiguous; the first wins).
- Bench (`pid`, best of 3; before → after):
  - parse+store at 900 B reads: 62–67k → **82–84k frames/s** (per-frame dict path now
    70–72k)
  - at 5000 B reads: **152–159k**; at 50 kB reads: ~183k
  - vs the R0.2 baseline of 66.8k: 1.2× (small reads) to 2.4–2.7× (large reads). The 5×
    target isn't met: per-frame Python framing and CRC remain (see roadmap note).
- Tests (+11, 127 with Qt; 108 + 19 skipped without):
  - decoder vs `struct`, both endiannesses and 64-bit types
  - **the vectorised path equals the per-frame decoder on a noisy 3000-frame stream**, with
    all counters identical
  - size dispatch and shared decode, ambiguous layouts, counter tracking across batches
  - the engine decodes interleaved streams at once
  - switching streams keeps history with no state change (real Qt)

---

## 2026-09-24 — SampleStore + GUI pull model (R2.4, R2.6)

- `core/acquisition/storage.SampleStore` replaces `SignalDataManager`:
  - A column-major matrix (2 × capacity rows) where every row is written twice, so the
    window is always one contiguous slice.
  - It has its own lock and a `version`. `append()` takes a batch as a few slice
    assignments. `snapshot(ids, since_version)` copies only the requested signals and
    returns None when nothing changed.
- The engine no longer pushes anything bulky: `data_ready` and the 100 ms
  `gui_update_timer` are gone (P2, P3).
- `ui/charts/live_feed.LiveFeed` (GUI thread):
  - Pulls the visible signals at up to 30 FPS, only on a new version, and backs off to
    twice the last frame's cost.
  - Pausing freezes one full snapshot, including hidden signals.
  - Visibility and stream changes invalidate the feed.
- Bench (`pid`, 34 signals; same machine, before → after):
  - parse+store 62–67k → **68–73k frames/s**
  - snapshot of all signals @100k: 25 ms → **12–13 ms**
  - what the GUI actually takes (6 visible) @100k: **1.6 ms / 5.6 MB**, down from
    26 ms / 28 MB every 100 ms
  - @20k: 0.4 ms
  - idle tick (no new data): **0.3 µs**
- GUI frame, offscreen software raster (reference only): 6 visible @100k = 20 ms tick +
  86 ms render; 34 visible @100k = 112 + 265 ms. Rendering is now the dominant cost, which
  is R3.4's render budget. With pull plus back-off there's no backlog; the frame rate just
  drops.
- Tests (+11, 116 with Qt; 98 + 18 skipped without):
  - store order across wrap for any batch size, batches larger than capacity,
    requested-only copies, version skip, NaN, resize, clear, and a concurrent
    writer/reader test with no torn snapshots
  - LiveFeed: visible-only pulls, no redraw when idle, invalidation, pause freeze

---

## 2026-09-24 — Phase 2 starts: Transport + reader thread (R2.1)

- ADR-0002 is now **Accepted**.
- New `core/transport/` (no Qt):
  - `Transport` protocol (open/close/read(timeout)/write).
  - `SerialTransport`: pyserial; `read` blocks for the first byte, then drains
    `in_waiting`; 0.2 s write timeout.
  - `ReaderThread`: a blocking read loop; reports a failure once and never on a requested
    stop.
- Engine:
  - The 10 ms `QTimer` polling is gone (P5). The reader thread parses and stores under
    `_data_lock`; snapshots, stats and reconfiguration take the same lock on the engine
    thread.
  - Reader failures go back to the engine thread through a queued signal.
  - The engine enters RUNNING *before* the reader starts, so an instant failure is never
    ignored.
  - Commands go through `Transport.write`. "Not connected" is now reported instead of
    silently ignored.
  - `transport_factory` lets tests inject a `FakeTransport` (`tests/fakes.py`).
- Tests (+13, 107 total):
  - `ReaderThread` order, stop, failure, handler crash and self-stop.
  - `SerialTransport` via mocks, plus **end to end over a real pty**: a 14 kB burst, then
    unplug (EIO → `TransportError`).
  - Engine read, store, disconnect and commands in the stub lane, and in a real `QThread`.
  - `wait_for` pumps Qt events when real Qt is loaded (queued signals need it).
- Measured end to end over a pty (pyserial → reader thread → parse → store, `pid` 146 B
  frames): **52–54k frames/s, 7.6–8.0 MB/s**, 0 CRC errors. That's about 80× what
  921600 baud can deliver. `tools/bench_pipeline.py` numbers are unchanged (same parser
  and storage).

---

## 2026-09-24 — Phase 1, part 3: dead features, config path (R1.9–R1.10). Phase 1 complete

- R1.9 (C7, C8):
  - The IMU panel buttons are disabled with a tooltip explaining that the protocol has no
    IMU command packet. The unused `imu_command_sent` signal and the fake
    `send_imu_command` slot are removed. I deliberately didn't invent a wire format; see
    R5.2.
  - The PID panel uses per-parameter `ParamSpec`s: signed ranges (±1000, `Rps` ±50,
    `Alpha` 0–1), 4–5 decimals and finer steps.
  - Removed the dead `raw` packet key (renamed the type to `PlotPacketWithBounds`) and the
    `_render_busy` guard.
- R1.10 (C10):
  - `resolve_config_path()` picks `--config`, then the last used file (`QSettings`), then
    the bundled `streams.json` next to the code; never the working directory.
  - `main.py` parses `--config`, passing Qt options through, and shows a dialog instead
    of a traceback when the file can't be loaded.
  - One `StreamConfigLoader` is shared by the dashboard and the Configuration tab. The
    loader no longer mutates the raw document (the `panel_type` default is applied to a
    copy).
  - Verified by launching `main.py` from `/tmp` with and without `--config` (offscreen).
- Tests: 94 passed with Qt; 79 passed + 15 skipped without.
- **Phase 1 is done.** Next is Phase 2 (ADR-0002 pipeline), starting with R2.1 `Transport`.

---

## 2026-09-24 — Phase 1, part 2: validation, lossless editor, engine lifecycle (R1.5–R1.8)

- R1.5 (C3, C11):
  - `core/config.validate_config()` checks types, duplicate fields, `loop_cntr`, payload
    ≤ 255 B, `stream_id` range, endianness, and that each signal's field exists. It also
    warns about an unknown panel type and a `loop_cntr` that isn't u32 or isn't first.
  - The loader leaves streams with errors out and keeps `problems`; the main window reports
    them in the status bar (no modal at startup).
  - The editor refuses to save a document with errors.
  - Missing fields are stored as NaN, drawn as gaps (pyqtgraph `connect="auto"`; dropped
    `skipFiniteCheck=True`) and read "n/a" in the cursor readout. Bounds skip all-NaN
    signals.
  - Added u64/i64/f64, so the README's type list is now true.
- R1.6 (C4): the editor is lossless.
  - Each row keeps its original dict in an opaque holder (a plain dict becomes a
    `QVariantMap`, which sorts keys), so unknown keys and their order survive.
  - Endianness is editable. Unknown types and panel types stay visible instead of being
    replaced.
  - New signals get unique keys (`acc_x_2`), and field choices follow frame edits.
  - Edits are committed when switching streams. Rename collisions are refused.
  - Saving keeps the dashboard's selected stream.
  - Loading and saving `streams.json` unchanged is **byte-identical** (test).
- R1.7 (C5, C6, C12):
  - `TelemetryEngine.select_stream()` does stop → configure → restart on the engine thread.
    `state_changed` feeds the GUI's `engine_state` mirror, and the GUI no longer reads
    engine attributes.
  - `configure_signals` no longer demotes RUNNING (that was the stall race).
  - "Connected" appears only once the engine is RUNNING.
  - Close uses a blocking queued stop, then `quit()`/`wait()`. `terminate()` is removed.
  - Time spinboxes have keyboard tracking off, so typing doesn't reallocate buffers on
    every keystroke.
- R1.8 (C9): `write_timeout=0.2 s` on the serial port.
- Bench: unchanged (parse 60–67k frames/s; snapshot 0.9 / 5–6 / 24–26 ms). Tests: 91
  passed with Qt, 77 passed + 14 skipped without; no xfails remain.

---

## 2026-09-24 — Phase 1, part 1: RX data loss, render speed, link stats (R1.1–R1.4)

- R1.1 (C1): removed the pre-parse "clear if > 4 KiB" guard. The parser already bounds the
  buffer to under one max frame (261 B) after each pass; a test asserts it. Discarded
  bytes are now counted. 5000 B reads: **20000/20000 decoded** (was 0). The C1 xfail is removed.
- R1.4 (C13): `core/protocol/stats.py` (`LinkStats`) counts bytes, frames per ID, header
  and payload CRC errors, size mismatches, other-ID frames, sync discards, and `loop_cntr`
  gaps, missing values and resets. The engine emits a `LinkReport` at 1 Hz. The status
  bar shows rates and errors (orange when there's a problem); the tooltip has the full
  breakdown. `ProtocolHandler.reset()` runs on connect, so stale bytes from a previous
  session are dropped.
- R1.2 (P1): `setDownsampling(auto=True, mode="peak")`. A Qt test asserts auto-downsampling
  and clip-to-view on curves.
- R1.3 (P4, C4c): default pen width is 1 px. The editor has a Width column and keeps each
  signal's width. `streams.json`: the 54 widths of 2 (written by the C4c bug) are now 1.
  The width part of C4c is fixed. The remaining editor loss (unknown keys such as `group`)
  stays pinned as a strict xfail for R1.6.
- R0.4 ticked: CI is green on `main` (run 35976740493).
- Bench: parse+store 60–67k frames/s vs 65–67k on `main` (same machine, alternating runs;
  within noise). Snapshot cost is unchanged (P2 is Phase 2).
- Tests: 63 passed, 1 xfailed with Qt; 58 passed, 6 skipped with `-p no:pytest-qt`.

---

## 2026-09-24 — Phase 0: safety net (R0.1–R0.6)

- R0.1: `tests/test_protocol_stream.py` covers random chunking, byte-by-byte feeds, garbage
  and fake magic bytes, header/payload CRC corruption, a split magic pair, wrong length,
  a 255 B payload, interleaved stream IDs and big-endian frames. C1 is pinned with
  `xfail(strict=True)` on reads over 4 KiB.
- R0.2: `tools/bench_pipeline.py` (≈2 s). Baseline for `pid`: 66.9k frames/s, 9.76 MB/s;
  5000 B reads 0/20000 decoded; CRC 3.5 µs; snapshot 0.84 / 6.1 / 25.6 ms per tick at
  2k / 20k / 100k samples (0.6 / 5.6 / 28 MB).
- R0.3: `qt` marker + real-Qt tests (`tests/test_qt_integration.py`): an engine in a real
  `QThread` delivering queued packets to the GUI thread, and a lossless editor round trip
  for `pid`. C4c (line width forced to 2) is pinned as a strict xfail. The `qt` tests skip
  under `-p no:pytest-qt`. `QT_QPA_PLATFORM` defaults to `offscreen`.
- R0.4: `.github/workflows/ci.yml` runs ruff check + format, mypy, pytest and the
  benchmark on ubuntu-24.04 with the Qt system libraries. Ticked once green on `main`.
- R0.5: `uv run mypy .` exits 0. App fixes: None-narrowing, `CollapsableSection.layout` no
  longer shadows `QWidget.layout()`, typed `StreamEditor.get_data`. Tests became a package
  so `[mypy-tests.*]` relaxations apply. Legacy untyped tests aren't body-checked.
- R0.6: removed `streams.json.bak` (`*.bak` is now ignored), translated the Polish
  comments and launch names, and dropped "DiffBot" from the window title, docstrings and
  package description.
- Note: with `target-version = "py314"`, `ruff format` rewrites `except (A, B):` as the
  PEP 758 form `except A, B:` (`core/acquisition/engine.py`). If T6 widens the supported
  Python versions, lowering the ruff target restores the parentheses.
- Also added a real-Qt `MainWindow` smoke test: start, expand the PID section, switch stream, close.
- Results: 49 passed, 4 xfailed with Qt; 46 passed, 4 skipped, 3 xfailed with
  `-p no:pytest-qt`.

---

## 2026-09-24 — Architecture review, roadmap, project records

- Reviewed the codebase at `637f7ba` in full:
  [reviews/2026-09-24-architecture-review.md](reviews/2026-09-24-architecture-review.md).
- Baseline numbers (for later comparison):
  - tests: 22 pass with `-p no:pytest-qt` (Qt stubbed); plain `pytest` crashes headless (no libEGL)
  - mypy strict: 40 errors in app code (125 including tests); ruff: clean
  - parse+store, 140 B `pid` payload: 66.8k frames/s, 9.8 MB/s; CRC-8 3.6 µs/frame
  - 5000 B read chunks: **0/20000 frames decoded** (C1, critical)
  - `get_plot_data` with 34 signals: 0.7 ms @2k, 4.6 ms @20k, 26 ms / 27 MB @100k samples per 100 ms tick
- Top issues: C1 (RX guard discards any read over 4 KiB), P1 (pyqtgraph downsampling
  configured but off), P2/P3 (full-buffer push every 100 ms with no back-pressure), C2
  (time base from UI period; reset breaks time), C3/C4 (silent zeros; editor corrupts or
  loses config).
- Added: `CLAUDE.md`, `docs/roadmap.md` (phases 0–5), ADR-0001 (record keeping),
  ADR-0002 (target pipeline, *Proposed*), and this log.
- Next: roadmap Phase 0 (parser tests reproducing C1, bench script, Qt test lane, CI), then
  R1.1–R1.3.

---

## History before this log (reconstructed from git)

| Date | Milestone |
|---|---|
| 2025-12-27 → 12-29 | First GUI, dummy data, serial detection, threaded worker ("performance best"), crosshair and legend, linter setup |
| 2025-12-30 | Worker and core refactor, UI refactor, docstrings; IMU panel, control-loop stream; in-app configuration editor replaces hand-edited `streams.json` |
| 2026-01-05 → 01-07 | New PID stream definition, two-motor PID panel, "send to both motors" command, K1–K3 gains, first performance pass |
| 2026-02-05 | Performance fixes |
| 2026-03-13 | README, reload validation, virtual-device dt, plot defaults; switched to **uv**, stricter typing, pylint/ruff fixes |
| 2026-03-16 | Package reorganisation into `core/protocol`, `core/acquisition`, `ui/{charts,panels,config,common}`; CRC lookup table, sync by `find()`, worker-side Y bounds, 10 ms serial timer; README and VS Code config for uv |
