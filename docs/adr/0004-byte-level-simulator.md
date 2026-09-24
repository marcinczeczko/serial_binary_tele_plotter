# ADR-0004: Byte-level simulator configured per stream

- Status: Accepted (2026-09-24, R2.7)
- Date: 2026-09-24
- Addresses: review finding A6 ([review](../reviews/2026-09-24-architecture-review.md)).
  Implements rule 4 of [ADR-0002](0002-target-acquisition-pipeline.md): "all sources
  produce bytes".

## Context

`VirtualDevice` was a QObject with a QTimer that emitted Python dicts straight into the
store (A6):
- It never exercised the parser, CRC, router, decoder or time base.
- It chose its waveforms by substring-matching the stream's *name* ("imu", "control").
- Streams it didn't recognise got PID-named fields that matched nothing, so they plotted
  as NaN.
- It ran at the global UI period, and commands sent while on VIRTUAL were rejected as
  "not connected".

## Decision

1. **`SimTransport` is the VIRTUAL port.** It implements `Transport`, and the engine drives
   it with the same `ReaderThread` as a serial port, so bytes → `FrameParser` →
   `StreamRouter` → `SampleStore`, and link statistics count it.
   - It paces frames in real time at the stream's own period (`time.scale_s × time.step`),
     handing out bytes at most every 10 ms, like a buffered port.
   - If it falls more than 2000 frames behind, it skips them, and the time base shows a
     gap.
   - It simulates the shown stream. `select_stream` retargets it without a restart, and
     the frame counter continues across switches.
2. **`FrameSynth` builds real frames from the stream definition**: a packed numpy record,
   plus header and CRC. What each field carries comes from config:

   ```json
   "sim": {
       "model": "pid_motor",
       "fields": {"acc_z": {"wave": "const", "offset": 1.0, "noise": 0.03}}
   }
   ```

   - `wave` is `sine | step | noise | const | counter`, with `amp`, `freq_hz`, `offset`,
     `phase_deg` and `noise` (a Gaussian standard deviation).
   - The time field and `loop_cntr` count frames, wrapping like the MCU's integers.
     Integer fields are clipped to their range.
   - Every field without a spec or model gets a distinct default sine, so any stream shows
     data on VIRTUAL with no `sim` block at all.
3. **`pid_motor` is an explicit model, chosen in config.** It's a feedforward + PI speed
   loop with back-calculation anti-windup, driving a first-order DC motor with static
   friction, for `left_*`/`right_*` fields (see `core/simulation/pid_motor.py`).
   - It parses the PID command packets written to the transport, so the PID panel works
     on VIRTUAL.
   - The command layouts are now constants shared with the encoder. The wire format is
     unchanged.
4. **`sim` problems are warnings, never errors.** The block only affects VIRTUAL, so it
   never excludes a stream. The synthesiser falls back to a default wave for anything
   invalid.

## Consequences

- VIRTUAL is now an end-to-end test of the receive path. Any stream in `streams.json` can
  be simulated with no code.
- The name-substring logic and `core/acquisition/virtual.py` are gone.
- The simulator costs 19–44 µs per `pid` frame (2–40 frames per call). That's under 1% of
  a core at 200 Hz, on the reader thread.
- `*_aw_term` stays at zero unless the output saturates. That's correct behaviour, and a
  test drives the model into saturation.
- Simulated commands mean something only for the model (`k1` is the feedforward gain,
  `k2` the friction offset, `k3` is ignored). This is documented in the model, not
  implied for the firmware.

## Alternatives considered

- **Keep dict injection and add a "bytes mode".** Two code paths, and the default would
  still bypass the parser. Rejected.
- **Simulate every configured stream at once**, like an MCU multiplexing streams. Streams
  sharing an ID and layout would duplicate frames, and most firmware sends one stream at
  a time. This could become a `sim` option later.
- **A separate simulator process on a pty.** It's more realistic, but platform-specific
  and heavier. The pty end-to-end test already covers the serial path.
