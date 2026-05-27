"""Tests for venue name resolution and the venues collection."""

from agent import venue_store


def test_create_and_find_by_name() -> None:
    db = "test-db"
    vid, name = venue_store.resolve_or_create(db, "The Tivoli Theatre")
    assert name == "The Tivoli Theatre"
    assert vid

    found = venue_store.find_by_name_or_alias(db, "The Tivoli Theatre")
    assert found is not None
    assert found["_id"] == vid


def test_find_by_alias() -> None:
    db = "test-db"
    vid, _ = venue_store.resolve_or_create(db, "The Tivoli Theatre")
    venue_store.add_alias(db, vid, "The Tivoli")
    venue_store.add_alias(db, vid, "Tivoli")

    found = venue_store.find_by_name_or_alias(db, "Tivoli")
    assert found is not None
    assert found["_id"] == vid


def test_resolve_or_create_returns_canonical_name() -> None:
    db = "test-db"
    vid, canonical = venue_store.resolve_or_create(db, "Fortitude Music Hall")
    again_id, again_name = venue_store.resolve_or_create(db, "Fortitude Music Hall")
    assert vid == again_id
    assert canonical == again_name == "Fortitude Music Hall"


def test_normalize_venue_key_collapses_whitespace() -> None:
    assert venue_store.normalize_venue_key("  The   Tivoli  ") == "the tivoli"
