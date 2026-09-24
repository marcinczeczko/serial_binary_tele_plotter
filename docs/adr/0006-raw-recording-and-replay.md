# ADR-0006: Raw byte recordings (`.sbtp`) and replay as a transport

- Status: Accepted (2026-09-24, R4.1, R4.2)
- Date: 2026-09-24
- Addresses: review finding A3 ([review](../reviews/2026-09-24-architecture-review.md)).
  Builds on [ADR-0002](0002-target-acquisition-pipeline.md) ("all sources produce bytes")
  and [ADR-0004](0004-byte-level-simulator.md).

## Context

There was no way to keep a session (A3). A tuning run was gone once the ring buffer
wrapped or the app closed, so a before/after comparison meant repeating the experiment on
the robot. Bugs in the receive path could only be reproduced with the hardware attached.

What to store was the main choice:
- **Decoded samples** (CSV, Parquet, HDF5) are easy to analyse elsewhere. But they bake in
  the decoder and config of the day: a fixed decoder bug or a corrected field type can't
  be applied to old data, and dropped frames, CRC errors and resyncs are gone.
- **Raw bytes** keep everything the device sent. Anything derived (samples, statistics,
  gaps) can be recomputed by running them through the current pipeline.

## Decision

1. **Recordings are the raw bytes, one chunk per transport read.** A new binary file
   format, `.sbtp` (little endian):

   ```
   "SBTP"  u16 version (1)  u32 header_len  header: UTF-8 JSON
   repeated:  u64 host_ts_ns  u32 length  bytes[length]
   ```

   - The JSON header has `created` (UTC, ISO 8601), `source` (the port name) and
     `streams` (the full stream config in use). Unknown keys are kept, so later versions
     can add fields without bumping the version. The version only changes if the chunk
     layout changes.
   - `host_ts_ns` is the host's wall clock at the read. It's used only to pace replays;
     plotted time still comes from the frames' own time field (ADR-0003).
2. **The recorder taps the engine's byte callback, before parsing**, on the reader thread.
   Writes are buffered (1 MiB). The engine flushes with the once-a-second link statistics,
   so a crash loses at most about a second. Files are opened exclusively and never
   overwritten. A write error stops the recording, not the session, and is reported.
   Recording can start automatically on connect (a `QSettings` option).
3. **A replay is a `Transport`** (`ReplayTransport`, port name `REPLAY:<file>`). The engine
   runs it exactly like a serial port: same `ReaderThread`, parser, router, time base,
   stores and statistics. So a replay reproduces the session's CRC errors, gaps and
   resyncs too.
   - Pacing is by recorded timestamps ÷ speed (1×, 2×, 5×, 10×, or max). It can be paused
     and stepped one recorded read at a time.
   - At the end, `read()` raises `ReplayEnded`, a `TransportError`. The session ends like
     an unplugged device, but the status says "Replay finished".
   - A replay is never recorded again, and `write()` (PID commands) is refused.
4. **A replay decodes with the current `streams.json`**, not the header's config, and says
   so when the recorded frame layouts differ. That's what makes fixing a config or decoder
   and replaying worthwhile. The header config is kept so the original layout is never
   lost, and decoding with it can be added later without a format change.
5. **Recordings double as regression fixtures.** `tests/fixtures/pid_sim_300.sbtp` has 300
   simulated frames with one corrupted payload byte. A test replays it and pins the
   decoded count, CRC error, sequence gap and time gap.

Decoded data can still be exported to CSV/Parquet (R4.3) for use in other tools. Export
is a view of the data, not the archive format.

## Consequences

- Any session can be kept and re-analysed with a later decoder. Receive-path bugs can be
  reproduced and tested without hardware.
- Files hold every field, not just the shown ones, plus 6 B of framing per frame and 12 B
  per read. The bundled PID stream (146 B frames at 200 Hz) records about 29 KB/s, or
  about 105 MB an hour.
- Recording costs the reader thread about 3 µs per 900 B read (buffered write). Parsing
  and storing the same read takes about 50 µs.
- The file format is now an interface: readers must keep accepting version 1, and a
  layout change needs a new version and this ADR superseded.
- Pacing by host timestamps reproduces USB/driver read batching, not the MCU's exact
  frame timing. That's fine because the X axis never used host time.
