"""Tests for AI-powered event search."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent.event_search import EventSearchMatch, EventSearchResult, search_matching_events
from agent.event_window import local_today
from agent.mongodb import EVENTS_COLLECTION, get_database


def test_search_matching_events_filters_by_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {
            "id": "evt-jazz",
            "eventName": "Jazz Night",
            "venue": "The Tivoli",
            "location": "Brisbane",
            "date": "Mon 14 Jul",
            "tags": ["jazz"],
        },
        {
            "id": "evt-rock",
            "eventName": "Rock Fest",
            "venue": "Surf Club",
            "location": "Gold Coast",
            "date": "Tue 15 Jul",
            "tags": ["rock"],
        },
    ]

    def fake_invoke(_llm, _messages, _model):
        return EventSearchResult(
            matches=[EventSearchMatch(event_id="evt-jazz", tags=["jazz", "free"])]
        )

    monkeypatch.setattr("agent.config.OPENAI_ENABLED", True)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)
    monkeypatch.setattr("agent.event_search.build_chat_llm", lambda: object())
    monkeypatch.setattr("agent.event_search.invoke_structured", fake_invoke)

    result = search_matching_events("Brisbane jazz", events)
    assert len(result.matches) == 1
    assert result.matches[0].event_id == "evt-jazz"
    assert result.matches[0].tags == ["jazz", "free"]


def test_search_matching_events_empty_query() -> None:
    result = search_matching_events("   ", [{"id": "a", "eventName": "Gig"}])
    assert result.matches == []


def test_post_events_search_returns_filtered_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from agent.api import create_app

    today = local_today()
    in_window = (today + timedelta(days=5)).isoformat()

    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_many(
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

    def fake_search(_query: str, events: list) -> EventSearchResult:
        ids = {str(ev.get("id") or "") for ev in events}
        assert "evt-match" in ids
        assert "evt-skip" in ids
        return EventSearchResult(matches=[EventSearchMatch(event_id="evt-match", tags=["classical"])])

    monkeypatch.setattr("agent.event_search.search_matching_events", fake_search)

    client = TestClient(create_app())
    response = client.post(
        "/api/test-db/events/search",
        json={"query": "Brisbane classical music"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["searchQuery"] == "Brisbane classical music"
    assert len(body["events"]) == 1
    assert body["events"][0]["eventName"] == "Classical Quartet"
    assert body["events"][0]["tags"] == ["classical"]


def test_post_events_search_requires_query() -> None:
    from starlette.testclient import TestClient

    from agent.api import create_app

    client = TestClient(create_app())
    response = client.post("/api/test-db/events/search", json={"query": "  "})
    assert response.status_code == 400
