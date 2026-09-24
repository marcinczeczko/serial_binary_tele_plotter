# Project log

Newest first. Keep each entry short: what changed, why, measured numbers, and links to
roadmap items (`R*`), findings (`C*/P*/A*/T*`) and ADRs. See
[ADR-0001](adr/0001-record-architecture-decisions.md) for the conventions.

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
