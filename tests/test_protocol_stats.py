from __future__ import annotations

from typing import Any

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
    clean = make_link_report(LinkStats(bytes_rx=29_300), LinkStats(unknown_id_frames=5), 0, 1.0)
    rate, problems, tooltip = format_link_report(clean)
    assert problems == ""  # a clean link shows only its rate (R9.2)
    assert "unconfigured stream IDs: 5" in tooltip and "Header CRC errors: 0" in tooltip

    cur = LinkStats(bytes_rx=29_300, header_crc_errors=1, payload_crc_errors=2, discarded_bytes=7)
    rate, problems, _ = format_link_report(make_link_report(LinkStats(), cur, 0, 1.0))
    assert rate == "29 kB/s"
    assert problems == "3 CRC errors · 7 bytes dropped"  # only what went wrong, in words


def test_snapshot_is_independent() -> None:
    stats = LinkStats(frames_by_id={1: 2})
    snap = stats.snapshot()
    stats.frames_by_id[1] = 99
    stats.bytes_rx = 5
    assert snap.frames_by_id == {1: 2}
    assert snap.bytes_rx == 0


def test_a_text_report_shows_the_line_counters() -> None:
    cur = LinkStats(bytes_rx=1_500, lines_rx=12, frames_decoded=9, lines_unmatched=3)
    rate, problems, tooltip = format_link_report(make_link_report(LinkStats(), cur, 9, 1.0, "text"))
    assert rate == "1.5 kB/s"
    assert problems == ""  # boards print banners: unmatched lines aren't a problem
    assert "Lines received: 12" in tooltip and "Lines matching no pattern: 3" in tooltip
    assert "CRC" not in tooltip

    bad = LinkStats(lines_overlong=1)
    _, problems, _ = format_link_report(make_link_report(LinkStats(), bad, 0, 1.0, "text"))
    assert problems == "1 line too long"
    worse = LinkStats(lines_overlong=2, value_errors=1, counter_missing=4)
    _, problems, _ = format_link_report(make_link_report(LinkStats(), worse, 0, 1.0, "text"))
    assert problems == "2 lines too long · 1 bad value · 4 lost"


def test_a_report_carries_the_replies_since_the_previous_one() -> None:
    from core.protocol.text_line import TextLineDecoder

    decoder = TextLineDecoder()
    stream: Any = {
        "name": "S",
        "frame": {"pattern": "v={v}", "fields": [{"name": "v", "type": "f32"}]},
    }
    decoder.configure({"s": stream})
    decoder.feed(b"ok 1\nv=1\nok 2\n")
    first = decoder.stats.snapshot()
    report = make_link_report(LinkStats(), first, 0, 1.0, "text", decoder.replies)
    assert report["replies"] == ["ok 1", "ok 2"] and report["replies_dropped"] == 0

    decoder.feed(b"".join(b"line %d\n" % i for i in range(150)) + b"v=2\n")
    report = make_link_report(first, decoder.stats.snapshot(), 0, 1.0, "text", decoder.replies)
    assert report["replies"] == [f"line {i}" for i in range(50, 150)]  # the newest 100
    assert report["replies_dropped"] == 50

    decoder.reset()
    assert list(decoder.replies) == []
