"""Tests for post-merge LLM event tagging."""

from datetime import date
from unittest.mock import patch

from agent.event_store import IDX_TAGS, save_existing_rows
from agent.event_tagging import (
    EventTagAssignment,
    EventTaggingResult,
    apply_event_tags,
    normalize_tags,
)
from agent.mongodb import EVENTS_COLLECTION, get_database


def test_normalize_tags_caps_at_three() -> None:
    assert normalize_tags(["Open Mic", "open mic", "FREE", "tribute", "extra"]) == [
        "open mic",
        "free",
        "tribute",
    ]


def test_apply_event_tags_writes_to_mongo() -> None:
    db = "test-db"
    row = [
        "Open Mic Night",
        "The Junk Bar",
        "Brisbane",
        date(2026, 6, 1),
        "https://example.com/open-mic",
        "",
        "",
        "Weekly open mic.",
        "2026-05-01",
        "evt-tags-1",
        "",
        [],
    ]
    save_existing_rows(db, {"evt-tags-1": row})

    fake = EventTaggingResult(
        assignments=[
            EventTagAssignment(event_id="evt-tags-1", tags=["open mic"]),
        ]
    )

    with patch("agent.event_tagging.invoke_structured", return_value=fake):
        with patch("agent.event_tagging.config.llm_inference_enabled", return_value=True):
            tagged = apply_event_tags(db)

    assert tagged == 1
    doc = get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-tags-1"})
    assert doc is not None
    assert doc["tags"] == ["open mic"]

    rows = __import__("agent.event_store", fromlist=["load_existing_rows"]).load_existing_rows(db)
    assert rows["evt-tags-1"][IDX_TAGS] == ["open mic"]


def test_apply_event_tags_skips_already_tagged_rows() -> None:
    db = "test-db"
    row = [
        "Band",
        "Venue",
        "City",
        date(2026, 6, 2),
        "https://example.com/gig",
        "",
        "",
        "",
        "2026-05-01",
        "evt-tagged",
        "",
        ["indie"],
    ]
    save_existing_rows(db, {"evt-tagged": row})

    with patch("agent.event_tagging.invoke_structured") as mock_llm:
        with patch("agent.event_tagging.config.llm_inference_enabled", return_value=True):
            tagged = apply_event_tags(db)

    assert tagged == 0
    mock_llm.assert_not_called()
