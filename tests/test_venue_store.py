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


def test_venue_keys_match_with_or_without_leading_the() -> None:
    assert venue_store.venue_keys_match("the imperial hotel", "imperial hotel")
    assert venue_store.venue_keys_match("imperial hotel", "the imperial hotel")
    assert venue_store.venue_keys_match("the triffid", "triffid")
    assert not venue_store.venue_keys_match("theatre", "music hall")
    assert not venue_store.venue_keys_match("the", "imperial hotel")


def test_find_by_name_matches_without_leading_the() -> None:
    db = "test-db"
    vid, canonical = venue_store.resolve_or_create(db, "The Imperial Hotel")
    assert canonical == "The Imperial Hotel"

    found = venue_store.find_by_name_or_alias(db, "Imperial Hotel")
    assert found is not None
    assert found["_id"] == vid

    again_id, again_name = venue_store.resolve_or_create(db, "Imperial Hotel")
    assert again_id == vid
    assert again_name == "The Imperial Hotel"


def test_find_by_name_matches_when_db_lacks_leading_the() -> None:
    db = "test-db"
    vid, canonical = venue_store.resolve_or_create(db, "Imperial Hotel")
    assert canonical == "Imperial Hotel"

    found = venue_store.find_by_name_or_alias(db, "The Imperial Hotel")
    assert found is not None
    assert found["_id"] == vid

    again_id, again_name = venue_store.resolve_or_create(db, "The Imperial Hotel")
    assert again_id == vid
    assert again_name == "Imperial Hotel"


def test_create_venue_has_no_lookup_key_fields() -> None:
    db = "test-db"
    vid, _ = venue_store.resolve_or_create(db, "The Triffid")
    doc = venue_store.find_by_name_or_alias(db, "The Triffid")
    assert doc is not None
    assert "name_key" not in doc
    assert "alias_keys" not in doc
    venue_store.add_alias(db, vid, "Triffid")
    doc = venue_store.find_by_name_or_alias(db, "Triffid")
    assert doc is not None
    assert "alias_keys" not in doc


def test_strip_lookup_keys_removes_legacy_fields() -> None:
    db = "test-db"
    from agent.mongodb import VENUES_COLLECTION, get_database

    coll = get_database(db)[VENUES_COLLECTION]
    coll.insert_one(
        {
            "_id": "legacy-venue",
            "name": "Old Venue",
            "name_key": "old venue",
            "aliases": ["Alias"],
            "alias_keys": ["alias"],
        }
    )
    removed = venue_store.strip_lookup_keys(db)
    assert removed == 1
    doc = coll.find_one({"_id": "legacy-venue"})
    assert doc is not None
    assert "name_key" not in doc
    assert "alias_keys" not in doc
    assert venue_store.find_by_name_or_alias(db, "Alias") is not None


def test_list_venues_page_returns_slice_and_total() -> None:
    db = "test-db"
    for name in ("Alpha Hall", "Beta Room", "Gamma Stage"):
        venue_store.create_venue(db, name)

    page, total = venue_store.list_venues_page(db, limit=2, skip=0)
    assert total == 3
    assert len(page) == 2
    assert page[0]["name"] == "Alpha Hall"

    page_two, _ = venue_store.list_venues_page(db, limit=2, skip=2)
    assert len(page_two) == 1
    assert page_two[0]["name"] == "Gamma Stage"


def test_update_venue_replaces_document() -> None:
    db = "test-db"
    created = venue_store.create_venue(db, "Before")
    venue_id = str(created["_id"])
    saved = venue_store.update_venue(
        db,
        venue_id,
        {"_id": venue_id, "name": "After", "aliases": ["Alt"]},
    )
    assert saved["name"] == "After"
    assert saved["aliases"] == ["Alt"]


def test_delete_venue_requires_different_replacement() -> None:
    db = "test-db"
    created = venue_store.create_venue(db, "Solo Venue")
    venue_id = str(created["_id"])
    try:
        venue_store.delete_venue(db, venue_id, replacement_venue_id=venue_id)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_delete_venue_with_linked_event_deletion() -> None:
    from agent.event_store import venue_to_mongo
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = "test-db"
    created = venue_store.create_venue(db, "Stale Hall")
    venue_id = str(created["_id"])
    get_database(db)[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-stale",
            "event": "Stale Act",
            "venue": venue_to_mongo("Stale Hall", venue_id),
            "url": "https://example.com/stale",
        }
    )

    stats = venue_store.delete_venue(
        db, venue_id, delete_linked_events=True
    )

    assert stats["events_deleted"] == 1
    assert stats["venues_deleted"] == 1
    assert venue_store.get_venue(db, venue_id) is None
    assert get_database(db)[EVENTS_COLLECTION].find_one({"_id": "evt-stale"}) is None
