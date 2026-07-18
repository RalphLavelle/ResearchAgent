"""Tests for YouTube eligibility and lookup (Task 6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.youtube import (
    is_cover_or_tribute,
    is_recognisable_act_name,
    lookup_youtube_for_act,
    normalise_act_for_search,
    resolve_event_youtube,
    youtube_eligible,
)


def test_normalise_act_strips_venue_and_location_hints() -> None:
    assert normalise_act_for_search("Junior Burger @ Mo's Desert Clubhouse, Burleigh") == "Junior Burger"
    assert normalise_act_for_search("Cooper smith (melb)") == "Cooper smith"
    assert normalise_act_for_search("NIRVANNA [USA]") == "NIRVANNA"


def test_youtube_eligible_for_named_acts() -> None:
    assert youtube_eligible("Junior Burger", [])
    assert youtube_eligible("The Beths", ["indie"])
    assert youtube_eligible("BRAIN ELECTRIC", [])


def test_youtube_not_eligible_for_tribute_or_generic() -> None:
    assert not youtube_eligible("Queen Tribute Night", ["tribute"])
    assert not youtube_eligible("Live Music", [])
    assert not youtube_eligible("Open Mic", ["open mic"])
    assert not youtube_eligible("Friday DJ Set", ["dj set"])
    assert not is_recognisable_act_name("Live Music", [])
    assert is_cover_or_tribute("Tribute to Fleetwood Mac", [])


@patch("agent.youtube.httpx.Client")
def test_lookup_youtube_for_act_picks_first_video(mock_client_cls: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [
            {"id": {"videoId": ""}, "snippet": {"title": "skip"}},
            {"id": {"videoId": "abc123"}, "snippet": {"title": "Junior Burger — Live"}},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value = mock_client

    with patch("agent.youtube.config.YOUTUBE_API_KEY", "test-key"):
        result = lookup_youtube_for_act("Junior Burger")

    assert result == {"videoId": "abc123", "title": "Junior Burger — Live"}
    mock_client.get.assert_called_once()


def test_resolve_event_youtube_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.mongodb import EVENTS_COLLECTION, get_database

    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_one(
        {
            "_id": "yt-cached",
            "event": "Junior Burger",
            "url": "https://example.com/gig",
            "date": "2026-08-01",
            "youtube_video_id": "cached99",
            "youtube_video_title": "Cached clip",
        }
    )

    payload, error = resolve_event_youtube("test-db", "yt-cached")
    assert error is None
    assert payload == {"videoId": "cached99", "title": "Cached clip", "cached": True}


@patch("agent.youtube.lookup_youtube_for_act")
def test_resolve_event_youtube_stores_lookup_result(
    mock_lookup: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.mongodb import EVENTS_COLLECTION, get_database

    coll = get_database("test-db")[EVENTS_COLLECTION]
    coll.insert_one(
        {
            "_id": "yt-fresh",
            "event": "Junior Burger",
            "url": "https://example.com/gig2",
            "date": "2026-08-02",
        }
    )
    mock_lookup.return_value = {"videoId": "fresh42", "title": "Fresh clip"}
    monkeypatch.setattr("agent.youtube.config.YOUTUBE_API_KEY", "test-key")

    payload, error = resolve_event_youtube("test-db", "yt-fresh")
    assert error is None
    assert payload == {"videoId": "fresh42", "title": "Fresh clip", "cached": False}

    stored = coll.find_one({"_id": "yt-fresh"})
    assert stored["youtube_video_id"] == "fresh42"
    assert stored["youtube_lookup_attempted"] is True
