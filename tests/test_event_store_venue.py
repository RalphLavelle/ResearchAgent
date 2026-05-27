"""Tests for nested venue documents on event records."""

from datetime import date

from agent.event_store import (
    IDX_VENUE,
    IDX_VENUE_ID,
    doc_to_row,
    row_to_doc,
    venue_name_from_doc,
    venue_id_from_doc,
)


def test_row_to_doc_writes_nested_venue() -> None:
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
    ]
    doc = row_to_doc(row)
    assert doc["venue"] == {"name": "The Tivoli Theatre", "id": "venue-abc"}
    assert "venue_id" not in doc


def test_doc_to_row_reads_nested_venue() -> None:
    doc = {
        "_id": "evt-1",
        "event": "The Beths",
        "venue": {"name": "The Tivoli Theatre", "id": "venue-abc"},
        "location": "Brisbane",
        "date": "2026-05-08",
        "url": "https://example.com/beths",
        "sources": [],
        "poster_url": "",
        "summary": "",
        "added": "2026-05-01",
    }
    row = doc_to_row(doc)
    assert row[IDX_VENUE] == "The Tivoli Theatre"
    assert row[IDX_VENUE_ID] == "venue-abc"


def test_doc_helpers_support_legacy_flat_venue() -> None:
    doc = {
        "venue": "Fortitude Music Hall",
        "venue_id": "venue-old",
    }
    assert venue_name_from_doc(doc) == "Fortitude Music Hall"
    assert venue_id_from_doc(doc) == "venue-old"
