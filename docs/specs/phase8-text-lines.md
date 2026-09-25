# Spec: text-line streams (R8.3, R8.4)

- Status: R8.3 is built; R8.4 is next. R8.1 (decoder slot) and R8.2 (device profiles)
  are done.
- Decision record: [ADR-0010](../adr/0010-device-profiles-and-text-line-streams.md).
  This spec is the working detail below that decision. Change it freely while building,
  and say why in the project log.
- Design canvases (private claude.ai artifacts, owner's account):
  - CSV/text spike: <https://claude.ai/artifact/EgT5RaZ5kTDfg9ZGFUx2fA>
    - `Profiles`: the profile menu and New profile. This is built (R8.2).
    - `Main`: the editor on a text profile.
    - `PasteLine`: the "From console output…" dialog.
    - `Pipeline`: the receive path.
  - Editor spike (Phase 7, built): <https://claude.ai/artifact/FeZdbavtH5BNkHUiJvezmH>

## Why

Many boards (Arduino style, quick firmware) print text lines instead of binary frames.
The owner's requirements, in their words:

- The firmware author prints "in any format he implements". Don't stick to the Arduino
  Serial Plotter or to CSV rules.
- There is **no header line**.
- The structure is found by **pasting console output**.
- Binary vs text is a **hardware decision**. It lives in the device profile (R8.2), not
  per stream or per session.
- Text *commands* (host → board) are a later phase, R8.5. They are not part of this spec.

## Where things stand after R8.2

- The engine holds a `LinkDecoder` (`core/protocol/link.py`) built by
  `make_link_decoder(fmt)`. `LINK_FORMATS` has only `"binary"` so far.
- `profile.format` is validated against `LINK_FORMATS`, so a `"text"` profile is a fatal
  error today. Registering the text decoder makes it valid.
- `FORMAT_LABELS` already names `"text"` "Text lines". New profile lists every
  `LINK_FORMATS` entry, so "Text lines" appears once it is registered.
- `TelemetryEngine.configure_profile(name, fmt)` and `_use_format` swap the decoder.
- Recordings store the profile's format. A replay decodes with the recorded format
  (`RecordingHeader.profile_format`). Text lines recorded as raw bytes therefore replay
  with no further work.

---

## R8.3 Text-line decoder

### Config shape

A text profile is an ordinary schema 3 file:

```json
{
  "schema_version": 3,
  "profile": {"name": "arduino-imu", "format": "text", "baud": 115200},
  "streams": {
    "imu": {
      "name": "IMU",
      "frame": {
        "pattern": "IMU,{ms},{ax},{ay},{az}",
        "fields": [
          {"name": "ms", "type": "u32"},
          {"name": "ax", "type": "f32"},
          {"name": "ay", "type": "f32"},
          {"name": "az", "type": "f32"}
        ]
      },
      "time": {"field": "ms", "scale_s": 0.001, "step": 10},
      "signals": {"ax": {"field": "ax", "label": "Acc X", "group": "accel"}}
    },
    "env": {
      "name": "Environment",
      "frame": {
        "pattern": "ENV t={t}C h={h}%",
        "fields": [{"name": "t", "type": "f32"}, {"name": "h", "type": "u32"}]
      },
      "signals": {"t": {"field": "t", "label": "Temp"}}
    }
  }
}
```

- `frame.pattern` is **new**. It is allowed only in schema 3, which introduced profiles,
  so no older file can hold a text stream and there is no schema bump. Check that the
  document validation doesn't flag `pattern` as an unknown key.
- `frame.fields` stays. It gives each slot a type, and its names must equal the
  pattern's slots **in order**. Everything downstream (signals, lanes, time base, the
  `StreamDraft`, stores, export) keeps working on fields unchanged.
- `stream_id`, `endianness` and `packed` mean nothing for text. They are omitted, and
  ignored with a warning if present. `pattern` in a binary profile is also a warning.
- Types: any `STRUCT_TYPE_MAP` type is accepted. The editor offers **number** (`f32`)
  and **integer** (`u32`, which wraps like a binary counter; `i32` when negatives were
  seen). The 255-byte payload limit doesn't apply.

### Pattern grammar

- `{name}` is a number slot. `name` is an identifier (`[A-Za-z_][A-Za-z0-9_]*`).
- Everything else is fixed text, matched literally, with two exceptions:
  - Write `{{` and `}}` for literal braces.
  - A run of whitespace in the pattern matches one or more whitespace characters. This
    tolerates `%6.2f`-style padding.
- Two slots with no fixed text between them are an **error**, because the boundary
  would be ambiguous.
- A duplicate slot name is an error. So is a pattern with no slots.
- The name `_line` is reserved; see "X axis without a counter" below.

### Matching a line

1. **Split lines** on `\n`, strip a trailing `\r`, and strip leading and trailing
   whitespace. Skip empty lines silently, since a blank line is not an error.
2. **Bound the length.** `MAX_LINE_BYTES = 1024`. When a line grows past that without
   `\n`, drop bytes until the next `\n` and count `lines_overlong` once per line. The
   buffer never grows past the bound.
3. **Decode** as ASCII with `errors="replace"`. A mangled byte then fails the match and
   is counted; it never raises.
4. **Try each stream's pattern in config order.** The **first** full match wins, via
   `re.fullmatch` on a compiled regex.
   - A slot's sub-pattern is a number or empty:
     `[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?(?:nan|inf|infinity)` (case-insensitive),
     or empty.
   - Keep the regex non-greedy where fixed text follows, and test `1,,3` and `-.5e-3`.
5. **Convert values.**
   - An empty slot, or `nan`, gives NaN for a float field: a **gap** in the plot.
   - For an integer field, an empty or non-integral value (`nan`, `1.5`, out of range)
     is not stored. The line is dropped and counted in `value_errors`.
   - Integers are parsed with `int()`. A float such as `1e3` in an integer slot is
     accepted only if it is integral.
6. **Otherwise count it.** A line that matches no pattern (boot banners, debug prints)
   is counted in `lines_unmatched`. Nothing is dropped silently (C13).

Build records per stream in batches: collect the matched values of a `feed()` chunk,
then create one structured array per stream. Don't create one array per line. Measure
with `tools/bench_pipeline.py` and add a text case to it; a rough target is ≥ 20k lines/s.

### Records and dtype

- A text stream's records hold one field per slot, as the numpy type of its
  `STRUCT_TYPE_MAP` entry in native byte order, **plus** `_line: u32`.
- `_line` is that stream's matched-line count since `reset()`, starting at 0 and
  wrapping at 2³².
- `SampleStore.append_records` already reads the time field by name and fills missing
  signal fields with NaN, so the store needs no change.

### X axis without a counter

- `time.field` defaults to `loop_cntr` today (`core/acquisition/timebase.py`). For a
  **text** stream the default becomes:
  - `loop_cntr` if a slot of that name exists;
  - otherwise `_line`, with `step: 1`.
- `time.scale_s` still converts ticks to seconds. For `_line` it is "seconds per line",
  and the editor shows the axis as "(line number)".
- Counter tracking (`counter_gaps`, `counter_missing`, `counter_resets`) runs on the
  time field when it is a real slot. It doesn't run on `_line`, which can't gap.

### Statistics

- Add to `LinkStats`, `LinkReport` and `make_link_report`:
  - `lines_rx`: complete lines seen;
  - `lines_unmatched`;
  - `lines_overlong`;
  - `value_errors`.
- For text, `frames_decoded` counts matched lines, and `frames_by_id` stays empty.
- The status bar's link readout (`format_link_report`) shows the text counters when the
  decoder is text; `LinkReport` carries the decoder's `format`. The binary layout is
  unchanged. (Built this way in R8.3: the link counters were never in
  `ui/panels/timing.py`, which only holds Period and History.)

### Validation (`core/config/streams.py`, `document.py`)

- `validate_stream` needs the profile's format. Pass `fmt` from `validate_config`, which
  has the document. The draft or editor validation path must pass it too.
- **Text:**
  - `pattern` is required; parse it with the same grammar as the decoder, so there is
    one parser in `core/protocol/text_line.py`;
  - its slots must equal the field names in order;
  - `loop_cntr` is **optional**;
  - `stream_id` checks are skipped;
  - `shared_id_problems` is skipped;
  - two streams with an identical pattern is a warning, because the second never
    matches.
- **Binary:** unchanged. `loop_cntr` stays required.

### Simulator (VIRTUAL)

- `SimTransport` today uses `FrameSynth.frames(k0, n)`, which returns binary frames.
  For a text profile it prints lines instead:
  - the same waves (`sim` config);
  - each value formatted into the pattern, with integers as `%d` and floats with `repr`
    to about 6 significant digits;
  - `\r\n` line endings, which is what Arduino's `println` sends;
  - at the stream's rate.
- It also emits an occasional unmatched line, e.g. `# sim tick` once per second, so the
  counter is visible.
- The engine passes the format to the transport it builds. Commands to a text VIRTUAL
  device are ignored until R8.5.

### Module plan

- `core/protocol/text_line.py` (no Qt):
  - `parse_pattern(text) -> Pattern`, holding the tokens (fixed text and slots), the
    compiled regex and the slot names;
  - `format_line(pattern, values) -> str`, used by the simulator and "Copy as printf";
  - `TextLineDecoder`, which implements the `LinkDecoder` Protocol.
- `core/protocol/link.py`: `TEXT = "text"`; register `TextLineDecoder` in
  `LINK_FORMATS`. `LINK_FORMATS`'s type becomes a callable or a Protocol type.
- `core/acquisition/timebase.py`: the text default for `time.field`, as above.
- `core/config/streams.py`, `document.py`: format-aware validation.
- `core/simulation/synth.py`, `core/transport/sim_transport.py`: line output.
- `core/protocol/stats.py`, `ui/panels/timing.py`: the new counters.
- `README.md`: add a text-profile section with the pattern grammar and an example.

### Acceptance tests (R8.3 is done when these pass)

- `tests/test_protocol_text_line.py`:
  - pattern grammar: slots, `{{`, adjacent slots rejected, whitespace runs;
  - numbers: sign, exponent, `nan`, `inf`, `.5`;
  - an empty slot gives NaN;
  - an integer slot with `1.5` gives `value_errors`;
  - first match wins;
  - lines split across `feed()` chunks;
  - `\r\n`;
  - an overlong line is counted once and the buffer stays bounded;
  - unmatched and non-ASCII lines are counted;
  - `_line` counts per stream;
  - `reset()` clears the buffer, stats and `_line`.
- Validation: a text profile with a pattern-and-fields mismatch is an error; no
  `loop_cntr` is fine for text and still an error for binary; `pattern` in a binary
  profile is a warning.
- An engine test (no Qt): a text profile's decoder is chosen, and `feed` fills the store.
- A `qt` test: a text profile plots from VIRTUAL, and the timing panel shows
  `lines_unmatched` > 0.
- A recording test: record text from VIRTUAL, replay it, and get the same records.

---

## R8.4 Editor for text profiles

Everything here follows the canvas boards `Main`, `Text stream editor states`,
`PasteLine`, `Listening on the port` and `Dashboard on a text profile` (revised
2026-09-25: states added, extra text cut), and the look of the existing editor (ADR-0009):

- black background;
- `#1b1306` decode strip;
- colored hexagon value blocks;
- grey `#888` secondary text;
- no extra chrome.

The editor still edits a `StreamDraft` only.

### Profile row (all profiles)

- A row under the editor toolbar, above the stream tabs:
  - `Profile: [name]`;
  - `Format: Text lines`, shown bold and read-only (the format is chosen at New
    profile);
  - `Baud: [combo]`.
- Editing it changes the document's `profile` block. On save, the dashboard's profile
  menu and title refresh; `MainWindow._refresh_profiles` / `_apply_profile` exist.
- The toolbar gains **From console output…** and **Copy as printf** for text profiles.

### Stream settings row (text)

- Key and Name, as now.
- **Pattern** replaces ID and byte order. It is a monospace line edit.
  - Editing the pattern re-derives the fields. Slots that keep their name keep their
    field, type and signals. A new slot becomes a new field (number). A removed slot
    removes its field and signals, like removing a binary field.
  - An invalid pattern gets a red border and its parse error in the status line
    (`· {ms} and {ax} need text between them`). It is not applied: the Line view keeps
    the last valid pattern, dimmed, until the pattern is fixed or Esc reverts it.
- **X axis**: a combo of the integer slots plus "(line number)", then `× time per tick`
  (`format_seconds` / `parse_seconds` exist), then Step. With "(line number)" the
  Step field is hidden and the unit reads "per line".
- **Controls**, as now.

### Line view (replaces the frame view for text)

- One row that draws the pattern:
  - fixed text as grey monospace;
  - each slot as a colored hexagon block, the same colors as the lanes (`field_colors`);
  - the selected slot inverted (white outline, filled color).
- A second row shows `last line`, then the line, then `✓ matches` in green or
  `✗ no match` in grey. No source or age is shown. The line is:
  - while connected, the newest line this stream matched, else the newest unmatched
    line (most likely the one being fixed). The engine's ~1 Hz `LinkReport` carries
    them (`last_lines: {key: line}` and `last_unmatched`); no per-line signal;
  - otherwise the last line pasted or heard in "From console output…" for this layout;
  - otherwise `none yet: connect, or paste console output`, in grey.
- Clicking a block selects its field in the form. The form on the right is titled
  **Value** and holds:
  - Name;
  - Type (number / integer);
  - Position ("4 of 7");
  - Label, Lane, Color, Line and Shown, as for binary;
  - Stop plotting;
  - Add value after / Remove value, which edit the pattern (inserting `,{vN}` after the
    slot, or removing the slot and its preceding separator).

### "From console output…" dialog

- Left: a paste box, plus **Listen on the port for 5 s**. That button is enabled only
  while connected (disabled, tooltip "Connect first"). While listening it reads
  `Listening… 3 s` (Cancel closes the dialog and stops it); then the lines are added to
  the box. No progress bar, no helper text. It collects raw lines from the engine: add an engine slot that
  captures raw lines for N seconds and emits them once, queued. It must not add a
  per-line signal.
- Right: **Line patterns found**. One card per pattern, each with:
  - a checkbox;
  - an editable stream name, defaulting to the first word of the fixed text, else
    `stream N`;
  - the pattern in monospace;
  - the line count;
  - the Line view blocks;
  - a value table: name, type, range seen, and `X axis, step 10` on the suggested axis
    (no other notes: the type says signed or not);
  - an unticked card is dimmed and not created.
- Below the cards: `Not a pattern (seen once): …`.
- Footer: `2 streams, 6 values from 9 lines. Values are named from the text next to
  them, else v1, v2…`, then Cancel and **Create N streams**, which adds them to the
  current profile as new drafts.

**Inference** (a pure function in `core/config/infer_lines.py`, no Qt):

1. For each non-empty line, split it into number runs (the decoder's number regex) and
   fixed text between them. The **shape** is the tuple of fixed-text pieces.
2. Group lines by shape. A shape seen **once** is not a pattern; it goes in the "seen
   once" list. Order the patterns by first appearance.
3. **Slot names.**
   - If the fixed text right before a slot ends in `name=` or `name:` (an identifier,
     optionally followed by spaces), the slot is named `name`, e.g. `t=` gives `t`.
   - Otherwise slots are named `v1`, `v2`… in order.
   - Make names unique (`unique_name` exists in `core/config/draft.py`).
4. **Types.** If every value of a slot is a whole number, the slot is an integer
   (`u32`, or `i32` if any value is negative). Otherwise it is a number (`f32`).
5. **X axis suggestion.** An integer slot whose values strictly increase with a constant
   (or near-constant) step is suggested as the X axis, with `time.step` set to that
   step. The owner decides `scale_s`: default it to 1 ms per tick when the name hints at
   `ms`, else leave it at the line-number default.
6. Build a stream dict per pattern: fields, the pattern, a signal per non-axis slot, and
   lanes left to the draft defaults. Then check that `TextLineDecoder` decodes the
   pasted lines into it. That is R8.4's "Done when".

### "Copy as printf"

- Puts a C `printf`/`Serial.printf` line for the current stream on the clipboard, e.g.
  `printf("IMU,%lu,%f,%f,%f\r\n", ms, ax, ay, az);`.
- Integers map to `%lu` (unsigned) or `%ld` (signed); numbers map to `%f`.
- Literal `%` is escaped as `%%`.
- This helps firmware authors print exactly what the profile expects.

### Acceptance tests (R8.4 is done when these pass)

- `tests/test_core_infer_lines.py`, using the canvas example:
  - input: a boot line, `IMU,…` lines and `ENV t=24.5C h=41%` lines;
  - expected: two patterns, `IMU,{v1},{v2},{v3},{v4}` and `ENV t={t}C h={h}%`;
  - types: `v1` `u32` (suggested X axis, step 10), `h` `u32`, the others `f32`;
  - the boot line is in "seen once";
  - the pasted lines decode through `TextLineDecoder` into those streams.
- Pattern edit keeps an unchanged slot's signal (a draft test).
- `qt` tests for the editor on a text profile:
  - the profile row edits the block;
  - a Pattern edit updates the Line view and fields;
  - the dialog creates N streams;
  - Copy as printf fills the clipboard.
- An untouched text stream saves byte-identically (C4).

---

## R8.5 (later, not in scope): text commands

Host → board commands as text templates (`"PID {kp} {ki}\n"`) for text profiles, reusing
the command panels. Until then, a text profile's panels can't send; show them disabled
(the reason in a tooltip, no text on the dock). A stream without a panel shows none, as
today. The dashboard needs nothing else for text: the status bar's link readout (R8.3)
already shows the line counters.

## Suggested PR split

1. R8.3 core: `text_line.py`, stats, time base default, validation, simulator, tests.
   Check with `bench_pipeline` and record the numbers.
2. R8.3 UI: timing counters, and a `qt` test that plots from VIRTUAL. Can merge with 1
   if small.
3. R8.4 editor: the profile row, Pattern, Line view.
4. R8.4 inference dialog and Copy as printf.
