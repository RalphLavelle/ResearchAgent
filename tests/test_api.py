"""Tests for the HTTP API."""

import pytest

from agent import venue_store
from agent.local_output import MergeStats
from agent.report_store import save_run_report
from starlette.testclient import TestClient

from agent.api import create_app


def test_get_events_only_returns_one_month_window() -> None:
    """The store keeps far-future events, but the API list is capped to a month."""
    from datetime import timedelta

    from agent.event_window import local_today
    from agent.mongodb import EVENTS_COLLECTION, get_database

    today = local_today()
    in_window = (today + timedelta(days=10)).isoformat()
    far_future = (today + timedelta(days=120)).isoformat()
    past = (today - timedelta(days=5)).isoformat()

    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_many(
        [
            {
                "_id": "evt-soon",
                "event": "Soon Band",
                "url": "https://example.com/soon",
                "date": in_window,
            },
            {
                "_id": "evt-far",
                "event": "Far Band",
                "url": "https://example.com/far",
                "date": far_future,
            },
            {
                "_id": "evt-past",
                "event": "Past Band",
                "url": "https://example.com/past",
                "date": past,
            },
        ]
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/events")

    assert response.status_code == 200
    events = response.json()["events"]
    returned_urls = {e["url"] for e in events}
    assert "https://example.com/soon" in returned_urls
    assert "https://example.com/far" not in returned_urls
    assert "https://example.com/past" not in returned_urls

    # All three remain stored — only the read-time window hides the others.
    assert coll.count_documents({}) == 3


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
            removed_orphan_venues=0,
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
    for row in body["venues"]:
        assert "linkedEventCount" in row
        assert row["linkedEventCount"] == 0


def test_get_venues_linked_event_count() -> None:
    from agent.event_store import venue_to_mongo
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = "test-db"
    created = venue_store.create_venue(db, "Busy Room")
    venue_id = str(created["_id"])
    get_database(db)[EVENTS_COLLECTION].insert_many(
        [
            {
                "_id": "evt-a",
                "event": "Band A",
                "venue": venue_to_mongo("Busy Room", venue_id),
                "url": "https://example.com/a",
                "date": "2026-07-01",
            },
            {
                "_id": "evt-b",
                "event": "Band B",
                "venue": venue_to_mongo("Busy Room", venue_id),
                "url": "https://example.com/b",
                "date": "2026-08-01",
            },
        ]
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/venues?limit=50")

    assert response.status_code == 200
    row = next(item for item in response.json()["venues"] if item["id"] == venue_id)
    assert row["linkedEventCount"] == 2


def test_get_venue_events_returns_linked_events() -> None:
    from agent.event_store import venue_to_mongo
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = "test-db"
    created = venue_store.create_venue(db, "The Triffid")
    venue_id = str(created["_id"])
    get_database(db)[EVENTS_COLLECTION].insert_many(
        [
            {
                "_id": "evt-later",
                "event": "Later Band",
                "venue": venue_to_mongo("The Triffid", venue_id),
                "url": "https://example.com/later",
                "date": "2026-09-01",
            },
            {
                "_id": "evt-soon",
                "event": "Soon Band",
                "venue": venue_to_mongo("The Triffid", venue_id),
                "url": "https://example.com/soon",
                "date": "2026-07-01",
            },
        ]
    )

    client = TestClient(create_app())
    response = client.get(f"/api/test-db/venues/{venue_id}/events")

    assert response.status_code == 200
    body = response.json()
    assert body["venueId"] == venue_id
    assert body["total"] == 2
    names = [event["eventName"] for event in body["events"]]
    assert names == ["Soon Band", "Later Band"]


def test_get_venue_events_unknown_venue_returns_404() -> None:
    client = TestClient(create_app())
    response = client.get("/api/test-db/venues/missing-venue-id/events")
    assert response.status_code == 404


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
    from datetime import timedelta

    from agent.event_store import save_existing_rows
    from agent.event_window import local_today
    from agent.mongodb import EVENTS_COLLECTION, IMAGES_COLLECTION, get_database

    in_window = local_today() + timedelta(days=10)
    get_database("test-db")[IMAGES_COLLECTION].insert_one(
        {
            "_id": "poster.jpg",
            "source_url": "https://cdn.example.com/the-beths-tour.jpg",
            "content_type": "image/jpeg",
            "data": b"fake",
        }
    )
    with_poster = [
        "The Beths",
        "The Venue",
        "",
        in_window,
        "https://example.com/with-poster",
        "",
        "https://cdn.example.com/the-beths-tour.jpg",
        "",
        "2026-06-01",
        "evt-poster",
        "",
    ]
    without_poster = [
        "Band No Poster",
        "The Venue",
        "",
        in_window + timedelta(days=1),
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


def test_get_events_spotlight_includes_legacy_rows_missing_poster_quality() -> None:
    from datetime import timedelta

    from agent.event_window import local_today
    from agent.mongodb import EVENTS_COLLECTION, IMAGES_COLLECTION, get_database

    in_window = (local_today() + timedelta(days=12)).isoformat()
    get_database("test-db")[IMAGES_COLLECTION].insert_one(
        {
            "_id": "legacy.jpg",
            "source_url": "https://cdn.example.com/the-beths-tour.jpg",
            "content_type": "image/jpeg",
            "data": b"fake",
        }
    )
    get_database("test-db")[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-legacy",
            "event": "The Beths",
            "url": "https://example.com/legacy",
            "date": in_window,
            "image_id": "legacy.jpg",
            "venue": {"name": "Venue", "id": ""},
        }
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/events/spotlight?limit=4")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == "evt-legacy"

    doc = get_database("test-db")[EVENTS_COLLECTION].find_one({"_id": "evt-legacy"})
    assert doc is not None
    assert doc["poster_quality"] >= 2
    assert doc["poster_url"] == "https://cdn.example.com/the-beths-tour.jpg"


def test_get_events_spotlight_excludes_generic_cached_posters() -> None:
    from datetime import timedelta

    from agent.event_window import local_today
    from agent.mongodb import EVENTS_COLLECTION, get_database

    in_window = (local_today() + timedelta(days=14)).isoformat()
    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_many(
        [
            {
                "_id": "evt-specific",
                "event": "The Beths",
                "url": "https://example.com/specific",
                "date": in_window,
                "image_id": "specific.jpg",
                "poster_quality": 3,
                "poster_url": "https://cdn.example.com/the-beths-tour.jpg",
                "venue": {"name": "Venue", "id": ""},
            },
            {
                "_id": "evt-generic",
                "event": "The Beths",
                "url": "https://example.com/generic",
                "date": in_window,
                "image_id": "generic.jpg",
                "poster_quality": 1,
                "poster_url": "https://venue.example/og-image.jpg",
                "venue": {"name": "Venue", "id": ""},
            },
        ]
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/events/spotlight?limit=4")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == "evt-specific"


def test_get_events_spotlight_respects_exclude() -> None:
    from datetime import timedelta

    from agent.event_window import local_today
    from agent.mongodb import EVENTS_COLLECTION, get_database

    in_window = (local_today() + timedelta(days=8)).isoformat()
    coll = get_database("test-db")[EVENTS_COLLECTION]
    for idx in range(3):
        coll.insert_one(
            {
                "_id": f"evt-{idx}",
                "event": f"Band {idx}",
                "url": f"https://example.com/{idx}",
                "date": in_window,
                "image_id": f"img-{idx}.jpg",
                "poster_quality": 2,
                "venue": {"name": "Venue", "id": ""},
            }
        )

    client = TestClient(create_app())
    response = client.get("/api/test-db/events/spotlight?limit=4&exclude=evt-0,evt-1")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == "evt-2"


def test_get_events_spotlight_only_returns_one_month_window() -> None:
    """Spotlight uses the same read-time window as the main events list."""
    from datetime import timedelta

    from agent.event_window import local_today
    from agent.mongodb import EVENTS_COLLECTION, get_database

    today = local_today()
    in_window = (today + timedelta(days=10)).isoformat()
    far_future = (today + timedelta(days=120)).isoformat()

    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_many(
        [
            {
                "_id": "evt-spot-soon",
                "event": "Soon Band",
                "url": "https://example.com/soon-spot",
                "date": in_window,
                "image_id": "soon.jpg",
                "poster_quality": 2,
                "venue": {"name": "Venue", "id": ""},
            },
            {
                "_id": "evt-spot-far",
                "event": "Far Band",
                "url": "https://example.com/far-spot",
                "date": far_future,
                "image_id": "far.jpg",
                "poster_quality": 2,
                "venue": {"name": "Venue", "id": ""},
            },
        ]
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/events/spotlight?limit=4")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == "evt-spot-soon"


def test_post_admin_run_once_requires_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    client = TestClient(create_app())
    response = client.post("/api/admin/run-once", json={"password": "wrong"})

    assert response.status_code == 401


def test_post_admin_run_once_runs_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    def fake_execute(*, dry_run: bool = False):
        assert dry_run is False
        return {"run_log_message": "Saved 2 new event(s)."}

    monkeypatch.setattr("agent.api.execute_run_once", fake_execute)

    client = TestClient(create_app())
    response = client.post("/api/admin/run-once", json={"password": "secret-admin"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["message"] == "Saved 2 new event(s)."


def test_post_admin_run_once_llm_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.runner import LLMNotReadyError

    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    def raise_not_ready(*, dry_run: bool = False):
        raise LLMNotReadyError("LLM backend is not reachable or misconfigured.")

    monkeypatch.setattr("agent.api.execute_run_once", raise_not_ready)

    client = TestClient(create_app())
    response = client.post("/api/admin/run-once", json={"password": "secret-admin"})

    assert response.status_code == 503
    assert "LLM backend" in response.json()["error"]


def test_post_admin_run_once_llm_invocation_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.runner import LLMInvocationError

    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    def raise_invocation(*, dry_run: bool = False):
        raise LLMInvocationError(
            'Planner failed (RuntimeError): model "qwen3" not found'
        )

    monkeypatch.setattr("agent.api.execute_run_once", raise_invocation)

    client = TestClient(create_app())
    response = client.post("/api/admin/run-once", json={"password": "secret-admin"})

    assert response.status_code == 503
    assert "qwen3" in response.json()["error"]


def test_post_admin_run_targeted_requires_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    client = TestClient(create_app())
    response = client.post(
        "/api/admin/run-targeted",
        json={"password": "secret-admin", "query": "   "},
    )

    assert response.status_code == 400
    assert "query" in response.json()["error"]


def test_post_admin_run_targeted_requires_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    client = TestClient(create_app())
    response = client.post(
        "/api/admin/run-targeted",
        json={"password": "wrong", "query": "Powderfinger Brisbane"},
    )

    assert response.status_code == 401


def test_post_admin_run_targeted_runs_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")

    def fake_execute(*, dry_run: bool = False, targeted_query: str | None = None):
        assert dry_run is False
        assert targeted_query == "Powderfinger Brisbane"
        return {"run_log_message": "Saved 1 new event(s)."}

    monkeypatch.setattr("agent.api.execute_run_once", fake_execute)

    client = TestClient(create_app())
    response = client.post(
        "/api/admin/run-targeted",
        json={"password": "secret-admin", "query": "Powderfinger Brisbane"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["message"] == "Saved 1 new event(s)."
    assert body["query"] == "Powderfinger Brisbane"


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


def test_post_user_subscribe_saves_email(monkeypatch) -> None:
    from agent.mongodb import USERS_COLLECTION, get_database

    monkeypatch.setattr("agent.config.EMAIL_SIGNUP_ENABLED", True)
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


def test_post_user_subscribe_rejects_invalid_email(monkeypatch) -> None:
    monkeypatch.setattr("agent.config.EMAIL_SIGNUP_ENABLED", True)
    client = TestClient(create_app())
    response = client.post(
        "/api/test-db/users/subscribe",
        json={"email": "not-valid"},
    )

    assert response.status_code == 400
    assert "Invalid" in response.json()["error"]


def test_post_user_subscribe_requires_email(monkeypatch) -> None:
    monkeypatch.setattr("agent.config.EMAIL_SIGNUP_ENABLED", True)
    client = TestClient(create_app())
    response = client.post("/api/test-db/users/subscribe", json={})

    assert response.status_code == 400
    assert response.json()["error"] == "email is required"


def test_get_site_config_defaults_email_signup_off() -> None:
    client = TestClient(create_app())
    response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json() == {
        "emailSignupEnabled": False,
        "googleAnalyticsMeasurementId": None,
    }


def test_get_site_config_reflects_env(monkeypatch) -> None:
    monkeypatch.setattr("agent.config.EMAIL_SIGNUP_ENABLED", True)
    monkeypatch.setattr("agent.config.GOOGLE_ANALYTICS_MEASUREMENT_ID", "G-TEST123")
    client = TestClient(create_app())
    response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json() == {
        "emailSignupEnabled": True,
        "googleAnalyticsMeasurementId": "G-TEST123",
    }


def test_post_user_subscribe_disabled_when_feature_off(monkeypatch) -> None:
    monkeypatch.setattr("agent.config.EMAIL_SIGNUP_ENABLED", False)
    client = TestClient(create_app())
    response = client.post(
        "/api/test-db/users/subscribe",
        json={"email": "fan@example.com"},
    )

    assert response.status_code == 403
    assert response.json()["error"] == "Email signup is disabled"


def test_post_comment_saves_feedback() -> None:
    from agent.mongodb import COMMENTS_COLLECTION, get_database

    client = TestClient(create_app())
    response = client.post(
        "/api/test-db/comments",
        json={"name": "Jamie", "comment": "Love the new layout!"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Jamie"
    assert body["comment"] == "Love the new layout!"
    assert body["date"]

    stored = get_database("test-db")[COMMENTS_COLLECTION].find_one({"name": "Jamie"})
    assert stored is not None
    assert stored["comment"] == "Love the new layout!"


def test_post_comment_requires_name_and_comment() -> None:
    client = TestClient(create_app())

    missing_name = client.post("/api/test-db/comments", json={"comment": "Hi"})
    assert missing_name.status_code == 400
    assert missing_name.json()["error"] == "name is required"

    missing_comment = client.post("/api/test-db/comments", json={"name": "Jamie"})
    assert missing_comment.status_code == 400
    assert missing_comment.json()["error"] == "comment is required"


def test_get_comments_lists_saved_feedback() -> None:
    from agent import comment_store

    saved = comment_store.add_comment("test-db", "Jamie", "Nice site!")
    client = TestClient(create_app())
    response = client.get("/api/test-db/comments?limit=50&skip=0")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    ids = {row["id"] for row in body["comments"]}
    assert str(saved["_id"]) in ids


def test_delete_comment_via_api() -> None:
    from agent import comment_store
    from agent.mongodb import COMMENTS_COLLECTION, get_database

    saved = comment_store.add_comment("test-db", "Test", "Delete me")
    cid = str(saved["_id"])
    client = TestClient(create_app())

    response = client.delete(f"/api/test-db/comments/{cid}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    assert get_database("test-db")[COMMENTS_COLLECTION].find_one({"_id": cid}) is None

    missing = client.delete("/api/test-db/comments/does-not-exist")
    assert missing.status_code == 404


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


def test_get_event_youtube_returns_cached_video() -> None:
    from agent.mongodb import EVENTS_COLLECTION, get_database

    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_one(
        {
            "_id": "evt-yt-api",
            "event": "Junior Burger",
            "url": "https://example.com/jb",
            "date": "2026-08-03",
            "youtube_video_id": "vid-api-1",
            "youtube_video_title": "JB live",
        }
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/events/evt-yt-api/youtube")

    assert response.status_code == 200
    body = response.json()
    assert body["videoId"] == "vid-api-1"
    assert body["title"] == "JB live"
    assert body["cached"] is True
