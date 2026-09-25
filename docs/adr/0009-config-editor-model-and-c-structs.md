# ADR-0009: Configuration editor: an editing model, the scope's look, and C structs

- Status: Accepted (2026-09-25, R7.1–R7.3)
- Date: 2026-09-25
- Design spike: the editor mockups in the scope's look, and the paste flow, chosen over
  three denser layouts that were judged "too much information".

## Context

The stream editor was two tabs: frame fields in one table, signals in another, joined by
a "Field Map" drop-down. `pid` has 35 fields and 34 signals, so every value was described
twice. The byte layout (offsets, the 255 B limit) wasn't visible. The editor kept a
stream in its widgets and rebuilt the dict from them, which is how edits and unknown
keys used to get lost (C4). It needed a commit on every stream switch to stay safe. Its
look (tables, group boxes, a green "SAVE TO DISK") didn't match the scope.

The firmware already describes each frame as a C struct, and nothing connected the two.

## Decision

1. **An editing model, `StreamDraft` (`core/config/draft.py`, no Qt).** It holds the
   stream dict, and each operation changes only the keys it's about:
   - rename a field, and its signals, time field and simulator entry follow;
   - remove a field, and its signals go;
   - plot a field (a signal labelled with the field's name, in a free palette color);
   - set a signal's label, color, lane, line or visibility;
   - write a time key only if the file had it or the value isn't the default.
   An untouched stream saves byte-identically without any special casing. The widgets
   call these operations and redraw from the draft. Each stream keeps its draft until
   saved or reverted, so switching streams can't lose an edit.
2. **The scope's look.** The same black theme, buttons and tab bar as the dashboard:
   - a toolbar (New stream, From C struct…, Copy as C struct, Delete, Revert, Save; Save
     turns orange with unsaved changes; Ctrl+S);
   - the streams as tabs (`PID Telemetry · 0x01`);
   - the stream's settings on one line (key, name, ID, byte order, X axis × time per
     tick such as `5 ms`, step, controls);
   - **the frame**, drawn like a bus decode on the plot's background: 32 bytes per row,
     one hexagon per field in its signal's color, grey when not plotted;
   - **the signals by lane**, like the Signals dock, with a "Not plotted" group for the
     fields without a signal. Dragging a field onto a lane plots it there; dragging a
     signal onto "Not plotted" removes it;
   - a form for the selected field and its signal;
   - a status line with the stream's size and its first problem, as you edit.
   No sparklines, previews or explanatory text.
3. **Only what was typed is applied.** A line edit is applied when you leave it, or on a
   save or switch if you typed in it. Text a redraw left in a widget is never taken for
   an edit (it once relabelled a newly plotted field with the previous stream's label).
4. **From C struct… (`core/config/cstruct.py`, no Qt).** It reads what a 32-bit MCU
   compiler lays out:
   - fixed-width integers, `float`, `double`, `bool`, and `char`/`short`/`int`;
   - several names per line, one-dimensional arrays (`name_0` …);
   - `packed` (attribute or `#pragma pack(1)`), and a `#define` with "ID" in its name for
     the stream ID; array sizes from `#define`s.
   - Without `packed`, the padding the compiler inserts becomes `_pad<offset>` u8 fields,
     so the layout still matches the wire. Bare member lines are taken as packed.
   - Nested structs, bit-fields, pointers and `long` are listed as problems and left out,
     never guessed. Labels start as the field names; guessing labels, units or lanes from
     names was tried in the spike and dropped.
   The dialog redraws the frame as you type. "Into" makes a new stream, or **replaces the
   fields** of the shown stream when the struct changed: fields that keep their name keep
   their signal's label, color and lane.
5. **Copy as C struct.** Any stream as a packed struct with its ID `#define` and a
   `_Static_assert` on its size, so the firmware can include what the plotter expects.
   Every bundled stream round-trips through it (a test).

No schema change: the editor writes the same `streams.json`. Commands and panels are
still edited in the file.

## Consequences

- The editor is one screen, and a field is described once: its place in the frame and
  how it's plotted are side by side, and selecting it in either selects it in both.
- C4-style loss can't come back through the editor: there is no "rebuild from widgets"
  step left to get wrong. The old round-trip tests still pass unchanged in intent.
- Starting a stream from firmware is a paste; a changed struct is a paste into the same
  stream.
- Not done, and possible later:
  - editing commands and panels in the editor (a command is a frame going the other way,
    so the same frame view would fit);
  - reordering fields by dragging in the frame view (the context menu moves them);
  - showing live values or checking the layout against the frames that arrive.
