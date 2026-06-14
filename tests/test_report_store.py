"""Unit tests for MongoDB run reports."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.local_output import MergeStats
from agent.report_store import (
    build_report_document,
    group_urls_by_host,
    list_reports,
    merge_stats_to_changes,
    save_run_report,
)

_FIXED_UTC = datetime(2026, 5, 25, 11, 42, 24, tzinfo=timezone.utc)

_SAMPLE_STATS = MergeStats(
    added=19,
    skipped=0,
    removed_past=4,
    removed_exclusion=2,
    removed_dedupe=1,
    total_after=27,
)


def test_group_urls_by_host_buckets_correctly() -> None:
    urls = [
        "https://hota.com.au/whats-on/live",
        "https://hota.com.au/whats-on/live/event/123",
        "https://qso.com.au/events",
    ]
    grouped = group_urls_by_host(urls)
    assert sorted(grouped.keys()) == ["hota.com.au", "qso.com.au"]
    assert len(grouped["hota.com.au"]) == 2


def test_merge_stats_to_changes_uses_task_labels() -> None:
    changes = merge_stats_to_changes(_SAMPLE_STATS)
    assert changes["added (new rows)"] == 19
    assert changes["skipped as duplicate"] == 0
    assert changes["total rows after merge"] == 27


def test_build_report_document_shape() -> None:
    doc = build_report_document(
        queries=["Brisbane jazz May 2026"],
        crawled_urls=["https://www.miamimarketta.com/ticketed-events"],
        merge_stats=_SAMPLE_STATS,
        diagnostics={"planner": "Planner: API rate limit exceeded — 429"},
        when=_FIXED_UTC,
    )
    assert doc["datetime"] == "2026-05-25T11:42:24+00:00"
    assert doc["searches"] == ["Brisbane jazz May 2026"]
    assert doc["urls"] == {
        "www.miamimarketta.com": ["https://www.miamimarketta.com/ticketed-events"]
    }
    assert doc["changes"]["added (new rows)"] == 19
    assert doc["diagnostics"]["planner"].startswith("Planner:")


def test_build_report_document_includes_memory_seed() -> None:
    doc = build_report_document(
        queries=["q"],
        crawled_urls=[],
        memory_seed="https://venue.example/whats-on",
    )
    assert doc["memory_seed"] == "https://venue.example/whats-on"


def test_build_report_document_omits_empty_diagnostics() -> None:
    doc = build_report_document(
        queries=[],
        crawled_urls=[],
        diagnostics={},
    )
    assert "diagnostics" not in doc


def test_save_and_list_reports_roundtrip() -> None:
    report_id = save_run_report(
        "test-db",
        queries=["q1", "q2"],
        crawled_urls=[
            "https://www.star.com.au/goldcoast/whats-on/entertainment/live-concerts",
            "https://www.miamimarketta.com/",
        ],
        merge_stats=_SAMPLE_STATS,
        when=_FIXED_UTC,
    )
    assert report_id

    reports = list_reports("test-db")
    assert len(reports) == 1
    row = reports[0]
    assert row["id"] == report_id
    assert row["searches"] == ["q1", "q2"]
    assert "www.star.com.au" in row["urls"]
    assert row["changes"]["skipped as duplicate"] == 0
