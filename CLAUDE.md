# CLAUDE.md

Guidance for Claude (and humans) working in this repository.

## What this is

A PyQt6 + pyqtgraph desktop app. It connects over serial to an embedded board (for
example a robot's motor controllers), decodes binary telemetry frames defined in
`streams.json`, and plots every signal live. From there you can pause, measure with
cursors and Δ, and send PID gains back to the MCU. A `VIRTUAL` port simulates a device
so you can work without hardware.

## Start here

1. `docs/roadmap.md`: the current plan. Work items `R*` have "Done when" criteria.
2. `docs/project-log.md`: newest-first journal of changes and measured numbers.
3. `docs/reviews/2026-09-24-architecture-review.md`: known defects, with stable IDs
   (`C*` correctness, `P*` performance, `A*` architecture, `T*` tooling).
4. `docs/adr/`: design decisions. ADR-0002 is the target pipeline.

Before starting non-trivial work, check whether a roadmap item or finding already covers
it, and reference its ID in commits and PRs.

## Commands

Always go through `uv`. Never run bare `python`/`pytest`.

```bash
uv sync                               # install runtime + dev deps into .venv (Python 3.14)
uv run python main.py [--config PATH] # run the app (default: last used, else bundled streams.json)
uv run pytest                         # all tests; `qt` ones use real Qt (offscreen)
uv run pytest -p no:pytest-qt         # when Qt can't load (no libEGL): `qt` tests skip
uv run ruff check . && uv run ruff format --check .
uv run mypy .                         # strict for app code and tools; must exit 0
uv run python tools/bench_pipeline.py # parser/storage benchmark (C1 guard, snapshot cost)
```

CI (`.github/workflows/ci.yml`) runs exactly these on every PR. Keep them green.

Headless containers (e.g. Claude Code on the web) may lack `libEGL.so.1`. Either install
`libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0t64` with apt (what CI
does), or run with `-p no:pytest-qt`. `tests/conftest.py` defaults `QT_QPA_PLATFORM` to
`offscreen`.

Tests come in two kinds. `qt`-marked tests (`tests/test_qt_integration.py`) use real Qt via
`qtbot`. Older tests use the hand-written PyQt6/pyqtgraph stub (`pyqt_stub` fixture), which
only activates when real PyQt6 isn't already imported. Write new Qt-facing tests as `qt`
tests. Known bugs are pinned with `xfail(strict=True, reason="<finding ID> …")`. When you
fix one, remove its xfail.

## Layout

```
main.py                 QApplication bootstrap, SIGINT handling
styles.py               global dark theme (QSS)
streams.json            stream/frame/signal definitions (single source of truth)
core/types.py           TypedDict config shapes, PlotMode, EngineState
core/config.py          validate_config (single source of truth) + StreamConfigLoader
core/protocol/          wire format: constants, crc (CRC-8), frame_parser (sync/CRC, all IDs), record_decoder (numpy dtype),
                        router (multi-stream dispatch), handler (single-stream API + command encoding), stats
core/transport/         Transport protocol, SerialTransport, SimTransport (the VIRTUAL port), ReaderThread (no Qt)
core/simulation/        synth (frames from a stream's `sim` config), pid_motor (FF + PI motor model); no Qt
core/acquisition/       engine (QThread controller), storage (SampleStore, StreamStores), timebase (per-stream time:
                        wrap/reset/gap -> monotonic ticks; seconds applied at snapshot)
ui/main_window.py       composition, thread setup, signal wiring
ui/charts/              TelemetryPlot (pyqtgraph), LiveFeed (pulls store snapshots)
ui/panels/              connection, stream select, PID, IMU, timing, signal visibility
ui/config/              in-app streams.json editor
tests/                  pytest: pure logic, stubbed-Qt legacy tests, `qt`-marked real-Qt tests
tools/                  dev scripts (bench_pipeline.py)
.github/workflows/      CI
docs/                   records (see "Start here")
```

## Wire protocol (must stay compatible with firmware)

`[0xAA 0x55][TYPE u8][LEN u8][H_CRC8 over 4 header bytes][PAYLOAD LEN bytes][P_CRC8 over payload]`.
CRC-8 uses poly 0x07 and init 0x00. The payload is the packed struct of `frame.fields`,
in order, with the configured endianness. `LEN` ≤ 255. A stream's X axis is its
`time.field` (default `loop_cntr`), unwrapped, times `time.scale_s` (ADR-0003). The MCU's
loop period is config, not a UI knob. Host → MCU PID commands use IDs `0x10` (single motor) and
`0x11` (both), with layouts in `core/protocol/constants.py` (the simulator parses them too).
Changing any of this is a firmware-visible change. Call it out explicitly.

## Architecture rules

- **Threading.** `TelemetryEngine` lives in its own `QThread`. The GUI talks to it only
  through signals or `QMetaObject.invokeMethod(..., QueuedConnection)`. Never call engine
  methods or read engine attributes from GUI code. The engine owns its state machine
  (`configure_streams`, `select_stream`, `start_working`, `stop_working`), and the GUI
  mirrors `state_changed`. Every configured stream is decoded all the time, so
  `select_stream(key)` is a view choice: it only retargets the simulator (`SimTransport`). The GUI
  re-looks-up stores after `streams_configured`.
  Bytes are read and parsed on a `ReaderThread`; parser and storage state are shared with
  the engine thread only under `TelemetryEngine._data_lock`. Keep those sections short.
  Reader failures reach the engine thread through a queued signal, never a direct call.
- **Bulk data doesn't belong in the Qt event queue.** The reader thread appends to the
  shared `SampleStore` (own lock, versioned). The GUI's `LiveFeed` pulls snapshots of the
  *visible* signals at up to 30 FPS, and only when the version changed. Don't add signals
  that carry sample arrays.
- **The protocol and decoding layers (`core/protocol`) must not import Qt.** Keep them
  pure and unit-testable.
- **Config-driven over hard-coded.** New stream or command shapes belong in
  `streams.json` and the config model, not in Python constants.
- **No silent failures in the data path.** Anything dropped (CRC, size mismatch, unknown
  ID, buffer trim) must be counted and reported (C13, R1.4).
- **Performance claims need numbers.** Run `tools/bench_pipeline.py` before and after.
  Record the numbers in `docs/project-log.md`.

## Conventions

- Python 3.14, `from __future__ import annotations`, full type hints, `ruff` (line length
  100; rules E, F, I, B, UP), mypy strict.
- English only in code, comments and UI strings.
- Docstrings describe *why* and contracts. Don't narrate the code.
- Tests go next to the matching area in `tests/test_<area>_*.py`. For every
  parser/storage bug fix, first add a test that reproduces the bug.
- Keep `README.md` in sync with behaviour. Supported types, protocol and config keys are
  user-facing.

## Keeping the records up to date (required)

With every meaningful change, in the same commit:

1. Add a dated entry at the top of `docs/project-log.md` (2–6 lines: what, why, numbers,
   IDs).
2. If the change completes a roadmap item, tick it in `docs/roadmap.md`. If scope
   changed, edit the item and say why in the log.
3. If you make a significant design decision (new dependency, thread/process model, file
   format, protocol change, config schema change), add `docs/adr/NNNN-title.md`. Never
   rewrite an accepted ADR. Supersede it instead.
4. Don't edit past reviews. A new audit goes in a new `docs/reviews/YYYY-MM-DD-*.md`.
5. If architecture, commands or conventions change, update this file.
