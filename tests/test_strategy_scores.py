"""Tests for deterministic recursive self-improvement scorecards."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.local_output import MergeStats
from agent.mongodb import STRATEGY_SCORES_COLLECTION, get_database
from agent.report_store import build_report_document, save_run_report
from agent.strategy_scores import (
    EXPLORATION_FLOOR,
    apply_strategy_scores_for_report,
    build_venue_query_weights,
    list_strategy_scores,
    load_scores_by_key,
    score_weight,
    venue_selection_weight,
)

_FIXED_UTC = datetime(2026, 6, 18, 8, 30, 0, tzinfo=timezone.utc)


def _score_doc(score_id: str) -> dict:
    doc = get_database("test-db")[STRATEGY_SCORES_COLLECTION].find_one({"_id": score_id})
    assert doc is not None
    return doc


def test_save_run_report_persists_strategy_scorecards() -> None:
    stats = MergeStats(
        added=2,
        skipped=1,
        removed_past=0,
        removed_exclusion=0,
        removed_dedupe=0,
        removed_orphan_venues=0,
        total_after=12,
        url_outcomes={
            "https://yield.example/whats-on": (2, 3),
        },
        url_distinct_event_counts={
            "https://yield.example/whats-on": 3,
        },
        venue_outcomes={
            "venue-1": {
                "venue_id": "venue-1",
                "name": "The Bright Room",
                "events_added": 2,
                "events_seen": 3,
                "duplicates": 1,
            }
        },
    )

    report_id = save_run_report(
        "test-db",
        queries=["Gold Coast jazz gigs", "Gold Coast jazz gigs"],
        crawled_urls=[
            "https://yield.example/whats-on",
            "https://empty.example/events",
        ],
        merge_stats=stats,
        when=_FIXED_UTC,
    )

    source = _score_doc("source_url:https://yield.example/whats-on")
    assert source["events_added"] == 2
    assert source["events_seen"] == 3
    assert source["duplicates"] == 1
    assert source["zero_yield_runs"] == 0
    assert source["applied_report_ids"] == [report_id]

    empty_source = _score_doc("source_url:https://empty.example/events")
    assert empty_source["events_added"] == 0
    assert empty_source["events_seen"] == 0
    assert empty_source["zero_yield_runs"] == 1

    host = _score_doc("host:yield.example")
    assert host["events_added"] == 2
    assert host["events_seen"] == 3

    venue = _score_doc("venue:venue-1")
    assert venue["label"] == "The Bright Room"
    assert venue["events_added"] == 2
    assert venue["duplicates"] == 1

    query = _score_doc("query:gold coast jazz gigs")
    assert query["runs"] == 1
    assert query["events_added"] == 2
    assert query["events_seen"] == 3


def test_strategy_score_update_is_idempotent_for_one_report() -> None:
    stats = MergeStats(
        added=0,
        skipped=2,
        removed_past=0,
        removed_exclusion=0,
        removed_dedupe=0,
        removed_orphan_venues=0,
        total_after=4,
        url_outcomes={"https://dupes.example/events": (0, 2)},
    )
    report_doc = build_report_document(
        queries=["Repeated query"],
        crawled_urls=["https://dupes.example/events"],
        merge_stats=stats,
        when=_FIXED_UTC,
    )

    touched = apply_strategy_scores_for_report(
        "test-db",
        report_id="report-123",
        report_doc=report_doc,
        merge_stats=stats,
    )
    second_touched = apply_strategy_scores_for_report(
        "test-db",
        report_id="report-123",
        report_doc=report_doc,
        merge_stats=stats,
    )

    assert touched == 3
    assert second_touched == 0
    source = _score_doc("source_url:https://dupes.example/events")
    assert source["runs"] == 1
    assert source["events_seen"] == 2
    assert source["duplicates"] == 2
    assert source["zero_yield_runs"] == 1


def test_list_strategy_scores_returns_api_safe_documents() -> None:
    stats = MergeStats(
        added=1,
        skipped=0,
        removed_past=0,
        removed_exclusion=0,
        removed_dedupe=0,
        removed_orphan_venues=0,
        total_after=1,
        url_outcomes={"https://api.example/events": (1, 1)},
    )
    save_run_report(
        "test-db",
        queries=["API safe query"],
        crawled_urls=["https://api.example/events"],
        merge_stats=stats,
        when=_FIXED_UTC,
    )

    rows = list_strategy_scores("test-db", kind="source_url")

    assert rows[0]["id"] == "source_url:https://api.example/events"
    assert "_id" not in rows[0]
    assert rows[0]["runs"] == 1


def test_load_scores_by_key_returns_kind_indexed_documents() -> None:
    stats = MergeStats(
        added=1,
        skipped=0,
        removed_past=0,
        removed_exclusion=0,
        removed_dedupe=0,
        removed_orphan_venues=0,
        total_after=1,
        venue_outcomes={
            "venue-a": {
                "venue_id": "venue-a",
                "name": "Alpha Hall",
                "events_added": 1,
                "events_seen": 1,
                "duplicates": 0,
            }
        },
    )
    save_run_report(
        "test-db",
        queries=["venue query"],
        crawled_urls=[],
        merge_stats=stats,
        when=_FIXED_UTC,
    )

    rows = load_scores_by_key("test-db", "venue")

    assert "venue-a" in rows
    assert rows["venue-a"]["kind"] == "venue"


def test_score_weight_rewards_yield_and_penalises_zero_yield() -> None:
    high = score_weight(
        {
            "events_added": 9,
            "events_seen": 12,
            "zero_yield_runs": 0,
            "last_seen": _FIXED_UTC.isoformat(),
        },
        now=_FIXED_UTC,
    )
    low = score_weight(
        {
            "events_added": 0,
            "events_seen": 0,
            "zero_yield_runs": 3,
            "last_seen": _FIXED_UTC.isoformat(),
        },
        now=_FIXED_UTC,
    )

    assert high > low
    assert low == EXPLORATION_FLOOR


def test_score_weight_decays_with_age() -> None:
    fresh = score_weight(
        {
            "events_added": 4,
            "events_seen": 4,
            "zero_yield_runs": 0,
            "last_seen": _FIXED_UTC.isoformat(),
        },
        now=_FIXED_UTC,
    )
    stale = score_weight(
        {
            "events_added": 4,
            "events_seen": 4,
            "zero_yield_runs": 0,
            "last_seen": "2025-01-01T00:00:00+00:00",
        },
        now=_FIXED_UTC,
    )

    assert fresh > stale


def test_venue_selection_weight_boosts_unlinked_and_unmined() -> None:
    baseline = venue_selection_weight(
        {"name": "Baseline", "events_link": "https://x/events", "last_mined": "2026-06-01"},
        {},
        today=_FIXED_UTC.date(),
        now=_FIXED_UTC,
    )
    discovery = venue_selection_weight(
        {"name": "Discovery", "events_link": "", "last_mined": ""},
        {},
        today=_FIXED_UTC.date(),
        now=_FIXED_UTC,
    )

    assert discovery > baseline


def test_build_venue_query_weights_reads_persisted_scores() -> None:
    stats = MergeStats(
        added=2,
        skipped=0,
        removed_past=0,
        removed_exclusion=0,
        removed_dedupe=0,
        removed_orphan_venues=0,
        total_after=2,
        venue_outcomes={
            "venue-strong": {
                "venue_id": "venue-strong",
                "name": "Strong Room",
                "events_added": 5,
                "events_seen": 5,
                "duplicates": 0,
            }
        },
    )
    save_run_report(
        "test-db",
        queries=["Strong venue query"],
        crawled_urls=[],
        merge_stats=stats,
        when=_FIXED_UTC,
    )

    weights = build_venue_query_weights(
        "test-db",
        [
            {"_id": "venue-strong", "name": "Strong Room"},
            {"_id": "venue-weak", "name": "Weak Room"},
        ],
        today=_FIXED_UTC.date(),
        now=_FIXED_UTC,
    )

    assert weights["venue-strong"] > weights["venue-weak"]
