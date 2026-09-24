# ADR-0005: Plot lanes, per-lane Y ranges, and a live level of detail

- Status: Accepted (2026-09-24, Phase 3: R3.1-R3.4)
- Date: 2026-09-24
- Addresses: review findings A4 and P7 ([review](../reviews/2026-09-24-architecture-review.md)),
  and the render budget left open by [ADR-0002](0002-target-acquisition-pipeline.md)

## Context

- Every signal shared one auto-ranged Y axis with zero forced in (A4). PWM in the hundreds
  flattened speeds around 0.3 and errors around 0.01.
- Live frames reset any user zoom every 200 ms. Y bounds covered the whole buffer, and
  every raw mouse event re-rendered the readout (P7).
- Drawing 34 signals × 100k samples cost 112 ms per tick plus 265 ms of paint, about
  2.7 FPS. Rendering had become the dominant GUI cost after Phase 2.
- The bundled `streams.json` already had `groups` (label, order), plus `group` and
  `y_range` per signal, for the IMU. The app ignored them.

## Decision

1. **Lanes are groups.** The lane layout reuses the existing keys rather than adding new ones.
   - `signals[*].group` names a signal's lane. Without one, a signal goes in the default lane.
   - `groups.<id>` describes a lane: `label` (the Y axis title), `order`, and `y_range`
     (`mode`: `auto | auto-grow | manual`, plus `min`, `max`, `include_zero`).
   - A lane shows only while one of its signals is visible.
   - The lanes are stacked `PlotItem`s with linked X and one fixed left-axis width, so
     time lines up across lanes.
   - Problems in any of these keys are warnings: they only affect display.
2. **Range modes per lane.**
   - `auto` fits the visible signals in view. Live, that's the whole window; paused, it's
     only the samples inside the zoomed X range.
   - `auto-grow` only ever widens.
   - `manual` holds its range.
   - Zero is no longer forced in; `include_zero` is an option.
   - Dragging or zooming a lane's Y switches that lane to manual, so live frames don't
     undo it. The lane's context menu switches modes and toggles "Include zero".
   - Live, X follows the newest data, and the mouse zooms and pans Y only.
3. **Moving signals between lanes.** Each row of the Signals panel has a lane selector,
   including "New lane", for the session. The Configuration tab's Lane column saves
   `signals[*].group`. This replaces the roadmap's "drag between lanes": a selector
   needed no custom drag-and-drop on pyqtgraph items, and it's testable.
4. **Cursor (R3.3).**
   - Mouse moves go through `pg.SignalProxy(rateLimit=60)`.
   - Each lane shows the readout for its own signals; the top lane also shows T and Δt.
   - Paused, a click drops a Δ anchor that can be dragged. Every lane's anchor moves together.
   - Interpolation is gap-aware: next to a gap marker it reads "n/a", never a blend
     across the gap.
5. **Live level of detail (R3.4).** `SampleStore` keeps an incremental min/max summary of
   its ring (`core/acquisition/lod.py`).
   - Rows are grouped by absolute row number into buckets of `k = capacity / 1000` rows.
     Each bucket keeps its min, max, whether it holds a NaN, and its first time.
   - It's brought up to date lazily, when a frame reads it (`overview()`): about 33 rows
     per frame at 1 kHz. So the reader thread pays nothing, and parse+store stays at 113k
     frames/s.
   - A frame is about 1000 buckets × 3 points per signal (min, max, then max again or NaN
     for a gap), whatever the capacity. The live readout asks the store for exact values
     (`values_at`), and pausing still freezes every sample.
6. **One paint per frame.** Three pyqtgraph behaviours each caused a second paint per
   live frame, and are now avoided:
   - Empty HUD `TextItem`s are hidden.
   - The idle cursor lines are hidden.
   - The view matrix is applied right after setting ranges, not during paint.

   Also: the grid draws at major ticks only (all three levels cost ~70 ms of paint), and
   pyqtgraph's own downsampling and clipping are only on while paused. `LiveFeed`'s timer is
   precise, and only restarted when its interval actually changes.

## Consequences

- **R3.4 passes** (`tools/bench_render.py`, offscreen software raster): the full fixture
  (34 signals × 100k samples at 1 kHz) runs at **30.1 FPS** with 0 bytes lost.
  - Pull + draw: 6–7 ms per frame. Paint: 22 ms.
  - Six visible signals: 30.3 FPS.
- The oldest bucket of a live window may include up to `k - 1` samples the ring already
  dropped. That's at most one pixel column at the left edge.
- Lanes change what `configure_signals` builds. `TelemetryPlot.plot` is now the top shown
  lane's `PlotItem`.
- The stubbed-Qt plot tests were replaced:
  - pure tests of the lane layout, range policy, decimation and interpolation
  - real-Qt lane tests

## Alternatives considered

- **Decimate the snapshot on the GUI thread every frame.** Tried first: about 15 ms of
  copying and reductions per frame, plus page faults from fresh 27 MB arrays.
- **Move that work to a worker thread.** Tried second, and it was worse: 8 FPS. PyQt
  holds the GIL while Qt paints and pyqtgraph calls back into Python, so the worker only
  ran between paints.
- **Keep lane plots out of the scene while hidden.** A removed `PlotItem` could outlive its
  scene, and destroying it later crashed Qt (seen after a failed test). Hidden lanes stay
  in the layout and collapse to about 2 px instead.
- **An OpenGL viewport.** Not testable headless, and fragile across drivers. It's not
  needed at the measured budget.
