"""Tests for database event search (no LLM)."""

from __future__ import annotations

from datetime import timedelta

from agent.event_search import (
    _score_text,
    _searchable_text,
    _terms_from_query,
    load_search_api_payload,
    search_scored_docs,
)
from agent.event_store import venue_to_mongo
from agent.event_window import local_today
from agent.mongodb import EVENTS_COLLECTION, get_database


def test_terms_from_query_skips_short_tokens() -> None:
    assert _terms_from_query("DJ sets on the Gold Coast") == ["dj", "sets", "on", "the", "gold", "coast"]


def test_score_text_substring_and_tag_match() -> None:
    text = "classical quartet the tivoli theatre jazz"
    assert _score_text(text, ["classical"]) > 0
    assert _score_text(text, ["jazz"]) > 0
    assert _score_text(text, ["punk"]) == 0


def test_score_text_all_terms_bonus() -> None:
    text = "brisbane classical music night"
    both = _score_text(text, ["classical", "music"])
    one = _score_text(text, ["classical"])
    assert both > one


def test_searchable_text_uses_event_summary_tags_venue() -> None:
    doc = {
        "event": "Jazz Night",
        "summary": "Smooth standards",
        "tags": ["jazz", "free"],
        "venue": venue_to_mongo("The Tivoli", "v1"),
    }
    blob = _searchable_text(doc)
    assert "jazz night" in blob
    assert "smooth standards" in blob
    assert "the tivoli" in blob


def test_search_scored_docs_matches_display_window_only() -> None:
    today = local_today()
    in_window = (today + timedelta(days=5)).isoformat()
    far_future = (today + timedelta(days=120)).isoformat()

    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_many(
        [
            {
                "_id": "evt-jazz",
                "event": "Jazz Night",
                "url": "https://example.com/jazz",
                "date": in_window,
                "tags": ["jazz"],
                "venue": venue_to_mongo("The Tivoli", ""),
            },
            {
                "_id": "evt-rock",
                "event": "Rock Fest",
                "url": "https://example.com/rock",
                "date": in_window,
                "tags": ["rock"],
            },
            {
                "_id": "evt-far",
                "event": "Far Jazz",
                "url": "https://example.com/far",
                "date": far_future,
                "tags": ["jazz"],
            },
        ]
    )

    scored = search_scored_docs("test-db", "jazz")
    ids = [str(doc["_id"]) for doc, _ in scored]
    assert ids == ["evt-jazz"]
    assert "evt-far" not in ids
    assert "evt-rock" not in ids


def test_search_scored_docs_matches_venue_name() -> None:
    today = local_today()
    in_window = (today + timedelta(days=3)).isoformat()

    get_database("test-db")[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-venue",
            "event": "Live Band",
            "url": "https://example.com/gig",
            "date": in_window,
            "venue": venue_to_mongo("Miami Marketta", ""),
        }
    )

    scored = search_scored_docs("test-db", "miami")
    assert len(scored) == 1
    assert scored[0][0]["_id"] == "evt-venue"


def test_load_search_api_payload_returns_api_shape() -> None:
    today = local_today()
    in_window = (today + timedelta(days=7)).isoformat()

    get_database("test-db")[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-match",
            "event": "Classical Quartet",
            "url": "https://example.com/classical",
            "date": in_window,
            "summary": "Chamber music evening",
            "tags": ["classical"],
        }
    )

    payload = load_search_api_payload("test-db", "classical chamber")
    assert payload["searchQuery"] == "classical chamber"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["eventName"] == "Classical Quartet"
    assert payload["events"][0]["isoDate"] == in_window


def test_post_events_search_endpoint() -> None:
    from starlette.testclient import TestClient

    from agent.api import create_app

    today = local_today()
    in_window = (today + timedelta(days=5)).isoformat()

    get_database("test-db")[EVENTS_COLLECTION].insert_many(
        [
            {
                "_id": "evt-match",
                "event": "Classical Quartet",
                "url": "https://example.com/classical",
                "date": in_window,
                "tags": ["classical"],
            },
            {
                "_id": "evt-skip",
                "event": "Punk Show",
                "url": "https://example.com/punk",
                "date": in_window,
                "tags": ["punk"],
            },
        ]
    )

    client = TestClient(create_app())
    response = client.post(
        "/api/test-db/events/search",
        json={"query": "classical"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["searchQuery"] == "classical"
    assert len(body["events"]) == 1
    assert body["events"][0]["eventName"] == "Classical Quartet"


def test_post_events_search_requires_query() -> None:
    from starlette.testclient import TestClient

    from agent.api import create_app

    client = TestClient(create_app())
    response = client.post("/api/test-db/events/search", json={"query": "  "})
    assert response.status_code == 400
