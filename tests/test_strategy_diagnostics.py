"""Tests for read-only recursive self-improvement diagnostics."""

from __future__ import annotations

from datetime import date, datetime, timezone

from agent.local_output import MergeStats
from agent.mongodb import SOURCES_COLLECTION, get_database
from agent.report_store import save_run_report
from agent.strategy_diagnostics import (
    StrategyDiagnostics,
    format_strategy_diagnostics,
    low_yield_hosts,
    remembered_sources,
    repeated_queries,
    venue_coverage,
)
from agent.venue_store import create_venue, set_last_event_date, set_venue_web_fields

_FIXED_UTC = datetime(2026, 6, 12, 3, 0, 0, tzinfo=timezone.utc)


def test_remembered_sources_rank_by_weight() -> None:
    coll = get_database("test-db")[SOURCES_COLLECTION]
    coll.insert_many(
        [
            {
                "host": "small.example",
                "last_seen": _FIXED_UTC.isoformat(),
                "urls": [
                    {
                        "url": "https://small.example/gigs",
                        "events_added": 1,
                        "events_seen": 2,
                        "runs_contributed": 1,
                        "last_seen": _FIXED_UTC.isoformat(),
                    }
                ],
            },
            {
                "host": "big.example",
                "last_seen": _FIXED_UTC.isoformat(),
                "urls": [
                    {
                        "url": "https://big.example/whats-on",
                        "events_added": 9,
                        "events_seen": 12,
                        "runs_contributed": 2,
                        "last_seen": _FIXED_UTC.isoformat(),
                    }
                ],
            },
        ]
    )

    rows = remembered_sources("test-db")

    assert rows[0].host == "big.example"
    assert rows[0].url == "https://big.example/whats-on"
    assert rows[0].weight > rows[1].weight


def test_venue_coverage_puts_missing_and_soonest_dates_first() -> None:
    missing = create_venue("test-db", "Missing Date Hall")
    soon = create_venue("test-db", "Soon Venue")
    later = create_venue("test-db", "Later Venue")

    set_last_event_date("test-db", soon["_id"], "2026-06-15")
    set_last_event_date("test-db", later["_id"], "2026-08-01")
    set_venue_web_fields(
        "test-db",
        soon["_id"],
        website="https://soon.example",
        events_link="https://soon.example/events",
        checked_iso=_FIXED_UTC.isoformat(),
    )

    rows = venue_coverage("test-db", today=date(2026, 6, 12))

    assert [row.name for row in rows[:3]] == [
        "Missing Date Hall",
        "Soon Venue",
        "Later Venue",
    ]
    assert rows[0].venue_id == missing["_id"]
    assert rows[1].days_until_last_event == 3
    assert rows[1].has_events_link is True


def test_low_yield_hosts_compares_reports_with_source_memory() -> None:
    save_run_report(
        "test-db",
        queries=["q"],
        crawled_urls=[
            "https://empty.example/events",
            "https://empty.example/events/page/2",
            "https://fruitful.example/whats-on",
        ],
        merge_stats=MergeStats(
            added=0,
            skipped=0,
            removed_past=0,
            removed_exclusion=0,
            removed_dedupe=0,
            removed_orphan_venues=0,
            total_after=0,
        ),
        when=_FIXED_UTC,
    )
    coll = get_database("test-db")[SOURCES_COLLECTION]
    coll.insert_one(
        {
            "host": "fruitful.example",
            "last_seen": _FIXED_UTC.isoformat(),
            "urls": [
                {
                    "url": "https://fruitful.example/whats-on",
                    "events_added": 5,
                    "events_seen": 8,
                    "runs_contributed": 1,
                    "last_seen": _FIXED_UTC.isoformat(),
                }
            ],
        }
    )

    rows = low_yield_hosts("test-db")

    assert rows[0].host == "empty.example"
    assert rows[0].crawled_urls == 2
    assert rows[0].source_events_added == 0


def test_repeated_queries_counts_case_insensitive_repeats() -> None:
    stats = MergeStats(
        added=0,
        skipped=0,
        removed_past=0,
        removed_exclusion=0,
        removed_dedupe=0,
        removed_orphan_venues=0,
        total_after=0,
    )
    save_run_report(
        "test-db",
        queries=["Brisbane jazz tonight", "Gold Coast gigs"],
        crawled_urls=[],
        merge_stats=stats,
    )
    save_run_report(
        "test-db",
        queries=["brisbane   jazz tonight", "Different query"],
        crawled_urls=[],
        merge_stats=stats,
    )

    rows = repeated_queries("test-db")

    assert len(rows) == 1
    assert rows[0].query == "brisbane jazz tonight"
    assert rows[0].count == 2


def test_format_strategy_diagnostics_includes_sections() -> None:
    text = format_strategy_diagnostics(
        StrategyDiagnostics(
            remembered_sources=[],
            venue_coverage=[],
            low_yield_hosts=[],
            repeated_queries=[],
        )
    )

    assert "Top remembered source URLs" in text
    assert "Venues with weakest future coverage" in text
    assert "Recently crawled low-yield hosts" in text
    assert "Repeated recent search queries" in text
