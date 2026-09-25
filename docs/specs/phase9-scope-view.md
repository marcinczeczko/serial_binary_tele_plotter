# Spec: the scope view (Phase 9, R9.1–R9.6)

- Status: approved by the owner (2026-09-25), not built.
- Decision record: [ADR-0012](../adr/0012-scope-view-layout.md) (proposed).
- Design canvas: <https://claude.ai/artifact/C7HPkY66MjUYJHUyPfRzPM> ("Scope view redesign").
  - **Now**: a screenshot of the current window with the 19 findings pinned.
  - **Proposed v2** (interactive; the font can be switched in its Tweaks), **Focus**, **Stopped**,
    **Top strip states** and **Typeface candidates**.
- References: Rigol DHO800 Quick Guide §4.4 "User Interface Overview"; Keysight InfiniiVision 3000 X
  User's Guide; Tektronix 4/5/6 Series MSO manual (Settings bar and badges).

## Why

The plot already reads like a scope. The window around it doesn't: it's about 60 % text and
controls, and the owner reads it as "AI-generated". The causes are always-on counters (mostly zeros),
things labelled twice, controls for rare actions shown all the time, and generic app styling
(rounded corners, pill buttons, line icons, a generic UI font). Bench scopes (Rigol, Keysight, Tek)
solve the same problem with a fixed visual language; Phase 9 adopts it.

## The visual language (applies to everything below)

- **Square and flat.** No rounded corners anywhere, no pills, no shadows, no gradients. 1 px borders,
  flat greys: panels `#0d0d0d`, bars `#141414`, borders `#333` / `#262626`, buttons `#222` with a
  `#484848` border. The plot area is pure black.
- **No icons.** Letters in small filled boxes, as scopes do: `H` (timebase), `T` (trigger, orange
  when armed), `A` / `B` (cursors). Everything else is a word.
- **Font: B612** for UI text and **B612 Mono** for numbers (Airbus/ENAC cockpit-display face, SIL OFL,
  bundled with the app). Candidates compared on the canvas: Share Tech Mono, Barlow Semi Condensed.
- **Colour means something.** Traces are the brightest thing on screen. Chrome stays grey. Green =
  running / alive, red = stopped / recording / fault, orange = trigger, amber `#FFB000` = edited /
  needs attention. A readout is written in its trace's colour.
- **Scope palette on black** for the bundled `diffbot` profile: Measurement yellow `#FFE81A`,
  Setpoint cyan `#00E5FF`, Target light grey `#C8C8C8`, Error magenta `#FF4FD8`, U_FF blue `#3D8BFF`,
  U_PI green `#3DFF6E`, Output / PWM red `#FF5A5A`, trigger orange `#FF9A1A`. (Colours stay config.)
- **Left solid, right dashed**, same hue, for the two motors' copies of a signal.
- Numbers use a dot decimal separator whatever the system locale, and show significant digits only
  (`0.12`, `26.5`, not `0,1200`, `26,5000`).

## Layout

```
┌ top bar (36 px) ───────────────────────────────────────────────────────────────────────┐
│ diffbot ▼ │ VIRTUAL [Disconnect] │ [RUN] │ ▪PID Telemetry ▪PID FF ▫IMU raw │ … msg … │
│                                   │ [H] 10.0 s │ 200 Hz / 2.00k pts │ [T]▪ ╱ 0.200 ARMED │ ▫REC │ 29 kB/s │
├─┬──────────────┬──────────────────────────────────────────────────────┬──────────────┬─┤
│S│ Signals pane │  graticule lanes (plot)                               │ Tune / Step  │T│
│I│  (238 px)    │                                                       │  (296 px)    │U│
│G│              │                                                       │              │N│
│ │              │                                                       │              │E│
│ │              │                                                       │              ├─┤
│ │              │                                                       │              │S│
└─┴──────────────┴──────────────────────────────────────────────────────┴──────────────┴─┘
```

No status bar. No dock title bars.

### Top bar (R9.2)

One 36 px bar replaces the toolbar, the stream-tabs row and the status bar. Left to right, each
item a box separated by 1 px dividers:

1. **Profile**: `diffbot ▼` (the format moves into the menu).
2. **Port**: the port name, then `Disconnect` (grey) or `Connect` (lit: light fill, black text).
   The baud combo shows only for a serial port. No ⟳: the port list refreshes when it opens.
3. **Run state**: `RUN` green text in a green-outlined box, or `STOP` white on a solid red box.
   Click or Space toggles (today's Pause). Disconnected: a dim `—`.
4. **Stream tabs**: the stream names, the shown one white with a 2 px underline. A 6 px square before
   each: green when frames arrive, hollow when not. The rate moves to the tooltip.
5. *(flex)* A **transient message** (was the status bar): grey text for a few seconds, e.g.
   `Sent PID L+R`. Errors that need action stay until the next message.
6. **Window**: `[H] 10.0 s`, the history shown (period × samples). Click opens today's time popup;
   the period and sample count live there.
7. **Rate / points**: two small lines, `200 Hz` over `2.00k pts`, like a scope's sample-rate and
   memory-depth label.
8. **Trigger**: `[T] —` when idle; armed: orange `[T]`, the source's colour square, the edge glyph
   and the level (`╱ 0.200`), then `ARMED`. Click opens today's trigger setup.
9. **REC**: a hollow square dot and `REC`; recording: red dot and `REC 02:14`.
10. **Link health**: `29 kB/s` dim. When anything was dropped: `CRC 3  LOST 12` in black on an amber
    block. The full breakdown stays in the tooltip.

### Panes and edge tabs (R9.3)

- The docks go. The window is a horizontal splitter: Signals pane, plot, right pane.
- **Edge tabs are always visible**, 22 px wide, vertical text: `SIGNALS` on the left edge,
  `TUNE` and `STEP` stacked on the right edge. An open pane's tab is lit (grey fill, white text, a
  2 px white edge on the pane side).
  - Clicking a lit tab closes its pane. Clicking an unlit right tab opens the right pane on that
    view (switching between Tune and Step).
- Keys: `[` toggles Signals, `]` toggles the right pane, `\` toggles both (focus mode).
- Open/closed, the right pane's view and the pane widths are remembered per profile (`UiState`),
  replacing the saved dock state.
- A text profile's right tab reads `TERMINAL` (R8.5's terminal) and there is no `STEP` tab.
- The Step pane still comes forward on a trigger capture.

### Signals pane (R9.4)

- A header row `Signals` with `L` and `R` column labels. No `26 of 34 shown`, no per-lane `6/8`.
- Lane headers in small caps (`SPEED rps`) followed by a hairline.
- **One row per signal pair**: the name, then an L swatch and an R swatch (12 px squares). The swatch
  *is* the toggle: filled = shown, hollow grey = hidden. The R swatch has a gap in the middle to match
  the dashed trace. A signal without a pair has one swatch.
- The filter field appears on Ctrl+F (or when typing in the pane) and hides again when empty.
- When stopped with cursors: a boxed header `A 0.420s  B 0.760s  ΔT 0.340s`, and each shown
  swatch gets its value at A beside it, in the trace's colour (Δ in the tooltip).

### Right pane: Tune and Step (R9.5)

Tune (the panel, still generated from `panels`):
- A row: `Manual | Live` as a square segmented control (Live lit amber when on), and `Presets ▼`
  on the right (load, Save as…, Delete inside the menu).
- The grid: an `L=R` box (lit when linked) in the top-left corner, `Left` / `Right` column labels,
  then one row per parameter: the label (drag to scrub, as today) and square black inputs with
  B612 Mono values. No spin arrows. Edited values are amber with an amber border.
- Booleans are 13 px square boxes (filled = on).
- `Send` under each column (the config's labels shortened), `Run test` across both columns.
- `Revert N` in amber, only while something is edited (Esc does the same).
- Under a hairline, the send log as plain grey lines: `20:41:07  PID L+R  Ki 0.015→0.02`. No table
  header, no `Send again` button: double-click a line to resend; the bytes are in the tooltip.

Step:
- Metrics as a table: label, `Now` in the measurement's colour, `Prev` dim, change in green when it
  improved. Then the setpoint and measured signal choices and an `Overlay previous` box.

### Plot (R9.6)

- Each lane is a framed graticule: 1 px `#4a4a4a` frame, 10 vertical divisions as dotted `#333` lines,
  horizontal dotted lines at the Y ticks (the zero line brighter), and a centre crosshair with minor
  ticks (5 per division).
- The Y gutter keeps the rotated lane name (dimmer, small caps) and tick labels in B612 Mono.
- No `Time [s]` label: the unit goes on the last X tick (`2.5 s`).
- Trigger armed: a dashed orange level line, an orange `T` marker on the right edge at the level,
  and an orange `T` marker at the top at the trigger position.
- Stopped with cursors: A solid and B dashed, light grey, the span between faintly shaded, and `A` /
  `B` flags on the top lane (A filled, B outlined).
- **Render budget (R3.4) still applies**: markers must not add a second paint per frame. If boxed-
  letter markers can't be drawn as fixed-size symbols without that, put them in the axis gutters as
  widgets. Check with `tools/bench_render.py`, interleaved against `main`.

## Items

| Item | What | Main files |
|---|---|---|
| R9.1 | Look: QSS (square, flat greys), bundled B612 / B612 Mono (OFL), scope palette in `streams.json`, C-locale numbers | `styles.py`, `main.py`, `assets/fonts/`, `streams.json` |
| R9.2 | Top bar replaces toolbar, tabs row and status bar | `ui/main_window.py`, `ui/panels/connection.py`, `stream_tabs.py` |
| R9.3 | Panes and edge tabs replace docks; `[` `]` `\`; state per profile | `ui/main_window.py`, `ui/ui_state.py` |
| R9.4 | Signals pane: paired rows, swatch toggles, readout in colour, R dashed | `ui/panels/signals.py`, `ui/charts/telemetry_plot.py`, config model |
| R9.5 | Tune / Step panes restyled and slimmed | `ui/panels/command_panel.py`, `command_log.py`, `trigger.py` |
| R9.6 | Graticule, x unit on the last tick, T and A/B markers | `ui/charts/telemetry_plot.py`, `lanes.py` |

Order: R9.1 first (everything else inherits it), then R9.3 and R9.2 (the frame), then R9.4–R9.6.

## Open questions (decide in the item's PR)

1. **L/R pairing (R9.4).** Detect the `L: ` / `R: ` name prefix, or add an optional pair key to a
   signal in `streams.json` (a schema bump, a migration step and a test)? Prefix detection needs no
   file change but ties behaviour to naming.
   *Decided in R9.4:* the label prefix, else the key prefix `left_` / `right_`, within a lane;
   no config key (`ui/panels/signal_rows.py`). A pair key can come later if a device needs it.
2. **Short button labels (R9.5).** `Update Left PID` → `Send` is a `panels` config edit; decide
   whether the panel keeps a long label for the tooltip.
3. **Right tab label.** `TUNE` for the bundled PID panel; for other panels, derive from the panel
   title or add a short title in `panels`?
   *Decided in R9.3:* `TUNE` for every command panel, with the panel title in the tooltip;
   `TERMINAL` for a text profile. No config key.
4. **Per-row linking.** ADR-0008 lets single rows be linked. The design has one `L=R` toggle; keep
   per-row linking (on the row's context menu) or drop it?

## Findings this answers (numbers from the canvas's "Now" board)

1 link stats always on · 2 red Disconnect · 3 baud and ⟳ for VIRTUAL · 4 format on the profile
button · 5 rate on every tab · 6 time button shows the period · 7 duplicate counts · 8 `L:`/`R:` × 34
· 9 checkbox + swatch · 10 dock title bars · 11 ten ⇄ buttons · 12 locale comma and trailing zeros ·
13 `Update Left PID` under `Left` · 14 Revert / Presets / Save as / Delete always shown · 15 empty
Sent table · 16 `PID Tuning` twice · 17 status bar row · 18 `Time [s]` row · 19 L and R share a
colour.
