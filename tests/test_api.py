"""Tests for the HTTP API."""

from agent import venue_store
from agent.local_output import MergeStats
from agent.report_store import save_run_report
from starlette.testclient import TestClient

from agent.api import create_app


def test_get_reports_returns_saved_rows() -> None:
    save_run_report(
        "test-db",
        queries=["Gold Coast gigs"],
        crawled_urls=["https://example.com/events"],
        merge_stats=MergeStats(
            added=1,
            skipped=0,
            removed_past=0,
            removed_exclusion=0,
            removed_dedupe=0,
            total_after=1,
        ),
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/reports")

    assert response.status_code == 200
    body = response.json()
    assert len(body["reports"]) >= 1
    latest = body["reports"][0]
    assert latest["searches"] == ["Gold Coast gigs"]
    assert "example.com" in latest["urls"]
    assert latest["changes"]["added (new rows)"] == 1


def test_get_reports_unknown_db_still_resolves() -> None:
    client = TestClient(create_app())
    response = client.get("/api/unknown-db-xyz/reports")
    assert response.status_code == 200
    assert response.json()["reports"] == []


def test_get_venues_returns_paged_records() -> None:
    db = "test-db"
    venue_store.create_venue(db, "The Tivoli Theatre")
    venue_store.create_venue(db, "Fortitude Music Hall")

    client = TestClient(create_app())
    response = client.get("/api/test-db/venues?limit=50")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 50
    assert len(body["venues"]) == 2
    names = {row["name"] for row in body["venues"]}
    assert names == {"The Tivoli Theatre", "Fortitude Music Hall"}


def test_get_venues_caps_limit_at_fifty() -> None:
    client = TestClient(create_app())
    response = client.get("/api/test-db/venues?limit=999")
    assert response.status_code == 200
    assert response.json()["limit"] == 50


def test_get_venues_unknown_db_still_resolves() -> None:
    client = TestClient(create_app())
    response = client.get("/api/unknown-db-xyz/venues")
    assert response.status_code == 200
    body = response.json()
    assert body["venues"] == []
    assert body["total"] == 0


def test_get_venue_returns_raw_document() -> None:
    db = "test-db"
    created = venue_store.create_venue(db, "The Triffid")
    venue_id = str(created["_id"])

    client = TestClient(create_app())
    response = client.get(f"/api/test-db/venues/{venue_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["_id"] == venue_id
    assert body["name"] == "The Triffid"
    assert body["aliases"] == []


def test_put_venue_updates_document() -> None:
    db = "test-db"
    created = venue_store.create_venue(db, "Old Name")
    venue_id = str(created["_id"])

    client = TestClient(create_app())
    response = client.put(
        f"/api/test-db/venues/{venue_id}",
        json={"_id": venue_id, "name": "New Name", "aliases": ["Alias One"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New Name"
    assert body["aliases"] == ["Alias One"]


def test_delete_venue_reassigns_events() -> None:
    from agent.event_store import venue_to_mongo
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = "test-db"
    old_venue = venue_store.create_venue(db, "Old Venue")
    new_venue = venue_store.create_venue(db, "New Venue")
    old_id = str(old_venue["_id"])
    new_id = str(new_venue["_id"])

    get_database(db)[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-venue-delete",
            "event": "Test Band",
            "venue": venue_to_mongo("Old Venue", old_id),
            "url": "https://example.com/gig",
        }
    )

    client = TestClient(create_app())
    response = client.request(
        "DELETE",
        f"/api/test-db/venues/{old_id}",
        json={"replacementVenueId": new_id},
    )

    assert response.status_code == 200
    assert response.json()["events_updated"] == 1
    assert venue_store.get_venue(db, old_id) is None
    event = get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-venue-delete"})
    assert event is not None
    assert event["venue"]["id"] == new_id
    assert event["venue"]["name"] == "New Venue"
