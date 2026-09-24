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
- [ ] **R0.4 CI** (T3). Workflow added; tick once it's green on `main`
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

- [ ] **R1.1 Fix the RX overflow guard** (C1)
  Parse first. Then trim only unsynchronised garbage, capping the buffer at
  `max(64 KiB, 4 × max_frame)` and keeping the tail that could still start a frame. Count
  every discarded byte.
  *Done when:* the R0.1 xfail test passes (xfail removed). 5000 B chunks decode 20000/20000.
- [ ] **R1.2 Enable downsampling for real** (P1)
  `setDownsampling(auto=True, mode="peak")`. Add a Qt-lane test asserting
  `plot.downsampleMode() == (…, True, "peak")`.
- [ ] **R1.3 1 px pens by default** (P4)
  Default `width=1`. The editor keeps the width from the file instead of forcing 2 (C4c).
  Expose width in the editor.
- [ ] **R1.4 Link statistics** (C13)
  `ProtocolHandler.stats`: bytes, frames per stream ID, header-CRC fails, payload-CRC fails,
  size mismatches, unknown IDs, discarded bytes, loop_cntr gaps. The engine emits a stats
  snapshot about once per second, and the status bar shows rates and error counters.
  *Done when:* feeding a corrupted fixture shows non-zero counters in a unit test.
- [ ] **R1.5 Config validation** (C3, C11)
  A single `validate(config) -> list[Problem]` used by the loader and by the editor before
  saving. Checks: known types, unique field names and signal keys, `signal.field` present
  in the frame, time field present, payload ≤ 255 B, `stream_id` 0–255, known
  `panel_type`. Missing values are stored as `NaN`, not `0.0`.
  Either add `u64/i64/f64` to `STRUCT_TYPE_MAP` or remove them from the README.
- [ ] **R1.6 Config editor data-safety** (C4)
  Commit the current stream on selection change. Round-trip every key the editor doesn't
  own (endianness, packed, width, unknown keys). Generate a unique signal key that doesn't
  depend on the field. Refresh the field combos when frame fields change. After save, keep
  the currently active stream selected. Add a round-trip test: load → save without edits
  → byte-identical JSON (modulo formatting).
- [ ] **R1.7 Engine owns stream switching and lifecycle** (C5, C6, C12)
  Add an engine slot `select_stream(cfg)` that does stop → configure → restart-if-running
  on the worker thread. The GUI stops reading `engine.state` and mirrors state from a new
  `state_changed(EngineState)` signal. "Connected" is shown only after the port actually
  opens. Shutdown uses `BlockingQueuedConnection` stop, then `quit()`/`wait()`, and
  `terminate()` is removed. Time-config input is debounced (`editingFinished`).
- [ ] **R1.8 Serial write timeout** (C9)
  Set `write_timeout=0.2`. On timeout, report it and keep acquiring.
- [ ] **R1.9 Wire or hide dead features** (C7, C8)
  Either wire the IMU commands to a real packet or hide the panel. Add a `control` panel
  entry or remove it from `PANEL_TYPES`. Allow negative PID values with a configurable
  range and precision. Drop the unused `raw` packet key and `_render_busy`.
- [ ] **R1.10 Config path** (C10)
  Resolve `streams.json` via a `--config` CLI arg, then `QSettings`-remembered path, then
  the bundled default. One loader instance is shared by the panel and the configurator.

## Phase 2: acquisition pipeline re-architecture (A1, A2, A6, P2, P3, P5, C2)

This implements [ADR-0002](adr/0002-target-acquisition-pipeline.md). Do it as a sequence of
PRs that each keep the app working. Measure with `tools/bench_pipeline.py` before and after.

- [ ] **R2.1 `Transport` interface**
  `open/close/read(timeout)->bytes/write(bytes)`, implemented by `SerialTransport`
  (pyserial, write timeout) and later by `ReplayTransport` and `SimTransport`. Reads run on
  a dedicated reader thread doing blocking reads (P5). No `QTimer` polling.
- [ ] **R2.2 Multi-stream `FrameParser` + `StreamRouter`** (A1)
  The parser yields `(stream_id, payload, host_rx_time)` for *all* IDs. The router
  dispatches to one decoder per configured stream. Unknown IDs are counted, not dropped
  silently.
- [ ] **R2.3 Vectorised decoder**
  A numpy structured dtype per stream, built from `frame.fields` + endianness. Payloads are
  decoded in batches (`np.frombuffer` over concatenated payloads) straight into the store.
  Target: 5× or better throughput vs. the R0.2 baseline.
- [ ] **R2.4 `SampleStore` per stream** (P2, P3)
  A double-write ring buffer, so the chronological window is always a contiguous zero-copy
  slice. It has a monotonic `version`, a short `threading.Lock` around write and read, and
  per-stream `time` computed at write time. Snapshots copy only requested signals and the
  requested X range.
- [ ] **R2.5 Time base** (C2)
  Per-stream config `time: {field: "loop_cntr", scale_s: 0.001}` (or a µs timestamp field).
  u32 unwrap, reset detection (new segment + status message), and NaN gap insertion when
  Δcounter is more than k × nominal. The UI "Period" control becomes a per-stream override
  that is saved to config, not a global runtime knob.
- [ ] **R2.6 GUI pull model** (P3)
  A `PlotController` `QTimer` at 30–60 FPS calls `store.snapshot(visible, since_version)`,
  which returns `None` if nothing changed. Nothing crosses threads except small control
  signals and stats, so the queued-packet backlog goes away entirely.
- [ ] **R2.7 Byte-level simulator** (A6)
  `SimTransport` generates *bytes* from any stream definition: per-field waveform spec
  (sine/step/noise/const/counter), plus an optional built-in DC-motor + PI plant for
  `pid` streams. Delete the name-substring logic.
  *Done when:* every stream in `streams.json` shows non-zero, plausible data on VIRTUAL,
  and the path exercises the parser end to end.

## Phase 3: visualisation for analysis (A4, P7)

- [ ] **R3.1 Plot lanes**: `signals[*].lane` (or a `lanes` list per stream). Stacked
  `PlotItem`s with linked X, each with its own Y auto-range. Drag a signal between lanes
  in the UI.
- [ ] **R3.2 Range modes** per lane: `auto` (visible X window only), `auto-grow`, `manual`.
  In live mode X follows the head, but the user can zoom or pan Y without it being reset.
  Drop the forced "include zero" rule, or make it a per-lane option.
- [ ] **R3.3 Cursor/HUD**: `pg.SignalProxy(rateLimit=60)`, readout per lane, two-cursor
  Δ measurement, and a readout that stays correct across segments and gaps.
- [ ] **R3.4 Render budget check**: with the bench fixture (34 signals × 100k samples ×
  1 kHz), the GUI holds ≥ 30 FPS and the reader drops 0 bytes. Record the numbers in the
  log.

## Phase 4: record, replay, analyse (A3)

- [ ] **R4.1 Raw recorder**: append-only `.sbtp` file with a small header (config snapshot
  + schema version), then `[host_ts_ns, len, bytes]` chunks. Start/stop from the UI,
  optionally recording automatically on connect.
- [ ] **R4.2 Replay transport**: open a recording, play it at 1×/N×/max speed or step
  through it, through the same pipeline. Use recordings as regression fixtures in tests.
- [ ] **R4.3 Export**: current window or selection → CSV / Parquet (optional dependency),
  one file per stream, time column first.
- [ ] **R4.4 Trigger capture**: oscilloscope-style trigger (signal crosses level,
  rising/falling, pre/post samples) that freezes a capture around a setpoint step.
- [ ] **R4.5 Step-response metrics** (optional): rise time, overshoot, settling time,
  steady-state error for a chosen setpoint/measurement pair on a captured step. Overlay
  runs from before and after a gain change.

## Phase 5: generic commands and config model (A5, C8)

- [ ] **R5.1 Typed config model**: dataclasses (or pydantic) with `schema_version`,
  `load/validate/save`, and migration from the current format. The editor binds to the
  model, not to raw dicts.
- [ ] **R5.2 Command definitions in config**: `commands: {name, packet_id, fields[], ui:
  {min,max,step,decimals,default}}`. A generic parameter panel is generated from them,
  and a generic encoder reuses the frame dtype code. The DiffBot PID panel becomes one
  config entry. The 20-argument signals are removed.
- [ ] **R5.3 Persist UI state**: last port, baud, stream, lane layout, visibility and PID
  values via `QSettings` or per-config sidecar.

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
