"""Tests for venue linking during event merge."""

from datetime import date, timedelta
from pathlib import Path

import pytest

import agent.local_output as local_output
from agent import venue_store
from agent.event_store import IDX_VENUE_ID
from agent.local_output import (
    _IDX_VENUE,
    _load_existing_rows,
    merge_and_write,
)
from agent.models import Resource


def _make_resource(title: str, url: str, days_ahead: int = 5) -> Resource:
    d = (date.today() + timedelta(days=days_ahead)).isoformat()
    return Resource(title=title, url=url, date=d)


def test_new_event_links_venue_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r = _make_resource("Band A @ The Tivoli, Brisbane", "https://example.com/a")
    merge_and_write([r])

    row = next(iter(_load_existing_rows(local_output.active_db_name()).values()))
    assert row[_IDX_VENUE] == "The Tivoli"
    assert row[IDX_VENUE_ID]
    assert len(venue_store.list_venues(local_output.active_db_name())) == 1


def test_alias_resolves_to_canonical_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When aliases are configured, new ingests use the canonical venue label."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    db = local_output.active_db_name()

    merge_and_write([_make_resource("Band A @ The Tivoli Theatre, Brisbane", "https://a.example/1")])
    venues = venue_store.list_venues(db)
    assert len(venues) == 1
    venue_store.add_alias(db, str(venues[0]["_id"]), "Tivoli")

    merge_and_write([_make_resource("Band B @ Tivoli, Brisbane", "https://b.example/2")])
    rows = list(_load_existing_rows(db).values())
    tivoli_rows = [row for row in rows if "Tivoli" in str(row[_IDX_VENUE])]
    assert len(tivoli_rows) == 2
    assert all(row[_IDX_VENUE] == "The Tivoli Theatre" for row in tivoli_rows)
    assert all(row[IDX_VENUE_ID] == str(venues[0]["_id"]) for row in tivoli_rows)


def test_partial_act_match_uses_venue_id_with_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial-name dedup treats aliased venue strings as the same place."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    db = local_output.active_db_name()
    d = (date.today() + timedelta(days=7)).isoformat()

    merge_and_write(
        [
            Resource(
                title="The Beths @ The Tivoli Theatre, Brisbane",
                url="https://ticketek.example/beths",
                date=d,
            )
        ]
    )
    vid = str(venue_store.list_venues(db)[0]["_id"])
    venue_store.add_alias(db, vid, "The Tivoli")

    added, skipped, _, _, _ = merge_and_write(
        [
            Resource(
                title="The Beths, with Wax Chattels @ The Tivoli, Brisbane",
                url="https://oztix.example/beths",
                date=d,
            )
        ]
    )
    assert added == 0
    assert skipped == 1
