# Spec: the configuration editor in the scope look (Phase 11, R11.1–R11.4)

- Status: approved by the owner (2026-09-26), with the four open questions answered as proposed.
  Built 2026-09-26 (R11.1–R11.4).
- Design canvas: <https://claude.ai/artifact/RYAdURH9Zk94xBP5dpJN9C> ("Config editor redesign").
  - **Now**: the current editor (PID Telemetry, 1300×860) with 14 findings pinned.
  - **Proposed** (interactive; Tweaks switch the frame between strip and grid and show a problem).
- Builds on Phase 9 ([spec](phase9-scope-view.md), ADR-0012): same tokens, fonts, bar and swatches.
  The editor model is unchanged (ADR-0009): every edit is an operation on a `StreamDraft`.

## Why

Phase 9 made the main window a scope; its editor window still looks like the app before it. Four
rows of controls come before any content, the byte map is the loudest thing on screen and repeats
the tree, the tree lists 34 rows where the Signals pane shows 17, and the form is sparse with
always-armed buttons at the bottom. The owner wants it simplified the same way as the main window.

## Decisions

1. **It stays a separate window** (File → Edit profile), not a view in the main window.
2. **Frame: a one-row strip**, not the grid (the paste dialog keeps its bus-decode `FrameView`).
3. **L=R in the editor**: on by default for a pair; lane, colour and line width go to both sides.
   Label, line style (R stays dashed) and "shown" stay per side.
4. **Save and Revert show only while there are unsaved changes** (as Tune's `Revert N`).
5. **Text profiles follow the binary layout**: the Line view in place of the strip, `Pattern` in
   place of ID and byte order, `Console ▼` in place of `C struct ▼`, "value" for "field".

## Layout

```
┌ top bar (36 px) ─────────────────────────────────────────────────────────────────────────┐
│ diffbot │ Binary 115200 ▼ │ PID Telemetry · 0x01 │ PID FF · 0x01 │ IMU · 0x03 │ + │ msg │ C struct ▼ │ [Revert][Save] │
├ stream row (36 px): Key [pid] Name [PID Telemetry] ID [01] Order [LE|BE] X [loop_cntr▼] × [5 ms] step [1] Tune [diffbot_pid▼]
├ frame strip: one row of cells sized by bytes; offsets and size under it ─────────────────┤
├───────────────────────────────────────────────────────────────┬─────────────────────────┤
│ Signal                               L                R        │ ▪ L: Setpoint     [L=R] │
│ SPEED rps ─────────────────────────────────────────────────── │   left_setpoint         │
│ Setpoint          ▪ left_setpoint  12 │ ▪ right_setpoint  80   │ SIGNAL ──── Stop plotting│
│ …                                                              │ Label / Lane / Colour / │
│ NOT PLOTTED ───────────────────────────────────────────────── │ Line [— - ·][1 2 3] /   │
│ X axis            loop_cntr         0                          │ Shown                   │
│                                                                │ FIELD ─ Insert after  Remove │
│                                                                │ Name / Type / Byte      │
└───────────────────────────────────────────────────────────────┴─────────────────────────┘
```

No toolbar, no profile row, no status line.

### R11.1 Top bar and stream row (`ui/config/tab.py`, `ui/config/stream_editor.py`)

- The main window's `TopBar`: profile name (edited in place), format and baud (`Binary` muted, the
  baud a flat combo), the stream tabs (`Name · 0xID`), `+` (menu: Empty stream, From C struct… /
  From console output…), the message, `C struct ▼` (Paste…, Copy) or `Console ▼` (From console
  output…, Copy as printf), then Revert and an amber Save only while unsaved.
- Delete stream moves to the stream tab's right-click menu.
- The message replaces the status line: the stream's first problem (red error, amber warning)
  stays while it's true; confirmations (`Saved`, `Copied …`) fade.
- Stream row: words without colons; the ID without spin arrows; byte order as an `LE | BE`
  segment; `X [field] × [5 ms] step [1]`; `Tune [panel]` (was Controls).

### R11.2 Frame strip (`ui/config/frame_strip.py`)

- One row, each field's cell as wide as its bytes. Grey by default; a plotted field has its
  signal's colour as a 3 px top edge (grey when hidden at open); the selected field is filled.
- A field's name is drawn only where it fits; the tooltip gives name, type and byte.
- Under it: byte offsets (every 16 B, or 4 B for a short frame), the selected field
  (`left_setpoint f32 +12`) and the size (`140 / 255 B`, red when over).
- Click selects, right-click opens the field menu (as the old frame view).

### R11.3 Signals list (`ui/config/signal_list.py`)

- The Signals pane's structure: lane headers (`SPEED rps` and a hairline), one row per L/R pair
  (`signal_rows.pair_rows`), columns Signal | L | R (one `Field` column when no row is a pair).
- A side's cell: its swatch (filled = shown when the stream opens, hollow = hidden; click to
  toggle), its field name (mono, muted) and its byte (position for text).
- Clicking a side selects that signal; the selected cell is lit with a bar in its colour.
- `NOT PLOTTED` at the end: fields without a signal; the X axis field is tagged.
- Drag and drop as before (onto a lane, empty space for a new lane, `Not plotted` to stop
  plotting); a pair moves together. Lane headers keep Rename lane… on right-click.
- Edits that rebuild the list after a click run on the next event-loop turn (the item under the
  mouse must outlive Qt's handler; the crash fixed in #32).

### R11.4 Inspector (`ui/config/stream_editor.py`)

- Header: the selected signal's swatch and label in its colour, its field under it, and `L=R`
  when it has a pair.
- `SIGNAL` section (header action `Stop plotting`): Label, Lane, Colour (a chip and its hex),
  Line (style `solid | dashed | dotted` and width `1 | 2 | 3` as segments), Shown (a swatch).
  For a field without a signal: "Not plotted." (or "The X axis.") and `Plot`, whose arrow
  lists the lanes and New lane (a click plots in the first lane).
- `FIELD` section (header actions `Insert after`, `Remove`): Name, Type, Byte
  (`12  0x0C · 4 B`); text: `VALUE`, Position (`4 of 4`).

## Done when

- The window has no `QToolBar`-like button row, profile row or status line; Revert and Save are
  hidden while nothing is unsaved.
- The bundled PID stream shows as 17 pair rows, each side with its field and byte, and the frame as
  one strip row.
- With L=R on, changing the colour, lane or width of `L: Setpoint` changes `R: Setpoint` too; an
  untouched stream still saves byte-identically (C4).
- Every behaviour the old editor had still works (tests: `test_qt_config_editor.py`,
  `test_qt_text_editor.py`, `test_qt_integration.py`).
