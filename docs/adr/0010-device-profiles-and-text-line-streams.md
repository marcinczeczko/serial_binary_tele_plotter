# ADR-0010: Device profiles, a decoder slot, and text-line streams

- Status: Accepted (2026-09-25, R8.1; R8.2–R8.4 to follow)
- Date: 2026-09-25
- Design spike: the "CSV streams" canvas (profile picker, the editor for a text profile,
  From console output…, the receive path), revised twice with the user.

## Context

The app reads one wire format: binary frames (`0xAA 0x55` header, CRC). Many boards,
Arduino-style ones especially, print text lines instead: CSV, or any layout the firmware
author chose (`ENV t=24.5C h=41%`), with no header line. Whether a device sends frames or
text is decided by its firmware, not per session.

Bytes flow `Transport → ReaderThread → FrameParser → StreamRouter → SampleStore`. Only the
parser and router know the wire format. Everything after them (stores, plot, trigger,
export) works on one numpy record array per stream.

Options considered for where the format lives:
- **Per stream** (a first draft): allows mixed ports, but the format is not a stream's
  property; it's the device's.
- **A port setting chosen at connect time**: recordings wouldn't know their format, and
  the streams would still need to match it.
- **A named device profile** (chosen): the bundle of format, baud and streams for one
  device, picked before connecting.

## Decision

1. **A decoder slot (R8.1).** The byte-to-records step is a `LinkDecoder`
   (`core/protocol/link.py`): `configure(streams)`, `reset()` for a new connection,
   `feed(bytes) -> {stream key: records}`, and the connection's `LinkStats`. The engine
   holds one and knows nothing else about the wire format. `BinaryFrameDecoder` wraps
   today's `FrameParser` + `StreamRouter` unchanged. The decoder is chosen by format name
   (`make_link_decoder`).
2. **Device profiles (R8.2).** A profile is one JSON file, as `streams.json` is today,
   with a top-level `profile` block:
   `{"name": "arduino-imu", "format": "binary" | "text", "baud": 115200}`. One file per
   profile, in a profiles folder: a profile can be committed next to its firmware, and
   everything already keyed by config file (last used, remembered view, recordings that
   embed their config) keeps working. The profile is picked first in the dashboard
   toolbar, only while disconnected. A schema 2 file loads as a binary profile named
   after the file (schema 3).
3. **Text lines are line patterns (R8.3).** A text stream's frame is a pattern: fixed
   text plus `{name}` number slots, e.g. `IMU,{ms},{ax},{ay}` or `ENV t={t}C h={h}%`. The
   slots are the stream's fields, so signals, lanes, the time base, the editor model and
   the stores need nothing new. A line goes to the first stream whose pattern matches;
   lines that match none (boot messages, debug prints) and overlong lines are counted in
   the link statistics. No delimiter, tag or header rules: a pattern covers them.
4. **Patterns are inferred from console output (R8.4).** In pasted lines, number runs
   become slots and the rest fixed text; lines with the same fixed text are one stream;
   a line seen once isn't a pattern. A slot is named after the text before it (`t=`),
   else `v1`, `v2`…; whole numbers get an integer type; a steadily rising integer is
   suggested as the X axis. Without a counter, the X axis is the line number.
5. **Text commands are a later phase (R8.5).** Commands stay binary packets for now.

## Consequences

- Adding a format is adding a `LinkDecoder`; the transport, reader, stores and GUI don't
  change. R8.1 changes no behaviour and was checked against the pipeline benchmark.
- A profile is the unit a user picks, shares and versions. `streams.json` becomes the
  first binary profile.
- Text streams have no CRC. A corrupted line either fails its pattern (counted) or
  decodes wrong values; a counter slot still reveals lost lines.
- `loop_cntr` stays required for binary frames and becomes optional for text lines.
