"""API tests for venue display fields."""

from datetime import timedelta

from starlette.testclient import TestClient

from agent.api import create_app
from agent.event_store import save_existing_rows
from agent.event_window import local_today
from agent.mongodb import VENUES_COLLECTION, get_database


def test_get_events_venue_is_plain_name_string() -> None:
    get_database("test-db")[VENUES_COLLECTION].insert_one(
        {
            "_id": "venue-abc",
            "name": "The Tivoli Theatre",
            "aliases": [],
            "location": "Brisbane",
        }
    )
    # Use a date inside the API's one-month read window so the row is returned.
    event_day = local_today() + timedelta(days=7)
    row = [
        "The Beths",
        "The Tivoli Theatre",
        "Brisbane",
        event_day,
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
    assert ev["tags"] == []
    assert isinstance(ev["venue"], str)
