"""Unit tests for fruitful URL memory (sources collection)."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from agent.mongodb import SOURCES_COLLECTION, get_database
from agent.source_store import (
    compute_source_weight,
    group_sources_by_host,
    is_stale_source_entry,
    normalize_source_url,
    pick_weighted_seed_url,
    prune_stale_source_urls,
    record_url_outcomes,
)

_FIXED_UTC = datetime(2026, 6, 12, 3, 0, 0, tzinfo=timezone.utc)


def test_normalize_source_url_strips_fragment_and_trailing_slash() -> None:
    assert (
        normalize_source_url("https://WWW.Example.com/gigs/?x=1#section")
        == "https://www.example.com/gigs?x=1"
    )


def test_compute_source_weight_is_sublinear() -> None:
    low = compute_source_weight(4, 8)
    high = compute_source_weight(100, 200)
    assert high > low
    # Sub-linear: 25x events should not produce 25x weight.
    assert high < low * 10


def test_is_stale_source_entry() -> None:
    assert not is_stale_source_entry({"runs_contributed": 3, "events_added": 2})
    assert is_stale_source_entry({"runs_contributed": 7, "events_added": 2})
    assert is_stale_source_entry({"runs_contributed": 4, "events_added": 0})


def test_record_url_outcomes_only_saves_multi_event_urls() -> None:
    url_listing = "https://venue.example/whats-on"
    url_single = "https://songkick.example/concerts/43168849-rampage"
    record_url_outcomes(
        "test-db",
        {
            url_listing: (2, 3),
            url_single: (1, 1),
        },
        distinct_counts={
            url_listing: 2,
            url_single: 1,
        },
        when=_FIXED_UTC,
    )

    coll = get_database("test-db")[SOURCES_COLLECTION]
    docs = list(coll.find())
    assert len(docs) == 1
    doc = docs[0]
    assert doc["host"] == "venue.example"
    assert len(doc["urls"]) == 1
    assert doc["urls"][0]["url"] == url_listing


def test_record_url_outcomes_groups_by_host() -> None:
    url_a = "https://venue.example/whats-on"
    url_b = "https://venue.example/gigs"
    record_url_outcomes(
        "test-db",
        {url_a: (2, 3), url_b: (1, 1)},
        distinct_counts={url_a: 2, url_b: 2},
        when=_FIXED_UTC,
    )
    # Single-event revisit is ignored — counters must not change.
    record_url_outcomes(
        "test-db",
        {url_a: (1, 1)},
        distinct_counts={url_a: 1},
        when=_FIXED_UTC,
    )

    coll = get_database("test-db")[SOURCES_COLLECTION]
    docs = list(coll.find())
    assert len(docs) == 1
    doc = docs[0]
    assert doc["host"] == "venue.example"
    assert len(doc["urls"]) == 2

    by_url = {entry["url"]: entry for entry in doc["urls"]}
    assert by_url[url_a]["events_added"] == 2
    assert by_url[url_a]["events_seen"] == 3
    assert by_url[url_a]["runs_contributed"] == 1
    assert by_url[url_b]["events_added"] == 1
    assert by_url[url_b]["events_seen"] == 1


def test_record_url_outcomes_separate_hosts() -> None:
    record_url_outcomes(
        "test-db",
        {
            "https://a.example/one": (1, 1),
            "https://b.example/two": (2, 2),
        },
        distinct_counts={
            "https://a.example/one": 1,
            "https://b.example/two": 2,
        },
        when=_FIXED_UTC,
    )
    coll = get_database("test-db")[SOURCES_COLLECTION]
    assert coll.count_documents({}) == 1
    assert coll.find_one({"host": "b.example"})["urls"][0]["url"] == "https://b.example/two"


def test_prune_stale_url_after_update() -> None:
    url = "https://stale.example/listings"
    coll = get_database("test-db")[SOURCES_COLLECTION]
    coll.insert_one(
        {
            "host": "stale.example",
            "first_seen": _FIXED_UTC.isoformat(),
            "last_seen": _FIXED_UTC.isoformat(),
            "urls": [
                {
                    "url": url,
                    "events_added": 2,
                    "events_seen": 8,
                    "runs_contributed": 6,
                    "first_seen": _FIXED_UTC.isoformat(),
                    "last_seen": _FIXED_UTC.isoformat(),
                }
            ],
        }
    )

    record_url_outcomes(
        "test-db",
        {url: (0, 2)},
        distinct_counts={url: 2},
        when=_FIXED_UTC,
    )

    assert coll.find_one({"host": "stale.example"}) is None


def test_group_sources_by_host() -> None:
    docs = [
        {
            "host": "a.example",
            "urls": [
                {"url": "https://a.example/one", "events_seen": 1},
                {"url": "https://a.example/two", "events_seen": 2},
            ],
        },
        {
            "host": "b.example",
            "urls": [{"url": "https://b.example/x", "events_seen": 1}],
        },
    ]
    grouped = group_sources_by_host(docs)
    assert len(grouped["a.example"]) == 2
    assert len(grouped["b.example"]) == 1


def test_pick_weighted_seed_url_prefers_higher_yield() -> None:
    coll = get_database("test-db")[SOURCES_COLLECTION]
    coll.insert_many(
        [
            {
                "host": "low.example",
                "first_seen": _FIXED_UTC.isoformat(),
                "last_seen": _FIXED_UTC.isoformat(),
                "urls": [
                    {
                        "url": "https://low.example/events",
                        "events_added": 1,
                        "events_seen": 1,
                        "runs_contributed": 1,
                        "first_seen": _FIXED_UTC.isoformat(),
                        "last_seen": _FIXED_UTC.isoformat(),
                    }
                ],
            },
            {
                "host": "high.example",
                "first_seen": _FIXED_UTC.isoformat(),
                "last_seen": _FIXED_UTC.isoformat(),
                "urls": [
                    {
                        "url": "https://high.example/events",
                        "events_added": 50,
                        "events_seen": 80,
                        "runs_contributed": 1,
                        "first_seen": _FIXED_UTC.isoformat(),
                        "last_seen": _FIXED_UTC.isoformat(),
                    }
                ],
            },
        ]
    )

    rng = random.Random(0)
    picks = {
        pick_weighted_seed_url("test-db", rng=rng)
        for _ in range(40)
    }
    assert "https://high.example/events" in picks

    host_doc = coll.find_one({"host": "high.example"})
    assert host_doc is not None
    entry = host_doc["urls"][0]
    assert entry["url"] == "https://high.example/events"
    assert "last_picked" in entry


def test_pick_weighted_seed_url_returns_none_when_empty() -> None:
    assert pick_weighted_seed_url("test-db") is None
