# Project log

Newest first. Keep each entry short: what changed, why, measured numbers, and links to
roadmap items (`R*`), findings (`C*/P*/A*/T*`) and ADRs. See
[ADR-0001](adr/0001-record-architecture-decisions.md) for the conventions.

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
