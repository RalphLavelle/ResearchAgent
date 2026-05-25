"""Tests for JSON output consumed by the Angular web app."""

from datetime import date, timedelta

from agent.json_output import build_events_payload, render_events_json
from agent.models import Resource


def _make_resource(
    title: str,
    url: str,
    days_ahead: int = 5,
    thumbnail_url: str | None = None,
    summary: str = "",
) -> Resource:
    d = (date.today() + timedelta(days=days_ahead)).isoformat()
    return Resource(
        title=title,
        url=url,
        date=d,
        thumbnail_url=thumbnail_url,
        summary=summary,
    )


def test_payload_has_generated_and_events() -> None:
    r = _make_resource("The Beths @ The Tivoli, Brisbane", "https://example.com/beths")
    payload = build_events_payload([r])
    assert "generated" in payload and isinstance(payload["generated"], str)
    assert "events" in payload and isinstance(payload["events"], list)
    assert len(payload["events"]) == 1


def test_event_fields_match_frontend_contract() -> None:
    r = _make_resource(
        "Band A @ Venue X, Gold Coast",
        "https://example.com/a",
        summary="Great lineup.",
        thumbnail_url="https://example.com/p.jpg",
    )
    ev = build_events_payload([r])["events"][0]
    assert ev["eventName"] == "Band A"
    assert ev["venue"] == "Venue X, Gold Coast"
    assert "Gold Coast" in ev["venue"]
    assert ev["url"] == "https://example.com/a"
    assert ev["summary"] == "Great lineup."
    assert ev["thumbnailUrl"] == "https://example.com/p.jpg"
    assert ev["id"] == r.id


def test_thumbnail_null_when_absent() -> None:
    r = _make_resource("Band", "https://example.com/b", thumbnail_url=None)
    ev = build_events_payload([r])["events"][0]
    assert ev["thumbnailUrl"] is None


def test_empty_resources() -> None:
    payload = build_events_payload([])
    assert payload["events"] == []


def test_render_is_valid_json() -> None:
    import json

    r = _make_resource("Act @ Place, City", "https://example.com/e")
    text = render_events_json([r])

    data = json.loads(text)
    assert data["events"][0]["eventName"] == "Act"
