"""
Acquisition pipeline micro-benchmark.

Measures the Qt-free hot paths so performance work can be compared before/after:
  1. parse + store throughput at a typical read size, via the engine's vectorised path
     (the binary LinkDecoder: FrameParser -> StreamRouter, then SampleStore.append_records)
     and via the per-frame dict path (ProtocolHandler -> SampleStore.append) for comparison
  2. decode ratio for large reads (regression guard for review finding C1)
  2b. the same stream printed as text lines (a text profile's TextLineDecoder, R8.3)
  3. CRC-8 cost per frame
  4. GUI pull cost for several buffer sizes, all signals or only the visible ones:
     a full-resolution snapshot (pause/analysis), the live overview (min/max level of
     detail, after one frame's worth of new samples, R3.4), and an idle tick

Usage (from the repository root):
    uv run python tools/bench_pipeline.py [--config streams.json] [--stream pid]

Record the printed numbers in docs/project-log.md when a change affects them.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.acquisition.storage import SampleStore  # noqa: E402
from core.acquisition.timebase import time_base_config  # noqa: E402
from core.config import StreamConfigLoader  # noqa: E402
from core.protocol.constants import MAGIC_0, MAGIC_1, STRUCT_TYPE_MAP  # noqa: E402
from core.protocol.crc import calculate_crc8  # noqa: E402
from core.protocol.handler import ProtocolHandler  # noqa: E402
from core.protocol.link import make_link_decoder  # noqa: E402
from core.simulation.synth import FrameSynth  # noqa: E402
from core.types import StreamConfig  # noqa: E402

N_FRAMES = 20_000
SNAPSHOT_SIZES = (2_000, 20_000, 100_000)
SNAPSHOT_REPEATS = 10
VISIBLE_SIGNALS = 6  # a typical number of traces on screen at once


def build_stream(cfg: StreamConfig, n: int) -> tuple[bytes, int]:
    """Returns n encoded frames for `cfg` and the size of one frame."""
    frame_cfg = cfg["frame"]
    endian = "<" if frame_cfg.get("endianness", "little") == "little" else ">"
    codes = [STRUCT_TYPE_MAP[f["type"]][0] for f in frame_cfg["fields"]]
    fmt = endian + "".join(codes)
    stream_id = frame_cfg.get("stream_id", 0)
    frames = []
    for i in range(n):
        values = [i] + [(i % 100) if c in "bBhHiI" else i * 0.25 for c in codes[1:]]
        payload = struct.pack(fmt, *values)
        header = bytes([MAGIC_0, MAGIC_1, stream_id, len(payload)])
        frames.append(
            header + bytes([calculate_crc8(header)]) + payload + bytes([calculate_crc8(payload)])
        )
    return b"".join(frames), len(frames[0])


def best_of(
    fn: Callable[[StreamConfig, bytes, int], tuple[int, float]],
    cfg: StreamConfig,
    blob: bytes,
    chunk: int,
    runs: int = 3,
) -> tuple[int, float]:
    """Fastest of a few runs: throughput numbers on a shared machine are noisy."""
    results = [fn(cfg, blob, chunk) for _ in range(runs)]
    return min(results, key=lambda r: r[1])


def text_stream(cfg: StreamConfig, n: int) -> tuple[StreamConfig, bytes]:
    """`cfg` as a text stream ("PID,{loop_cntr},{v1},…") and n of its simulated lines."""
    fields = cfg["frame"]["fields"]
    pattern = "PID," + ",".join(f"{{{f['name']}}}" for f in fields)
    text_cfg: StreamConfig = {**cfg, "frame": {"pattern": pattern, "fields": fields}}
    text_cfg.pop("sim", None)
    return text_cfg, FrameSynth(text_cfg, seed=0).lines(0, n)


def parse_and_store(
    cfg: StreamConfig, blob: bytes, chunk: int, fmt: str = "binary"
) -> tuple[int, float]:
    """The engine's path: the profile's LinkDecoder (R8.1) -> append_records (R2.2/R2.3)."""
    link = make_link_decoder(fmt)
    link.configure({"s": cfg})
    store = SampleStore(2_000)
    store.configure(cfg.get("signals", {}), time_base_config(cfg))
    decoded = 0
    t0 = time.perf_counter()
    for off in range(0, len(blob), chunk):
        batches = link.feed(blob[off : off + chunk])
        if "s" in batches:
            decoded += store.append_records(batches["s"])
    return decoded, time.perf_counter() - t0


def parse_and_store_dicts(cfg: StreamConfig, blob: bytes, chunk: int) -> tuple[int, float]:
    """The single-stream per-frame path (struct.unpack -> dict), for comparison."""
    handler = ProtocolHandler()
    handler.configure(cfg)
    store = SampleStore(2_000)
    store.configure(cfg.get("signals", {}), time_base_config(cfg))
    decoded = 0
    t0 = time.perf_counter()
    for off in range(0, len(blob), chunk):
        handler.add_data(blob[off : off + chunk])
        decoded += store.append(list(handler.process_available_frames()))
    return decoded, time.perf_counter() - t0


def bench_snapshot(
    cfg: StreamConfig, max_samples: int, visible: int | None
) -> tuple[float, float, float, float]:
    """
    Returns (ms per snapshot, MB per snapshot, ms per live overview, us per idle tick) for
    a full, wrapped buffer. `visible` limits the copy to the first N signals (None = all).
    """
    store = SampleStore(max_samples)
    signals = cfg.get("signals", {})
    store.configure(signals, time_base_config(cfg))
    frame = {f["name"]: 1.0 for f in cfg["frame"]["fields"]}
    batch = []
    for i in range(max_samples + 17):  # wrap once so the window is not at index 0
        frame["loop_cntr"] = float(i)
        batch.append(dict(frame))
    store.append(batch)
    del batch  # don't let the fill data skew the timed allocations
    ids = list(signals)[:visible] if visible is not None else None
    t0 = time.perf_counter()
    snap = None
    for _ in range(SNAPSHOT_REPEATS):
        snap = store.snapshot(ids, None)
    elapsed = (time.perf_counter() - t0) / SNAPSHOT_REPEATS
    assert snap is not None
    mb = (snap.time.nbytes + sum(a.nbytes for a in snap.signals.values())) / 1e6
    # Live frames: 33 new samples (1 kHz at 30 FPS), then the overview the GUI pulls.
    store.overview(ids)  # the first call summarises the whole window once
    new = [dict(frame, loop_cntr=float(max_samples + 17 + i)) for i in range(33 * 10)]
    overview_s = 0.0
    for r in range(10):
        store.append(new[r * 33 : (r + 1) * 33])
        t0 = time.perf_counter()
        store.overview(ids)
        overview_s += time.perf_counter() - t0
    version = store.version
    t0 = time.perf_counter()
    for _ in range(1000):
        store.overview(ids, version)  # nothing changed -> None
    idle_us = (time.perf_counter() - t0) / 1000 * 1e6
    return elapsed * 1e3, mb, overview_s / 10 * 1e3, idle_us


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--config", default="streams.json")
    parser.add_argument("--stream", default="pid")
    args = parser.parse_args()

    cfg = StreamConfigLoader(args.config).get_stream(args.stream)
    n_signals = len(cfg.get("signals", {}))
    blob, frame_len = build_stream(cfg, N_FRAMES)
    print(f"stream '{args.stream}': {frame_len} B/frame, {n_signals} signals, {N_FRAMES} frames")

    for label, fn in (("vectorised", parse_and_store), ("per-frame dicts", parse_and_store_dicts)):
        decoded, dt = best_of(fn, cfg, blob, 900)
        print(
            f"parse+store, {label:>15} (900 B reads): {decoded / dt:>10,.0f} frames/s  "
            f"{len(blob) / dt / 1e6:6.2f} MB/s  ({decoded}/{N_FRAMES} decoded)"
        )
    decoded, dt = best_of(parse_and_store, cfg, blob, 5_000)
    print(
        f"parse+store,      vectorised (5000 B reads): {decoded / dt:>9,.0f} frames/s  "
        f"({decoded}/{N_FRAMES} decoded)  [C1 guard; bigger reads = bigger batches]"
    )

    text_cfg, lines = text_stream(cfg, N_FRAMES)
    decoded, dt = best_of(lambda c, b, n: parse_and_store(c, b, n, "text"), text_cfg, lines, 900)
    print(
        f"parse+store,      text lines (900 B reads): {decoded / dt:>10,.0f} lines/s  "
        f"{len(lines) / dt / 1e6:6.2f} MB/s  ({decoded}/{N_FRAMES} decoded, "
        f"{len(lines) / N_FRAMES:.0f} B/line)"
    )

    payload = blob[5 : frame_len - 1]
    t0 = time.perf_counter()
    for _ in range(N_FRAMES):
        calculate_crc8(payload)
    crc_us = (time.perf_counter() - t0) / N_FRAMES * 1e6
    print(f"crc8 on {len(payload)} B payload:    {crc_us:6.2f} us")

    for size in SNAPSHOT_SIZES:
        for visible, label in ((None, f"all {n_signals}"), (VISIBLE_SIGNALS, "visible 6")):
            ms, mb, live_ms, idle_us = bench_snapshot(cfg, size, visible)
            print(
                f"snapshot {label:>9} @ {size:>7,} samples: {ms:7.2f} ms  {mb:6.1f} MB"
                f"   live overview {live_ms:5.2f} ms   idle tick {idle_us:5.1f} us"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
