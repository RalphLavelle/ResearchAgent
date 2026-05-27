"""API tests for venue display fields."""

from datetime import date

from starlette.testclient import TestClient

from agent.api import create_app
from agent.event_store import save_existing_rows


def test_get_events_venue_is_plain_name_string() -> None:
    row = [
        "The Beths",
        "The Tivoli Theatre",
        "Brisbane",
        date(2026, 5, 8),
        "https://example.com/beths",
        "",
        "",
        "Great show.",
        "2026-05-01",
        "evt-1",
        "venue-abc",
    ]
    save_existing_rows("test-db", {"evt-1": row})

    client = TestClient(create_app())
    response = client.get("/api/test-db/events")

    assert response.status_code == 200
    ev = response.json()["events"][0]
    assert ev["venue"] == "The Tivoli Theatre"
    assert ev["location"] == "Brisbane"
    assert ev["venueId"] == "venue-abc"
    assert isinstance(ev["venue"], str)
