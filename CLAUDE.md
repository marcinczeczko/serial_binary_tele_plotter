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
5. `docs/specs/`: working specs for the next roadmap items. **Phase 9 is in progress (R9.1–R9.6):
   the scope view, `docs/specs/phase9-scope-view.md` (ADR-0012, proposed). R9.1–R9.4 done; next R9.5.**

Before starting non-trivial work, check whether a roadmap item or finding already covers
it, and reference its ID in commits and PRs.

## Commands

Always go through `uv`. Never run bare `python`/`pytest`.

```bash
uv sync --all-extras                  # runtime + dev deps + optional pyarrow into .venv (Python 3.14)
uv run python main.py [--config PATH] # run the app (default: last used, else bundled streams.json)
uv run pytest                         # all tests; `qt` ones use real Qt (offscreen)
uv run pytest -p no:pytest-qt         # when Qt can't load (no libEGL): `qt` tests skip
uv run ruff check . && uv run ruff format --check .
uv run mypy .                         # strict for app code and tools; must exit 0
uv run python tools/bench_pipeline.py # parser/storage benchmark (C1 guard, snapshot cost)
uv run python tools/bench_render.py   # GUI render budget (R3.4): 34 signals x 100k @ 1 kHz, >= 30 FPS
```

CI (`.github/workflows/ci.yml`) runs exactly these on every PR. Keep them green. The two benchmarks
are informational there (`bench_render` may not reach 30 FPS on a shared runner).

On Claude Code on the web, `.claude/hooks/session-start.sh` (a SessionStart hook) installs
those libraries and runs `uv sync --all-extras`, so the commands above work at once.

Other headless containers may lack `libEGL.so.1`. Either install
`libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0t64` with apt (what CI
does), or run with `-p no:pytest-qt`. `tests/conftest.py` defaults `QT_QPA_PLATFORM` to
`offscreen`.

Tests come in two kinds. `qt`-marked tests (`tests/test_qt_integration.py`) use real Qt via
`qtbot`. Older tests use the hand-written PyQt6/pyqtgraph stub (`pyqt_stub` fixture), which
only activates when real PyQt6 isn't already imported. Write new Qt-facing tests as `qt`
tests. Register every widget with `qtbot.addWidget` and give every other QObject a parent.
The autouse `collect_qt_garbage` fixture then destroys a finished test's objects on the GUI
thread. A Python-owned QObject freed by garbage collection on a reader/engine thread crashes
some later test in `QObject::event`. Known bugs are pinned with `xfail(strict=True, reason="<finding ID> …")`. When you
fix one, remove its xfail.

## Layout

```
main.py                 QApplication bootstrap, SIGINT handling
styles.py               the scope look (ADR-0012): square flat QSS, colour tokens, bundled B612 / B612 Mono
                        loaded at start-up (`load_fonts`, `mono_font`)
assets/fonts/           B612 and B612 Mono TTFs with OFL.txt (SIL OFL 1.1)
streams.json            schema 3: the bundled `diffbot` device profile: profile, streams, commands, panels
                        (single source of truth; ADR-0007, ADR-0010)
core/types.py           TypedDict config shapes, PlotMode, EngineState
core/config/            document (load -> migrate -> validate -> save, StreamConfigLoader), streams (stream
                        validation), controls (CommandDef/PanelDef parsing), migrate (schema versions),
                        draft (StreamDraft: the editor's model), cstruct (C struct in and out), profile
                        (the `profile` block: name, format, baud; listing a profiles folder), infer_lines
                        (line patterns from console output, R8.4)
core/protocol/          wire format: link (LinkDecoder: bytes -> records per stream; the engine's only view of the
                        format, ADR-0010), constants, crc (CRC-8), frame_parser (sync/CRC, all IDs), record_decoder
                        (numpy dtype), router (multi-stream dispatch), handler (single-stream API), commands, stats,
                        text_line (text profiles: pattern grammar, TextLineDecoder, `_line` X axis; R8.3)
core/transport/         Transport protocol, SerialTransport, SimTransport (the VIRTUAL port), ReplayTransport
                        (plays an .sbtp file), ReaderThread (no Qt)
core/recording/         .sbtp raw recordings (ADR-0006): RecordingWriter/Reader; no Qt
core/analysis/          export (CSV/Parquet), trigger detection, step-response metrics; no Qt
core/simulation/        synth (frames from a stream's `sim` config), pid_motor (FF + PI motor model); no Qt
core/acquisition/       engine (QThread controller), storage (SampleStore, StreamStores), timebase (per-stream time:
                        wrap/reset/gap -> monotonic ticks; seconds applied at snapshot), lod (min/max level of
                        detail for live frames, summarised lazily on read)
ui/main_window.py       composition (the top bar (R9.2), a splitter of Signals pane | plot | right pane (Tune or
                        Step) with edge tabs and `[` `]` `\` (R9.3), editor window), thread setup, signal
                        wiring, menus; `_say` puts a message in the bar (ADR-0012)
ui/panes.py             PaneState (open panes, right view, widths; kept per profile) and EdgeTab (R9.3)
ui/app_settings.py      QSettings keys (config path, recording options, profiles folder, recent profiles)
ui/ui_state.py          remembered port and baud, stream, view overrides, panel values, presets, Live mode
                        (per config file, i.e. per device profile), the panes (per profile) and the window geometry
ui/charts/              TelemetryPlot (lanes = signals[*].group, per-lane Y modes, cursor/Δ), LiveFeed (pulls the
                        store's overview), lanes.py + series.py (Qt-free layout, range, decimation, readout),
                        trigger_controller (arms on the shown store, emits captures)
ui/panels/              container (owns the controls; MainWindow places them), top_bar (the 36 px row: PartsButton
                        with boxed letters, RunBox, MessageLabel, RatePoints, LinkHealth), connection (profile
                        menu, port (refreshes on open), baud (serial only), Connect, RUN/STOP), profile_dialog (New profile),
                        stream_tabs, signals (the Signals pane: one row per L/R pair from signal_rows,
                        swatch toggles, readout at A in colour, filter on Ctrl+F, drag rows between lanes), command_panel (generated from `panels`: edited vs sent, linked rows, Live,
                        presets, label scrubbing, Esc revert), command_log, terminal (a text profile's
                        input line and replies, R8.5),
                        timing, trigger (setup popup + step-response results)
ui/config/              streams.json editor (ADR-0009): tab (toolbar, profile row, stream tabs, save),
                        stream_editor (settings row, lanes tree, field form), frame_view (bus-decode drawing),
                        line_view (a text stream's pattern and last line, R8.4), paste_dialog,
                        console_dialog (From console output…: paste or listen, infer streams)
ui/common/              color_button, numbers (format_number, ScopeDoubleSpinBox: dot decimal, significant
                        digits, whatever the locale; use it for every float input)
tests/                  pytest: pure logic, stubbed-Qt legacy tests, `qt`-marked real-Qt tests
tools/                  dev scripts (bench_pipeline.py)
.github/workflows/      CI
.claude/                settings (read denies for secrets) and the web SessionStart hook
docs/                   records and specs (see "Start here")
```

## Wire protocol (must stay compatible with firmware)

`[0xAA 0x55][TYPE u8][LEN u8][H_CRC8 over 4 header bytes][PAYLOAD LEN bytes][P_CRC8 over payload]`.
CRC-8 uses poly 0x07 and init 0x00. The payload is the packed struct of `frame.fields`,
in order, with the configured endianness. `LEN` ≤ 255. A stream's X axis is its
`time.field` (default `loop_cntr`), unwrapped, times `time.scale_s` (ADR-0003). The MCU's
loop period is config, not a UI knob. Host → MCU commands are framed the same way; their IDs and
layouts are `commands` in `streams.json` (ADR-0007), encoded by `core/protocol/commands.py` and
decoded by the simulator. The bundled PID panel sends `0x10` (single motor) and `0x11` (both);
a test pins them byte-for-byte. Changing any of this is a firmware-visible change. Call it out explicitly. `.sbtp` recordings
hold these bytes verbatim (ADR-0006), so a wire change also affects replaying old recordings.

## Architecture rules

- **Threading.** `TelemetryEngine` lives in its own `QThread`. The GUI talks to it only
  through signals or `QMetaObject.invokeMethod(..., QueuedConnection)`. Never call engine
  methods or read engine attributes from GUI code. The engine owns its state machine
  (`configure_streams`, `select_stream`, `start_working`, `stop_working`), and the GUI
  mirrors `state_changed`. Every configured stream is decoded all the time, so
  `select_stream(key)` is a view choice: it only retargets the simulator (`SimTransport`). The GUI
  re-looks-up stores after `streams_configured`.
  Bytes are read and decoded (`engine.link`, a `LinkDecoder`) on a `ReaderThread`; decoder
  and storage state are shared with
  the engine thread only under `TelemetryEngine._data_lock`. Keep those sections short.
  Reader failures reach the engine thread through a queued signal, never a direct call.
- **Bulk data doesn't belong in the Qt event queue.** The reader thread appends to the
  shared `SampleStore` (own lock, versioned). The GUI's `LiveFeed` pulls
  `store.overview()` of the *visible* signals at up to 30 FPS, and only when the version
  changed. That's min/max buckets, about 1000 per signal (ADR-0005). Exact values come from
  `store.values_at()`, and pause uses a full `snapshot()`. Don't add signals that carry
  sample arrays.
- **Render budget.** Keep one paint per live frame. pyqtgraph items that change their
  transform or geometry inside `paint()` (a visible `TextItem`, a deferred view matrix)
  schedule a second paint. So nothing is drawn as text on the plot: the readout is shown in
  the Signals pane (`readout_changed`), and overlays are lines and regions only (markers,
  trigger level, capture shading). Check changes to the plot with `tools/bench_render.py`
  and record the numbers.
- **The protocol and decoding layers (`core/protocol`) must not import Qt.** Keep them
  pure and unit-testable.
- **The config editor edits a `StreamDraft`, never its widgets' contents.** Each edit is an
  operation on the draft that touches only its keys; line edits apply what was typed, on
  leaving or on a save/switch (ADR-0009). Keep new editor features on that path, so an
  untouched stream still saves byte-identically (C4).
- **A config file is a device profile (ADR-0010).** Its `profile.format` picks the engine's
  `LinkDecoder` (`configure_profile`), switched only while disconnected; everything keyed
  by config file (`UiState`, the editor, recordings) follows the profile. The window
  switches profiles with `switch_profile(path)`, which reloads the loader, panels, editor
  and engine; don't add another path that changes the loaded file.
- **Config-driven over hard-coded.** New stream or command shapes belong in
  `streams.json` and the config model, not in Python constants. A command's panel is a
  `panels` entry; the GUI encodes a press and hands the engine a finished packet
  (`send_packet(bytes)`). A change to the file format needs a `schema_version` bump, a
  step in `core/config/migrate.py` and a test that the previous version still loads.
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

## How we work

- **One PR per roadmap item** (or a small, related group), on the session's branch, with
  the item ID in the title. The PR body lists what changed, the numbers and the checks run.
- **The owner merges by saying "Merge".** Never merge on your own. On "Merge": squash-merge,
  then restart the branch from the new `main` for the next item.
- **Spike before building UI.** Design new screens as a canvas first and iterate with the
  owner. They want the oscilloscope look, little text, and nothing "AI-bloated".
- **Before pushing:** ruff, format, mypy and the full `uv run pytest` (with real Qt) all
  clean. For a performance-sensitive change, run the benchmark on `main` and on the
  branch, **interleaved** (a worktree helps). A single run on a shared container is noisy.
- **Qt tests:** see "Commands" (`qtbot.addWidget`, parents for every QObject). If a crash
  happens once, rerun the suite many times before calling it a flake. Record it in the
  log either way.

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
