# ADR-0002: Target acquisition and rendering pipeline

- Status: Accepted (2026-09-24, Phase 2 started with R2.1)
- Date: 2026-09-24
- Addresses: review findings A1, A2, A6, P2, P3, P5, C2, C5
  ([review](../reviews/2026-09-24-architecture-review.md))

## Context

Today one `TelemetryEngine` QThread does four things. It polls pyserial on a 10 ms QTimer,
parses only the one selected stream ID, stores samples in numpy rings, and every 100 ms
*pushes* a full copy of every signal to the GUI through a queued signal. Measured
consequences:

- Reads larger than 4 KiB are discarded (C1). Polled reads compete with the snapshot work.
- The snapshot costs up to 26 ms and 27 MB per tick, whether or not data changed (P2).
- Nothing bounds how many snapshots can queue up for the GUI (P3).
- Only one stream can be seen at a time (A1). The time axis depends on a UI setting (C2).

## Decision

Build the pipeline from small, single-purpose parts. Data flows through a lock-protected
store that the GUI **pulls** from at its own frame rate.

```
          ┌──────────────── reader thread ────────────────┐
Transport │  read(timeout) → FrameParser → StreamRouter    │
 Serial   │     (sync, CRC,      (stream_id →               │
 Sim      │      all IDs,         Decoder: numpy dtype,     │
 Replay   │      stats)           batch decode)             │
          │                          │                      │
          │   RawRecorder (opt.) ◀───┤ bytes + host ts      │
          └──────────────────────────┼──────────────────────┘
                                     ▼
                         SampleStore[stream]  (double-write ring,
                          time column computed at write,
                          version counter, short Lock)
                                     ▲
          ┌───────────── GUI thread ─┴────────────────────┐
          │ PlotController QTimer 30–60 FPS:               │
          │   snap = store.snapshot(visible_ids, x_range,  │
          │                         since_version)         │
          │   if snap: lanes[i].curve.setData(...)         │
          │ Stats label ← stats snapshot at 1 Hz           │
          └────────────────────────────────────────────────┘
Commands: CommandSpec (config) → encoder → Transport.write(write_timeout)
Control:  Session (GUI-side facade) → queued calls → AcquisitionService
          AcquisitionService owns the state machine and emits state_changed
```

Key rules:

1. **Only small control messages and stats cross threads by signal.** Bulk data never
   goes through the Qt event queue. The GUI copies exactly what it's about to draw.
2. **Every configured stream is decoded all the time.** Stream selection is a *view*
   concern.
3. **Time is computed where the sample is written**, from a per-stream `time` spec
   (field + scale). The store handles counter wrap and reset and inserts NaN gaps.
4. **All sources produce bytes.** The simulator and replay go through the same parser as
   real hardware.
5. **One owner for lifecycle state.** The acquisition service runs its state machine on
   its own thread. The GUI only reflects `state_changed`.

## Consequences

- Positive: no data loss from polling or GIL stalls. GUI cost is proportional to what's
  visible, not to buffer size. Multi-stream and record/replay come almost for free. The
  whole pipeline can be tested deterministically from recorded byte fixtures.
- Negative: a real refactor of `core/acquisition` and `ui/main_window.py`. The lock
  discipline in `SampleStore` must be kept simple: one lock, O(1) work while writing,
  copies done outside the lock where possible. A small Python reader thread still shares
  the GIL. That's acceptable at the measured throughput (about 10 MB/s for per-frame
  Python parsing, more once decoding is batched).

## Alternatives considered

- **Keep the push model and throttle harder.** This lowers the frequency of P2/P3 but not
  the cost per tick or the unbounded queue. Rejected.
- **`QSerialPort` with `readyRead` in the engine thread.** It's event-driven and avoids
  polling, but readiness still depends on that thread's event loop, which also does the
  snapshots. It could still be used inside `SerialTransport` if pyserial proves
  problematic on some OS.
- **A separate process (multiprocessing and shared memory).** It avoids the GIL entirely,
  but adds a lot of complexity. Revisit only if R3.4's render-budget check fails after
  Phase 2.
