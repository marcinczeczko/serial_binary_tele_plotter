# ADR-0012: The scope view: one top bar, edge-tab panes, a bundled instrument font

- Status: Accepted (2026-09-25; decisions 1–5 built in R9.1–R9.3)
- Date: 2026-09-25
- Supersedes: ADR-0008 decisions 1–3 (the session toolbar, the stream-tabs row and
  the docks). ADR-0008's other decisions (no text on the plot, edited vs sent, Live mode, numbered
  sends) stay.
- Spec: [`docs/specs/phase9-scope-view.md`](../specs/phase9-scope-view.md). Design canvas:
  <https://claude.ai/artifact/C7HPkY66MjUYJHUyPfRzPM>.

## Context

ADR-0008 gave the dashboard a toolbar, a stream-tabs row, a status bar and three docks. It works,
but the owner reads the result as overloaded and "AI-generated": always-on counters, labels shown
twice, controls for rare actions, dock chrome, and generic app styling (rounded corners, icons, a
generic UI font). About 60 % of the window is not plot. The panes can't be collapsed quickly.

Bench oscilloscopes (Rigol DHO800, Keysight InfiniiVision, Tek MSO) show a lot of state in little
space with a consistent language: one status line of boxed labels, letters in boxes instead of
icons, square flat controls, readouts in the channel's colour, a dotted graticule.

## Decision

1. **One top bar** of boxed labels replaces the toolbar, the stream-tabs row and the status bar:
   profile, port and Connect, a RUN/STOP box, stream tabs, a transient message, `H` window,
   rate/points, `T` trigger, REC, link health (errors only when non-zero).
2. **Panes, not docks.** The window is a splitter: Signals pane, plot, right pane (Tune / Step, or
   the terminal for a text profile). Always-visible **edge tabs** open and close them; `[`, `]` and
   `\` do the same from the keyboard. Open/closed state and widths are kept per profile in
   `UiState`. Panes can no longer float or move; nobody used that, and it cost title bars.
3. **Square, flat styling and no icons**, in `styles.py`: 1 px borders, flat greys, pure black plot,
   letters in boxes (`H`, `T`, `A`, `B`) where a symbol is needed.
4. **Bundle B612 and B612 Mono** (SIL Open Font License 1.1) under `assets/fonts/`, loaded with
   `QFontDatabase` at start-up, with the licence file next to them. A new runtime asset, no new
   Python dependency.
5. **Numbers are locale-independent**: a dot decimal separator and significant digits only, in
   every input and readout.

## Consequences

- `ui/main_window.py` changes shape (no `QDockWidget`, no `QToolBar`, no `QStatusBar`); the saved
  window state from ADR-0008 (`saveState()`) is dropped once, and the panes start open.
- Tests that find the docks, the toolbar or the status bar change with it.
- The plot gains markers (trigger `T`, cursor flags). They must keep one paint per frame (R3.4);
  `tools/bench_render.py` is run before and after.
- The font files add a few hundred kB to the repository (measured in R9.1).
