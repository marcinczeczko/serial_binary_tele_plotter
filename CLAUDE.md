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
uv run python main.py                 # run the app (CWD must be the repo root, see C10)
uv run pytest                         # tests (needs libEGL for pytest-qt)
uv run pytest -p no:pytest-qt         # tests on headless machines without libEGL
uv run ruff check . && uv run ruff format --check .
uv run mypy core ui main.py           # strict; currently 40 known errors (roadmap R0.5)
```

Headless containers (e.g. Claude Code on the web) have no display and no `libEGL.so.1`.
Qt GUI imports fail there, so use `-p no:pytest-qt` and `QT_QPA_PLATFORM=offscreen`. The
existing tests stub PyQt6 and pyqtgraph in `tests/conftest.py`, so they run anyway.

## Layout

```
main.py                 QApplication bootstrap, SIGINT handling
styles.py               global dark theme (QSS)
streams.json            stream/frame/signal definitions (single source of truth)
core/types.py           TypedDict config shapes, PlotMode, EngineState
core/config.py          StreamConfigLoader (load + minimal validation)
core/protocol/          wire format: constants, crc (CRC-8 poly 0x07), decoder (struct), handler (sync/CRC/encode)
core/acquisition/       engine (QThread controller), storage (numpy ring), virtual (simulator)
ui/main_window.py       composition, thread setup, signal wiring
ui/charts/              TelemetryPlot (pyqtgraph)
ui/panels/              connection, stream select, PID, IMU, timing, signal visibility
ui/config/              in-app streams.json editor
tests/                  pytest; Qt stubbed via conftest.pyqt_stub fixture
docs/                   records (see "Start here")
```

## Wire protocol (must stay compatible with firmware)

`[0xAA 0x55][TYPE u8][LEN u8][H_CRC8 over 4 header bytes][PAYLOAD LEN bytes][P_CRC8 over payload]`.
CRC-8 uses poly 0x07 and init 0x00. The payload is the packed struct of `frame.fields`,
in order, with the configured endianness. `LEN` ≤ 255. Frame X axis today is
`loop_cntr × UI period` (C2). Host → MCU PID commands use IDs `0x10` (single motor) and
`0x11` (both), with layouts hard-coded in `core/protocol/handler.py`.
Changing any of this is a firmware-visible change. Call it out explicitly.

## Architecture rules

- **Threading.** `TelemetryEngine` lives in its own `QThread`. The GUI talks to it only
  through signals or `QMetaObject.invokeMethod(..., QueuedConnection)`. Never call engine
  methods or read engine attributes from GUI code. Existing violations are tracked as C5.
- **Bulk data doesn't belong in the Qt event queue.** Today the engine pushes snapshots
  (P2/P3). The target design (ADR-0002) has the GUI pull from a versioned store. New code
  should move in that direction, not add more push paths.
- **The protocol and decoding layers (`core/protocol`) must not import Qt.** Keep them
  pure and unit-testable.
- **Config-driven over hard-coded.** New stream or command shapes belong in
  `streams.json` and the config model, not in Python constants.
- **No silent failures in the data path.** Anything dropped (CRC, size mismatch, unknown
  ID, buffer trim) must be counted and reported (C13, R1.4).
- **Performance claims need numbers.** Use `tools/bench_pipeline.py` (added in R0.2;
  until then, the script in the review's Appendix A) before and after. Record the numbers
  in `docs/project-log.md`.

## Conventions

- Python 3.14, `from __future__ import annotations`, full type hints, `ruff` (line length
  100; rules E, F, I, B, UP), mypy strict.
- English only in code, comments and UI strings. Some Polish remains; translate it when
  you touch it.
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
