# ADR-0011: Text commands

- Status: proposed (2026-09-25)
- Supersedes: nothing; extends ADR-0007 (commands and panels in config) and ADR-0010
  (device profiles and text-line streams), whose point 5 left text commands for R8.5.

## Context

A text profile's board prints lines and reads lines. The command panels (ADR-0007) can
only send binary packets (`packet_id` + packed fields, framed with CRCs). Everything
above the encoding (parameters, buttons, presets, Live mode, the log) applies to a text
device unchanged.

## Decision

1. **A text command is a template**, e.g. `"PID {motor} {kp} {ki}\n"`, in the same
   grammar as a text stream's `frame.pattern`, with the same rule: its slots are its
   `fields`, in order. The line ending is part of the template, so the bytes sent are
   exactly what the file says.
2. **Fields and panels keep their meaning.** A field's `type` range-checks and formats
   its value; its value still comes from a button, a constant or a panel parameter.
3. **Numbers are formatted to be read back exactly and simply:** integers as decimals,
   floats as the shortest text that round-trips, never with an exponent (Arduino's
   `toFloat()` can't read one). NaN and infinity are refused.
4. **The engine doesn't change.** The GUI encodes the line and hands the engine bytes
   (`send_packet`), as for binary packets.
5. **The format decides.** In a text profile a command must have a template; in a binary
   profile a template is ignored with a warning. No schema bump: `template`, like
   `frame.pattern`, only exists in schema 3 files.

## Consequences

- A text board is tuned with the same panels as a binary one; the simulator answers text
  commands like the firmware would, through the same model.
- Commands to a binary device are unchanged, byte for byte (the pinned `0x10`/`0x11`
  tests stand).
- Formatting is fixed in the app, not per field. If a firmware needs a fixed number of
  decimals, a per-field format can be added later without changing templates.
