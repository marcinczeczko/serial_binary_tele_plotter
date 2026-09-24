# Project log

Newest first. Keep each entry short: what changed, why, measured numbers, and links to
roadmap items (`R*`), findings (`C*/P*/A*/T*`) and ADRs. See
[ADR-0001](adr/0001-record-architecture-decisions.md) for the conventions.

---

## 2026-09-24 — Architecture review, roadmap, project records

- Reviewed the codebase at `637f7ba` in full:
  [reviews/2026-09-24-architecture-review.md](reviews/2026-09-24-architecture-review.md).
- Baseline numbers (for later comparison):
  - tests: 22 pass with `-p no:pytest-qt` (Qt stubbed); plain `pytest` crashes headless (no libEGL)
  - mypy strict: 40 errors in app code (125 including tests); ruff: clean
  - parse+store, 140 B `pid` payload: 66.8k frames/s, 9.8 MB/s; CRC-8 3.6 µs/frame
  - 5000 B read chunks: **0/20000 frames decoded** (C1, critical)
  - `get_plot_data` with 34 signals: 0.7 ms @2k, 4.6 ms @20k, 26 ms / 27 MB @100k samples per 100 ms tick
- Top issues: C1 (RX guard discards any read over 4 KiB), P1 (pyqtgraph downsampling
  configured but off), P2/P3 (full-buffer push every 100 ms with no back-pressure), C2
  (time base from UI period; reset breaks time), C3/C4 (silent zeros; editor corrupts or
  loses config).
- Added: `CLAUDE.md`, `docs/roadmap.md` (phases 0–5), ADR-0001 (record keeping),
  ADR-0002 (target pipeline, *Proposed*), and this log.
- Next: roadmap Phase 0 (parser tests reproducing C1, bench script, Qt test lane, CI), then
  R1.1–R1.3.

---

## History before this log (reconstructed from git)

| Date | Milestone |
|---|---|
| 2025-12-27 → 12-29 | First GUI, dummy data, serial detection, threaded worker ("performance best"), crosshair and legend, linter setup |
| 2025-12-30 | Worker and core refactor, UI refactor, docstrings; IMU panel, control-loop stream; in-app configuration editor replaces hand-edited `streams.json` |
| 2026-01-05 → 01-07 | New PID stream definition, two-motor PID panel, "send to both motors" command, K1–K3 gains, first performance pass |
| 2026-02-05 | Performance fixes |
| 2026-03-13 | README, reload validation, virtual-device dt, plot defaults; switched to **uv**, stricter typing, pylint/ruff fixes |
| 2026-03-16 | Package reorganisation into `core/protocol`, `core/acquisition`, `ui/{charts,panels,config,common}`; CRC lookup table, sync by `find()`, worker-side Y bounds, 10 ms serial timer; README and VS Code config for uv |
