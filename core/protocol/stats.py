"""
Link statistics for the receive path.

Every byte and frame the parser sees is accounted for here, so a flat or noisy plot can be
traced to its cause (baud mismatch, wrong stream_id, layout mismatch, dropped frames)
instead of failing silently. Pure Python, no Qt: the engine snapshots it periodically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TypedDict


@dataclass
class LinkStats:
    """Cumulative receive-path counters since the last reset."""

    bytes_rx: int = 0
    """Raw bytes handed to the parser."""
    frames_by_id: dict[int, int] = field(default_factory=dict)
    """Frames with valid header and payload CRC, per stream ID (any ID)."""
    frames_decoded: int = 0
    """Frames decoded into at least one stream."""
    unknown_id_frames: int = 0
    """Valid frames whose stream ID no configured stream uses."""
    header_crc_errors: int = 0
    payload_crc_errors: int = 0
    size_mismatches: int = 0
    """Frames of a configured stream ID whose LEN matches none of its layouts."""
    discarded_bytes: int = 0
    """Bytes skipped while searching for sync (garbage, corrupted headers)."""
    counter_gaps: int = 0
    """Times loop_cntr jumped forward by more than 1 (frames lost upstream or by CRC)."""
    counter_missing: int = 0
    """Total loop_cntr values skipped across all gaps."""
    counter_resets: int = 0
    """Times loop_cntr went backwards or repeated (MCU reset or u32 wrap)."""
    # Text profiles (R8.3). There `frames_decoded` counts matched lines, and the counter
    # fields above track the stream's integer time slot, if it has one.
    lines_rx: int = 0
    """Complete non-empty lines seen."""
    lines_unmatched: int = 0
    """Lines no stream's pattern matches (boot banners, debug prints, mangled bytes)."""
    lines_overlong: int = 0
    """Lines longer than MAX_LINE_BYTES, dropped."""
    value_errors: int = 0
    """Matched lines dropped because a value doesn't fit its field (1.5 in an integer)."""
    last_lines: dict[str, str] = field(default_factory=dict)
    """The newest line each text stream matched: the editor's Line view shows it (R8.4)."""
    last_unmatched: str = ""
    """The newest line no pattern matched (most likely the one being fixed)."""

    def snapshot(self) -> LinkStats:
        return replace(self, frames_by_id=dict(self.frames_by_id), last_lines=dict(self.last_lines))

    @property
    def errors(self) -> int:
        return self.header_crc_errors + self.payload_crc_errors + self.size_mismatches


class LinkReport(TypedDict):
    """Periodic summary the engine emits to the GUI (small; safe to send across threads)."""

    format: str
    """The decoder's wire format (`binary`, `text`): which counters apply."""
    bytes_per_s: float
    samples_per_s: float
    bytes_rx: int
    frames_decoded: int
    header_crc_errors: int
    payload_crc_errors: int
    size_mismatches: int
    unknown_id_frames: int
    discarded_bytes: int
    counter_gaps: int
    counter_missing: int
    counter_resets: int
    lines_rx: int
    lines_unmatched: int
    lines_overlong: int
    value_errors: int
    last_lines: dict[str, str]
    last_unmatched: str
    replies: list[str]
    """Unmatched lines since the previous report, oldest first (the terminal's, R8.5)."""
    replies_dropped: int
    """Unmatched lines since the previous report that `replies` doesn't hold."""


MAX_REPLIES_PER_REPORT = 100


def make_link_report(
    prev: LinkStats,
    cur: LinkStats,
    samples_delta: int,
    dt_s: float,
    fmt: str = "binary",
    recent_unmatched: Sequence[str] = (),
) -> LinkReport:
    """
    Builds a report with rates computed over the interval since `prev`. `recent_unmatched`
    is the decoder's ring of the newest unmatched lines (text profiles), from which the
    report takes those since `prev`, at most MAX_REPLIES_PER_REPORT.
    """
    dt = max(dt_s, 1e-6)
    new = max(0, cur.lines_unmatched - prev.lines_unmatched)
    available = list(recent_unmatched)[-new:] if new else []
    replies = available[-MAX_REPLIES_PER_REPORT:]
    return {
        "format": fmt,
        "bytes_per_s": (cur.bytes_rx - prev.bytes_rx) / dt,
        "samples_per_s": samples_delta / dt,
        "bytes_rx": cur.bytes_rx,
        "frames_decoded": cur.frames_decoded,
        "header_crc_errors": cur.header_crc_errors,
        "payload_crc_errors": cur.payload_crc_errors,
        "size_mismatches": cur.size_mismatches,
        "unknown_id_frames": cur.unknown_id_frames,
        "discarded_bytes": cur.discarded_bytes,
        "counter_gaps": cur.counter_gaps,
        "counter_missing": cur.counter_missing,
        "counter_resets": cur.counter_resets,
        "lines_rx": cur.lines_rx,
        "lines_unmatched": cur.lines_unmatched,
        "lines_overlong": cur.lines_overlong,
        "value_errors": cur.value_errors,
        "last_lines": dict(cur.last_lines),
        "last_unmatched": cur.last_unmatched,
        "replies": replies,
        "replies_dropped": new - len(replies),
    }


def _rate(report: LinkReport) -> str:
    kb = report["bytes_per_s"] / 1000
    return f"{kb:.0f} kB/s" if kb >= 10 else f"{kb:.1f} kB/s"


def _problems(counts: list[tuple[str, str, int]]) -> str:
    """Only what went wrong, in words: `3 CRC errors · 12 lost` (empty when nothing did)."""
    return " · ".join(f"{n} {one if n == 1 else many}" for one, many, n in counts if n)


def format_link_report(report: LinkReport) -> tuple[str, str, str]:
    """
    (rate, problems, tooltip) for the top bar's link-health item (R9.2): the byte rate,
    the non-zero drop counters in words ("" when the link is clean), and the full
    breakdown. Nothing dropped is hidden: every counter is in the tooltip (C13).
    """
    if report["format"] == "text":
        return _format_text_report(report)
    crc = report["header_crc_errors"] + report["payload_crc_errors"]
    # Unconfigured stream IDs aren't flagged: the MCU may send streams nobody plots.
    problems = _problems(
        [
            ("CRC error", "CRC errors", crc),
            ("size mismatch", "size mismatches", report["size_mismatches"]),
            ("byte dropped", "bytes dropped", report["discarded_bytes"]),
            ("lost", "lost", report["counter_missing"]),
            ("reset", "resets", report["counter_resets"]),
        ]
    )
    tooltip = "\n".join(
        [
            f"{report['samples_per_s']:.0f} samples/s",
            f"Bytes received: {report['bytes_rx']}",
            f"Frames decoded: {report['frames_decoded']}",
            f"Header CRC errors: {report['header_crc_errors']}",
            f"Payload CRC errors: {report['payload_crc_errors']}",
            f"Size mismatches (LEN != frame layout): {report['size_mismatches']}",
            f"Frames with unconfigured stream IDs: {report['unknown_id_frames']}",
            f"Bytes discarded while syncing: {report['discarded_bytes']}",
            f"loop_cntr gaps: {report['counter_gaps']} ({report['counter_missing']} missing)",
            f"loop_cntr resets/wraps: {report['counter_resets']}",
        ]
    )
    return _rate(report), problems, tooltip


def _format_text_report(report: LinkReport) -> tuple[str, str, str]:
    # Unmatched lines aren't flagged: boards print banners and debug lines nobody plots.
    problems = _problems(
        [
            ("line too long", "lines too long", report["lines_overlong"]),
            ("bad value", "bad values", report["value_errors"]),
            ("lost", "lost", report["counter_missing"]),
            ("reset", "resets", report["counter_resets"]),
        ]
    )
    tooltip = "\n".join(
        [
            f"{report['samples_per_s']:.0f} samples/s",
            f"Bytes received: {report['bytes_rx']}",
            f"Lines received: {report['lines_rx']}",
            f"Lines decoded: {report['frames_decoded']}",
            f"Lines matching no pattern: {report['lines_unmatched']}",
            f"Lines too long (dropped): {report['lines_overlong']}",
            f"Lines with a value that doesn't fit its field: {report['value_errors']}",
            f"Counter gaps: {report['counter_gaps']} ({report['counter_missing']} missing)",
            f"Counter resets/wraps: {report['counter_resets']}",
        ]
    )
    return _rate(report), problems, tooltip
