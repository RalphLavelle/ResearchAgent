"""Tests for the HTTP API."""

import pytest

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


def test_get_events_spotlight_only_returns_cached_posters() -> None:
    from agent.event_store import save_existing_rows
    from agent.mongodb import EVENTS_COLLECTION, IMAGES_COLLECTION, get_database

    get_database("test-db")[IMAGES_COLLECTION].insert_one(
        {
            "_id": "poster.jpg",
            "source_url": "https://cdn.example.com/poster.jpg",
            "content_type": "image/jpeg",
            "data": b"fake",
        }
    )
    with_poster = [
        "Band With Poster",
        "The Venue",
        "",
        __import__("datetime").date(2099, 6, 1),
        "https://example.com/with-poster",
        "",
        "https://cdn.example.com/poster.jpg",
        "",
        "2026-06-01",
        "evt-poster",
        "",
    ]
    without_poster = [
        "Band No Poster",
        "The Venue",
        "",
        __import__("datetime").date(2099, 6, 2),
        "https://example.com/no-poster",
        "",
        "",
        "",
        "2026-06-01",
        "evt-plain",
        "",
    ]
    save_existing_rows("test-db", {"evt-poster": with_poster, "evt-plain": without_poster})

    client = TestClient(create_app())
    response = client.get("/api/test-db/events/spotlight?limit=4")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == "evt-poster"
    assert events[0]["thumbnailUrl"] == "/api/test-db/images/poster.jpg"


def test_get_events_spotlight_respects_exclude() -> None:
    from agent.mongodb import EVENTS_COLLECTION, get_database

    coll = get_database("test-db")[EVENTS_COLLECTION]
    for idx in range(3):
        coll.insert_one(
            {
                "_id": f"evt-{idx}",
                "event": f"Band {idx}",
                "url": f"https://example.com/{idx}",
                "date": "2099-06-10",
                "image_id": f"img-{idx}.jpg",
                "venue": {"name": "Venue", "id": ""},
            }
        )

    client = TestClient(create_app())
    response = client.get("/api/test-db/events/spotlight?limit=4&exclude=evt-0,evt-1")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == "evt-2"


def test_post_admin_verify_password_accepts_correct_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    client = TestClient(create_app())
    response = client.post("/api/admin/verify-password", json={"password": "secret-admin"})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_post_admin_verify_password_rejects_wrong_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    client = TestClient(create_app())
    response = client.post("/api/admin/verify-password", json={"password": "wrong"})

    assert response.status_code == 401
    assert "Incorrect" in response.json()["error"]


def test_post_admin_verify_password_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "")

    client = TestClient(create_app())
    response = client.post("/api/admin/verify-password", json={"password": "anything"})

    assert response.status_code == 503


def test_get_users_returns_paged_records() -> None:
    from agent import user_store

    user_store.subscribe("test-db", "alpha@example.com")
    user_store.subscribe("test-db", "beta@example.com")

    client = TestClient(create_app())
    response = client.get("/api/test-db/users?limit=50")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 50
    assert len(body["users"]) == 2
    emails = {row["email"] for row in body["users"]}
    assert emails == {"alpha@example.com", "beta@example.com"}


def test_get_users_caps_limit_at_fifty() -> None:
    client = TestClient(create_app())
    response = client.get("/api/test-db/users?limit=999")
    assert response.status_code == 200
    assert response.json()["limit"] == 50


def test_get_users_unknown_db_still_resolves() -> None:
    client = TestClient(create_app())
    response = client.get("/api/unknown-db-xyz/users")
    assert response.status_code == 200
    body = response.json()
    assert body["users"] == []
    assert body["total"] == 0


def test_post_user_subscribe_saves_email() -> None:
    from agent.mongodb import USERS_COLLECTION, get_database

    client = TestClient(create_app())
    response = client.post(
        "/api/test-db/users/subscribe",
        json={"email": "fan@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "fan@example.com"
    assert body["subscribed_at"]

    stored = get_database("test-db")[USERS_COLLECTION].find_one({"email": "fan@example.com"})
    assert stored is not None


def test_post_user_subscribe_rejects_invalid_email() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/test-db/users/subscribe",
        json={"email": "not-valid"},
    )

    assert response.status_code == 400
    assert "Invalid" in response.json()["error"]


def test_post_user_subscribe_requires_email() -> None:
    client = TestClient(create_app())
    response = client.post("/api/test-db/users/subscribe", json={})

    assert response.status_code == 400
    assert response.json()["error"] == "email is required"


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


def test_delete_venue_deletes_linked_events() -> None:
    from agent.event_store import venue_to_mongo
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = "test-db"
    bad_venue = venue_store.create_venue(db, "Wrong Venue")
    bad_id = str(bad_venue["_id"])

    get_database(db)[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-venue-delete-linked",
            "event": "Bad Gig",
            "venue": venue_to_mongo("Wrong Venue", bad_id),
            "url": "https://example.com/bad",
        }
    )

    client = TestClient(create_app())
    response = client.request(
        "DELETE",
        f"/api/test-db/venues/{bad_id}",
        json={"deleteLinkedEvents": True},
    )

    assert response.status_code == 200
    assert response.json()["events_deleted"] == 1
    assert venue_store.get_venue(db, bad_id) is None
    assert (
        get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-venue-delete-linked"})
        is None
    )
