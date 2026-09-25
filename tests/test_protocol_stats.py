from __future__ import annotations

from core.protocol.stats import LinkStats, format_link_report, make_link_report


def test_report_rates_and_totals() -> None:
    prev = LinkStats(bytes_rx=1_000)
    cur = LinkStats(bytes_rx=3_000, frames_decoded=40, payload_crc_errors=2, counter_missing=3)
    report = make_link_report(prev, cur, samples_delta=100, dt_s=2.0)

    assert report["bytes_per_s"] == 1_000.0
    assert report["samples_per_s"] == 50.0
    assert report["frames_decoded"] == 40
    assert report["payload_crc_errors"] == 2


def test_format_flags_problems_but_not_other_stream_ids() -> None:
    clean = make_link_report(LinkStats(), LinkStats(unknown_id_frames=5), 0, 1.0)
    text, tooltip, problems = format_link_report(clean)
    assert not problems
    assert "CRC err 0" in text
    assert "unconfigured stream IDs: 5" in tooltip

    bad = make_link_report(LinkStats(), LinkStats(header_crc_errors=1, discarded_bytes=7), 0, 1.0)
    text, _, problems = format_link_report(bad)
    assert problems
    assert "CRC err 1" in text
    assert "dropped 7 B" in text


def test_snapshot_is_independent() -> None:
    stats = LinkStats(frames_by_id={1: 2})
    snap = stats.snapshot()
    stats.frames_by_id[1] = 99
    stats.bytes_rx = 5
    assert snap.frames_by_id == {1: 2}
    assert snap.bytes_rx == 0


def test_a_text_report_shows_the_line_counters() -> None:
    cur = LinkStats(lines_rx=12, frames_decoded=9, lines_unmatched=3)
    text, tooltip, problems = format_link_report(make_link_report(LinkStats(), cur, 9, 1.0, "text"))
    assert not problems  # boards print banners: unmatched lines aren't a problem
    assert "unmatched 3" in text and "CRC" not in text
    assert "Lines received: 12" in tooltip and "Lines decoded: 9" in tooltip

    bad = LinkStats(lines_overlong=1)
    _, _, problems = format_link_report(make_link_report(LinkStats(), bad, 0, 1.0, "text"))
    assert problems
