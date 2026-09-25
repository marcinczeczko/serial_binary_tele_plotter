# ADR-0008: Dashboard layout: a toolbar, stream tabs and docks

- Status: Accepted (2026-09-25, R6.1–R6.5)
- Date: 2026-09-25
- Design spike: concept A of the UX canvas (layouts A, B and C compared with a screenshot
  of the previous UI).

## Context

The dashboard was one sidebar column holding everything, in this order:
- connection
- stream selector
- the PID panel (a 10 × 2 grid)
- time window
- trigger and step response
- the signal list

Setup that is touched once per session took most of the height. The signal list, which is
used the most, got about two visible rows out of 34 signals. The plot had no legend. The
readout was text drawn over the plot. The Configuration editor was a second tab that hid
the dashboard.

The control panel sent values but never showed what the device had last received. Tuning
two motors meant typing every value twice. Nothing tied a send to the response it caused.

## Decision

1. **A session toolbar**: port, baud, Connect, Pause (Space), Record (with the elapsed
   time), Trigger (with its state) and the link statistics. Setup no longer takes sidebar
   height.
2. **Streams are tabs above the plot**, each with its live sample rate or "no data". Every
   stream is decoded all the time, so a tab is only a view. The rate shows at once when a
   stream never receives frames. It's computed from the stores' counts at the once-a-second
   statistics tick.
3. **Docks** (`QDockWidget`), which the user can close, move or float. Their layout is saved
   with `saveState()` in `QSettings`.
   - **Left, Signals**: grouped by lane, with a filter, lane check boxes and counts. It is
     also the legend (color, name) and the cursor readout (value and Δ per signal).
   - **Right, Controls**: the shown stream's control panel, with the send log under it.
   - **Right, Step response**, tabbed with Controls: the metrics table. It comes forward on
     a capture.
4. **Nothing is drawn as text on the plot.** The readout moved to the Signals dock: the
   plot emits `readout_changed`, and the lane `TextItem`s are gone. A visible `TextItem`
   costs a second paint per frame (R3.4). The plot's overlays are lines and regions only:
   - send markers (dashed vertical lines)
   - the trigger level (a draggable horizontal line)
   - the pre-trigger shading of a capture
5. **The control panel shows edited vs sent.**
   - The main window tells the panel what each send carried (`mark_sent`), and the panel
     highlights and counts the values that differ.
   - Before the first send, what the device holds is unknown, so nothing is marked.
   - Rows can be linked across columns. Rows whose values start equal start linked.
6. **Live mode** sends a column after its values settle: a 150 ms debounce, at most once
   per 100 ms per column.
   - It's per panel, off by default and remembered. It has an amber style because it sends
     to hardware as the user edits.
   - A Live send while disconnected only sets the status. It isn't logged, so an edit burst
     doesn't flood the log.
7. **Every send is numbered and logged**, with what changed since the previous send of those
   values. The same number marks the plot at the send time of every stream that has data,
   on that stream's own time base.
   - Markers belong to a session; a new connection clears them.
   - Refused sends are logged too.
   - A logged packet can be sent again unchanged.
8. **Presets** are named value sets per panel, kept with the UI state (per config file), not
   in `streams.json`. They're one user's working values, not the device's definition. An
   A/B comparison is two presets plus the trigger's overlay of the previous capture. A
   dedicated A/B toggle was left out.
9. **The stream editor opens in its own window** (File → Edit streams.json, Ctrl+,). It no
   longer replaces the dashboard.

## Consequences

- The plot gets the full height, and the signal list most of a column. Session setup is one
  row.
- `MainControlPanel` no longer lays anything out. It owns the controls and the logic
  between them, under the same attribute names, and `MainWindow` places them. A different
  arrangement (for example concept B's activity rail) is a change to `MainWindow` only.
- Render cost is unchanged when the cursor isn't over the plot (`bench_render`: 30.3 FPS,
  the same as main in interleaved runs). While hovering, there's no text item left to force
  a second paint.
- Not done, and possible later:
  - dragging a parameter's label to change its value
  - dragging a signal to another lane (a context menu does it)
  - Esc to revert (it would conflict with closing popups)
