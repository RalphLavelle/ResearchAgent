"""Tests for one-shot schema migrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from agent import venue_store
from agent.event_store import load_existing_rows
from agent.mongodb import EVENTS_COLLECTION, VENUES_COLLECTION, get_database
from agent.migrations_runner import run_pending_migrations_for_db

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_migration(stem: str):
    path = _REPO_ROOT / "migrations" / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remove_poster_url_migration() -> None:
    db = "test-db"
    get_database(db)[EVENTS_COLLECTION].insert_one(
        {"_id": "evt-1", "event": "Band", "poster_url": "https://example.com/poster.jpg"}
    )

    stats = _load_migration("001_remove_poster_url").run(db)

    doc = get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-1"})
    assert doc is not None
    assert "poster_url" not in doc
    assert stats["events_updated"] == 1


def test_move_location_to_venues_migration() -> None:
    db = "test-db"
    get_database(db)[VENUES_COLLECTION].insert_one(
        {
            "_id": "venue-1",
            "name": "The Tivoli Theatre",
            "aliases": [],
        }
    )
    get_database(db)[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-1",
            "event": "Band",
            "venue": {"name": "The Tivoli Theatre", "id": "venue-1"},
            "location": "Brisbane",
        }
    )

    stats = _load_migration("002_move_location_to_venues").run(db)

    event = get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-1"})
    venue = venue_store.get_venue(db, "venue-1")
    assert event is not None
    assert "location" not in event
    assert venue is not None
    assert venue["location"] == "Brisbane"
    assert stats["venues_updated"] == 1


def test_runner_applies_each_migration_once() -> None:
    db = "test-db"
    get_database(db)[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-1",
            "event": "Band",
            "venue": {"name": "Hall", "id": "venue-1"},
            "location": "Gold Coast",
            "poster_url": "https://example.com/p.jpg",
        }
    )
    get_database(db)[VENUES_COLLECTION].insert_one(
        {"_id": "venue-1", "name": "Hall", "aliases": []}
    )

    first = run_pending_migrations_for_db(db)
    second = run_pending_migrations_for_db(db)

    assert len(first) == 3
    assert second == []
    event = get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-1"})
    assert event is not None
    assert "poster_url" not in event
    assert "location" not in event
    assert venue_store.get_venue(db, "venue-1")["location"] == "Gold Coast"


def test_load_existing_rows_hydrates_location_from_venue() -> None:
    db = "test-db"
    get_database(db)[VENUES_COLLECTION].insert_one(
        {
            "_id": "venue-abc",
            "name": "The Tivoli Theatre",
            "aliases": [],
            "location": "Brisbane",
        }
    )
    get_database(db)[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-1",
            "event": "The Beths",
            "venue": {"name": "The Tivoli Theatre", "id": "venue-abc"},
            "url": "https://example.com/beths",
        }
    )

    rows = load_existing_rows(db)
    row = rows["evt-1"]
    assert row[2] == "Brisbane"


def test_add_event_tags_migration() -> None:
    db = "test-db"
    get_database(db)[EVENTS_COLLECTION].insert_one({"_id": "evt-1", "event": "Band"})

    stats = _load_migration("003_add_event_tags").run(db)

    doc = get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-1"})
    assert doc is not None
    assert doc["tags"] == []
    assert stats["events_updated"] == 1


def test_event_tags_roundtrip_through_save() -> None:
    from datetime import date

    from agent.event_store import IDX_TAGS, save_existing_rows

    db = "test-db"
    row = [
        "The Beths",
        "The Tivoli Theatre",
        "Brisbane",
        date(2026, 5, 8),
        "https://example.com/beths",
        "",
        "",
        "",
        "2026-05-01",
        "evt-1",
        "venue-abc",
        ["indie", "rock"],
    ]
    save_existing_rows(db, {"evt-1": row})

    rows = load_existing_rows(db)
    assert rows["evt-1"][IDX_TAGS] == ["indie", "rock"]
    doc = get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-1"})
    assert doc is not None
    assert doc["tags"] == ["indie", "rock"]
