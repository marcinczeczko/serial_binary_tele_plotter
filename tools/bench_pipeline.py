"""
Acquisition pipeline micro-benchmark.

Measures the Qt-free hot paths so performance work can be compared before/after:
  1. parse + store throughput (ProtocolHandler -> SignalDataManager) at a typical read size
  2. decode ratio for large reads (regression guard for review finding C1)
  3. CRC-8 cost per frame
  4. GUI snapshot cost (SignalDataManager.get_plot_data) for several buffer sizes

Usage (from the repository root):
    uv run python tools/bench_pipeline.py [--config streams.json] [--stream pid]

Record the printed numbers in docs/project-log.md when a change affects them.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.acquisition.storage import SignalDataManager  # noqa: E402
from core.config import StreamConfigLoader  # noqa: E402
from core.protocol.constants import MAGIC_0, MAGIC_1, STRUCT_TYPE_MAP  # noqa: E402
from core.protocol.crc import calculate_crc8  # noqa: E402
from core.protocol.handler import ProtocolHandler  # noqa: E402
from core.types import StreamConfig  # noqa: E402

N_FRAMES = 20_000
SNAPSHOT_SIZES = (2_000, 20_000, 100_000)
SNAPSHOT_REPEATS = 10


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


def parse_and_store(cfg: StreamConfig, blob: bytes, chunk: int) -> tuple[int, float]:
    handler = ProtocolHandler()
    handler.configure(cfg)
    store = SignalDataManager(2_000)
    store.configure(cfg.get("signals", {}))
    decoded = 0
    t0 = time.perf_counter()
    for off in range(0, len(blob), chunk):
        handler.add_data(blob[off : off + chunk])
        for frame in handler.process_available_frames():
            store.store_frame(frame)
            decoded += 1
    return decoded, time.perf_counter() - t0


def bench_snapshot(cfg: StreamConfig, max_samples: int) -> tuple[float, float]:
    """Returns (ms per get_plot_data call, MB per packet) for a full buffer."""
    store = SignalDataManager(max_samples)
    store.configure(cfg.get("signals", {}))
    frame = {f["name"]: 1.0 for f in cfg["frame"]["fields"]}
    for i in range(max_samples + 17):  # wrap once so the logical start is not index 0
        frame["loop_cntr"] = float(i)
        store.store_frame(frame)
    t0 = time.perf_counter()
    packet = None
    for _ in range(SNAPSHOT_REPEATS):
        packet = store.get_plot_data(0.001)
    elapsed = (time.perf_counter() - t0) / SNAPSHOT_REPEATS
    assert packet is not None
    mb = (packet["time"].nbytes + sum(a.nbytes for a in packet["signals"].values())) / 1e6
    return elapsed * 1e3, mb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--config", default="streams.json")
    parser.add_argument("--stream", default="pid")
    args = parser.parse_args()

    cfg = StreamConfigLoader(args.config).get_stream(args.stream)
    n_signals = len(cfg.get("signals", {}))
    blob, frame_len = build_stream(cfg, N_FRAMES)
    print(f"stream '{args.stream}': {frame_len} B/frame, {n_signals} signals, {N_FRAMES} frames")

    decoded, dt = parse_and_store(cfg, blob, 900)
    print(
        f"parse+store (900 B reads):   {decoded / dt:>10,.0f} frames/s  "
        f"{len(blob) / dt / 1e6:6.2f} MB/s  ({decoded}/{N_FRAMES} decoded)"
    )
    decoded, _ = parse_and_store(cfg, blob, 5_000)
    print(f"large reads (5000 B):        {decoded}/{N_FRAMES} decoded  [C1 guard]")

    payload = blob[5 : frame_len - 1]
    t0 = time.perf_counter()
    for _ in range(N_FRAMES):
        calculate_crc8(payload)
    crc_us = (time.perf_counter() - t0) / N_FRAMES * 1e6
    print(f"crc8 on {len(payload)} B payload:    {crc_us:6.2f} us")

    for size in SNAPSHOT_SIZES:
        ms, mb = bench_snapshot(cfg, size)
        print(f"get_plot_data @ {size:>7,} samples: {ms:7.2f} ms/tick  {mb:6.1f} MB/packet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
