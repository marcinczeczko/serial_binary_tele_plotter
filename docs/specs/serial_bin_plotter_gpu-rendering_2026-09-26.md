# GPU rendering: analysis and migration plan (proposed Phase 10)

- Status: accepted as Phase 10 (2026-09-26). R10.0 measured on macOS (section 1b); the
  owner's go is still needed at the R10.2 gate.
- Relates to: ADR-0005 (render budget rules), R3.4, ADR-0012 / R9.6 (graticule and markers).

## 1. What limits rendering today (measured)

`tools/bench_render.py`, 34 signals × 100k samples at 1 kHz, same M1 Pro, 6 s runs:

| setup                                          | pull + draw | paint  | FPS            |
|------------------------------------------------|-------------|--------|----------------|
| offscreen software raster (what CI uses)       | 2.9 ms      | 8.0 ms | 30.3 (capped)  |
| native Cocoa, QPainter                         | 2.7 ms      | 9.7 ms | 30.3 (capped)  |
| native Cocoa, pyqtgraph `useOpenGL=True`       | 2.8 ms      | 6.5 ms | 30.3 (capped)  |
| native, 60 FPS cap, 2400×1400, QPainter        | 2.3 ms      | 8.7 ms | 58.8           |
| native, 60 FPS cap, 2400×1400, `useOpenGL`     | 2.4 ms      | 5.7 ms | 58.8           |

What this means:

- On this Mac the plot is no longer the bottleneck. It holds 60 FPS on the CPU raster with
  about 11 ms of a 16.7 ms frame used.
- The rules in ADR-0005 were written against numbers from before the level of detail was
  added (2.7 FPS, a 265 ms paint) and against the software raster in the container:
  - no text on the plot
  - one paint per frame
  - 1 px pens
  - major ticks only
  - live decimation
- Most of those costs are **Python and QGraphicsView overhead per item**, not pixel filling:
  - `paint()` callbacks
  - `setData` building paths
  - a second paint when a `TextItem` moves during paint
  - holding the GIL during paint
- A GPU draws the pixels. It doesn't remove that Python overhead. So a GPU backend by
  itself does *not* make those issues disappear. A different scene model does: retained GPU
  buffers, no QGraphicsItem per overlay, text drawn by the GPU.
- The real gains of a GPU backend:
  1. Headroom on weaker Windows laptops and 4K/HiDPI screens, where the CPU raster scales
     with pixel count.
  2. Wide and dashed lines at no cost. The QPainter raster slows down a lot above 1 px (P4).
  3. Text and markers drawn on the plot without the second-paint trap.
  4. Paused view: all 100k × 34 samples drawn at full resolution from buffers uploaded once.
     No re-decimation on zoom.
- **Open question for the owner:** where do you see slowness today, and on which machine?
  If it's the whole window (Signals pane readout, QSS, panes) rather than the plot, a GPU
  plot won't help. Step 0 measures that.

## 1b. R10.0: the whole window (`tools/bench_window.py`, M1 Pro, Retina 2x, 1440×900)

Same fixture, the real `MainWindow`, 6 s steady state, repeated twice (the numbers matched
within 3%):

| scenario                    | FPS  | GUI thread busy | pull + draw | plot paint     | rest of window |
|-----------------------------|------|-----------------|-------------|----------------|----------------|
| live, mouse idle            | 30.3 | 41%             | 20%         | **67%**        | 13%            |
| live, cursor sweeping 60 Hz | 30.3 | 78%             | 9%          | **61%**        | 30%            |
| 60 FPS cap, cursor sweeping | 58.8 | 82–91%          | 15%         | **57%**        | 28%            |

What this shows:

- **The plot's paint is the dominant cost: 57–67% of the GUI thread.** The profile names
  `QPainter.drawPath` for the curves as the largest single entry. That's well above R10.0's
  30% bar, so Phase 10 goes on.
- **Cursor moves repaint the whole plot.** With the mouse moving there are about 2.7 plot
  paints per live frame (463 paints for 171 frames). Each paint redraws all 34 curves and
  the graticule. A retained GPU scene makes a repaint cheap. On the raster path, coalescing
  cursor updates into the live frame fixes it (R10.7).
- **The rest of the window** (about 230–250 ms/s with the cursor moving) is mostly the
  Signals pane repainting its rows for each readout (`SignalDelegate.paint`, about 90 ms/s),
  plus the readout itself. It's not the plot's problem, but it is worth a look if the GUI
  thread is still busy once the plot is on the GPU.
- Windows (1080p and 4K) is still to be run by the owner:
  `uv run python tools/bench_window.py --cursor [--size 3840x2160]`.

## 2. Options (multi-platform: macOS and Windows)

| option                                      | macOS         | Windows       | verdict |
|---------------------------------------------|---------------|---------------|---------|
| pyqtgraph `useOpenGL=True`                  | GL 4.1 (deprecated by Apple) | vendor GL | one-line toggle, −33% paint here. Curves only; dashes and >1 px lost on the GL curve path. Opt-in quick win, not the destination |
| **fastplotlib / pygfx on wgpu**             | **Metal**     | **D3D12 / Vulkan** | the only serious native-GPU option with PyQt6 embedding (`rendercanvas.qt.QRenderWidget`): thick and dashed lines in the shader, NaN gaps, GPU text, picking, shared controllers (linked X). Pre-1.0; fastplotlib pins an exact pygfx |
| VisPy                                       | GL            | GL            | same deprecated GL on macOS, basic axes. No |
| Qt Graphs (RHI) / Qt Charts GL              | Metal / GL    | D3D / GL      | 2D is QML-only from Python, no numpy path, thin features; Qt Charts is deprecated. No |
| custom Qt Quick scene graph (`QSGGeometryNode`) | Metal     | D3D           | possible (numpy → `vertexData()` by memmove), but lines are 1 px on Metal/D3D, and there are no axes and no text: we'd be writing our own plotting library. No |

**Recommendation.** Target **pygfx** (use it directly, or through fastplotlib if the spike
shows its layout and axes fit the scope look). Keep **pyqtgraph + QPainter as the fallback
renderer**. Put `useOpenGL` behind a setting now as a cheap opt-in.

Risks to retire in the spike:

- **Present mode.** rendercanvas's default Qt present renders on the GPU, reads the frame
  back and blits it. Measure that readback against the native-surface present, which has
  known `winId()` issues.
- HiDPI and P3 colour shifts on macOS.
- Matching B612, the graticule and the T/A/B flags.
- Pre-1.0 API churn: pin versions and wrap everything behind our own seam.
- Wheels for Python 3.14: wgpu ships `py3-none` wheels for mac arm64, win_amd64/arm64 and
  manylinux; confirm at `uv add` time.
- Environments with no GPU (RDP, VMs): need an automatic fallback.

## 3. Plan

Work item IDs are proposed. One PR per item, as usual.

**R10.0 Baseline the real app (no code change to the product).**

- Add `tools/bench_window.py`: the full `MainWindow` on VIRTUAL, with the same fixture as
  `bench_render`. It reports frame time split into plot paint, rest of window, and Python
  (`cProfile` or py-spy).
- Run it on macOS and on a Windows machine, at 1080p and 4K.
- *Done when:* the log names where frame time goes. If the plot is < 30% of it, stop here
  and fix what dominates instead.

**R10.1 A renderer seam in `ui/charts`.**

- Nothing outside `ui/charts` touches pyqtgraph, but `TelemetryPlot` mixes lane logic with
  pg items. Extract a small `PlotRenderer` protocol:
  - lanes with the X range, Y range, visibility and order
  - `set_series(lane, id, t, y)` with pen, width, dash and visibility
  - vertical and horizontal lines and a region
  - graticule ticks, the marker state (T/A/B), view↔data mapping
  - mouse events out: move, click, Y drag or zoom, level drag
- The existing logic stays renderer-free: `lanes.py`, `series.py`, range modes, readout,
  anchor, trigger. `PgRenderer` is today's code moved behind the seam.
- The `qt` tests assert on `TelemetryPlot` and seam state, not on `pg.*` items. For
  example, `trigger_line()` becomes a level plus a lane.
- *Done when:* there's no behaviour change, the full `uv run pytest` passes, and
  `bench_render` matches `main` interleaved.

**R10.2 Spike: pygfx in one lane (canvas first, owner review). Go/no-go gate.**

- A throwaway `QRenderWidget` with 4 lanes: 34 lines from the live overview, NaN gaps, the
  graticule as GPU lines, T/A/B flags as GPU text in B612, and Y zoom.
- Measure frame time and present cost (bitmap versus native) on the M1 and on Windows
  (D3D12 and Vulkan).
- Screenshot next to the current look.
- *Gate:*
  - ≥ 60 FPS at 34 × 1000-bucket live on both OSes, with less GUI-thread time than
    `PgRenderer`
  - the look is accepted by the owner
  - no crashes over 10 min of connect, disconnect and profile switching

  If it fails, keep `PgRenderer` (plus the `useOpenGL` opt-in) and close Phase 10 with the
  numbers.

**R10.3 `GfxRenderer`: live view at parity.**

- Lanes as stacked viewports sharing one X controller.
- Curves as one line collection per lane, fed from preallocated float32 buffers that are
  updated in place (no reallocation per frame).
- The graticule, the time axis with the unit on its last tick, and lane names.
- The cursor A line, command markers, the trigger level line, the capture shading.
- Range modes; Y-only mouse while live.
- The lane context menu stays a `QMenu`.
- *Done when:* the `qt` tests pass against both renderers (parametrized), and
  `bench_render --renderer gfx` shows ≥ 60 FPS on the M1, with numbers logged.

**R10.4 Paused and analysis at full resolution.**

- Upload the frozen snapshot once. Zoom and pan only change the camera: no `setData`, no
  decimation.
- The Δ anchor drag, reference overlay (dashed, alpha), and step view overlays.
- *Done when:* zooming 34 × 100k paused stays ≥ 60 FPS. The readout and Δ still use
  `Interpolator` and are unchanged.

**R10.5 Lift the ADR-0005 constraints that no longer apply (GfxRenderer only).**

- Pen width and dash from `streams.json` honoured.
- Optional minor grid.
- Optional readout at the cursor on the plot.
- Supersede the render rules of ADR-0005 with ADR-0013, "GPU renderer and fallback". Don't
  edit ADR-0005.

**R10.6 Selection, fallback and CI.**

- `renderer = auto | gpu | raster` in `app_settings` plus `--renderer` on the command line.
  `auto` tries wgpu adapter creation on start-up and falls back to `PgRenderer` if it
  fails, with a message in the top bar via `_say`.
- CI:
  - Linux runs the gfx tests on lavapipe (`mesa-vulkan-drivers`), with image snapshots of
    one lane.
  - The raster path keeps the existing offscreen tests.
  - Add a Windows job for smoke tests only.
- Put the new dependencies in `pyproject.toml` as an extra (`gpu = ["pygfx", "rendercanvas",
  "wgpu"]`), so a raster-only install still works.
- Update `README.md` and `CLAUDE.md`: the render rules, commands and the new extra.

**R10.7 Cursor repaints (found by R10.0).** Coalesce cursor-line moves into the next live
frame, so a moving mouse costs no extra plot paint on the raster path.

**Cheap win to do now (independent of the gate): `useOpenGL` opt-in.**

- A hidden setting that turns on pyqtgraph's `QOpenGLWidget` viewport.
- Measured −33% paint on the M1.
- Caveats: curves drawn on GL lose dashes and widths above 1 px; test on Windows before
  offering it.

## 4. What stays unchanged

- Thread model: reader, then store, then `LiveFeed` pull. The store's level of detail
  (ADR-0005 §5) still bounds the bytes per frame. A GPU doesn't make copying 34 × 100k
  floats from Python free.
- The wire protocol, config schema and recordings are not touched.
- All rendering stays on the GUI thread. wgpu calls come from the GUI thread only, like Qt
  painting.

## 5. Rough size

- R10.1: medium (~800 lines moved, tests re-pointed).
- R10.2: small or medium, throwaway.
- R10.3 and R10.4: large (~1000 new lines: the graticule, markers, axes and interaction
  written again).
- R10.5 and R10.6: small or medium each.
