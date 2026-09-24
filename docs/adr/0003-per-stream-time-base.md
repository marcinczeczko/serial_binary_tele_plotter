# ADR-0003: Per-stream time base

- Status: Accepted (2026-09-24, R2.5)
- Date: 2026-09-24
- Addresses: review finding C2 ([review](../reviews/2026-09-24-architecture-review.md)).
  Refines rule 3 of [ADR-0002](0002-target-acquisition-pipeline.md).

## Context

Until now, time was `loop_cntr × Period`, where Period was one global spin box in the UI
(C2):
- A wrong Period silently skewed every readout.
- Changing it rescaled every stream.
- An MCU reset or a counter wrap made time run backwards. That drew a line back to the
  left and broke the cursor readout, which needs sorted time.
- Lost frames were bridged by straight lines.

ADR-0002 said time is computed where the sample is written, from a per-stream `time` spec.

## Decision

1. **Config.** Each stream may have `"time": {"field", "scale_s", "step"}`:
   - `field` defaults to `loop_cntr`.
   - `scale_s` is seconds per tick of that field. It defaults to 0.005, the old UI default,
     so existing files behave as before.
   - `step` is the field's nominal increase per frame (default 1). For a µs timestamp
     field it's the loop period in µs.

   `validate_config` checks the block. The bundled `streams.json` states it explicitly.
2. **Ticks at write, seconds at read.** When a batch is stored, `TimeBase` converts the
   raw field values to *monotonic ticks* under the store's lock:
   - **Wraps** are unwrapped. An integer field has modulus 2^bits. A backwards jump is a
     wrap only if the distance forward through the wrap is at most
     min(1000 frames, half the range).
   - **Anything else backwards is a reset.** A new segment starts two steps after the old
     one, with a NaN gap marker in between.
   - **A forward jump over 1.5 × step** gets a NaN marker one step after the last sample,
     so lost data shows as a gap.

   The snapshot returns `ticks × scale_s`. So the scale is a *correction*: changing it
   re-times the whole history consistently, and it can never create a discontinuity. This
   refines ADR-0002 rule 3. Wrap, reset and gap handling happen at write time as planned.
   Only the multiplication moved to read time.
3. **The dashboard Period is per stream and session-only.** It shows the stream's
   `scale_s × step`. An edit overrides that stream only, through
   `engine.set_time_scale(key, s)`, and is highlighted until the config is reloaded.
   Persistence goes through the Configuration tab's new Time Base fields. The roadmap
   suggested the dashboard control save directly to the file. It doesn't, because the
   Configuration tab keeps its own unsaved copy of the document, and two writers would
   race and overwrite each other's edits.
4. **Resets are reported.** The engine checks each store's reset count on its 1 Hz stats
   tick. When it grows, it emits one status message naming the affected streams.

## Consequences

- Time is monotonic (non-decreasing), even across wraps and resets. The cursor readout's
  `searchsorted` is valid, and the plot never draws backwards.
- Gap markers take ring slots: one per loss event, bounded by the number of events.
- The time base is a Python loop per frame, like the router's counter tracking. A
  vectorised version cost about 40% of parse+store throughput at 900 B reads, because a
  read is only about 6 frames. The loop costs about 5% (see project log).
- After a reset, absolute time no longer equals `counter × scale`. It's continuous with
  the previous segment. That's what the plot needs. Host timestamps (R4.1) will cover
  wall-clock time.

## Alternatives considered

- **Seconds at write time** (ADR-0002's wording). Then changing the scale has two bad
  options: rewrite the whole history, which is the same cost and lock hold as a resize,
  or leave a kink at the change point. Correcting a wrong scale is the common case, so
  scale-at-read wins.
- **Guess the nominal step from the data** (for example the median difference). It fails
  on lossy links and at startup. An explicit `step` with a validation warning is
  predictable.
