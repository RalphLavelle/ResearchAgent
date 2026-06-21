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


def test_host_matches_venue_by_domain_label() -> None:
    doc = {"name": "The Triffid", "aliases": []}
    assert venue_store.host_matches_venue("www.thetriffid.com.au", doc)
    assert venue_store.host_matches_venue("thetriffid.com.au", doc)
    assert not venue_store.host_matches_venue("www.eventbrite.com.au", doc)


def test_text_mentions_venue_in_search_title() -> None:
    doc = {"name": "The Triffid", "aliases": ["Triffid Brisbane"]}
    assert venue_store.text_mentions_venue("Gigs at The Triffid this winter", doc)
    assert not venue_store.text_mentions_venue("Some other venue downtown", doc)


def test_set_venue_web_fields_and_listing() -> None:
    db = "test-db"
    created = venue_store.create_venue(db, "The Triffid")
    vid = str(created["_id"])
    venue_store.set_venue_web_fields(
        db,
        vid,
        website="https://www.thetriffid.com.au",
        events_link="https://www.thetriffid.com.au/whats-on",
        checked_iso="2026-06-21T00:00:00+00:00",
    )
    listed = venue_store.venues_with_events_link(db)
    assert len(listed) == 1
    assert listed[0]["events_link"] == "https://www.thetriffid.com.au/whats-on"


def test_admin_round_trip_preserves_mining_fields() -> None:
    """Editing a venue in the admin UI must not wipe agent-learned fields."""
    db = "test-db"
    created = venue_store.create_venue(db, "The Triffid")
    vid = str(created["_id"])
    venue_store.set_venue_web_fields(
        db,
        vid,
        website="https://www.thetriffid.com.au",
        events_link="https://www.thetriffid.com.au/whats-on",
        checked_iso="2026-06-21T00:00:00+00:00",
    )
    venue_store.set_last_event_date(db, vid, "2026-09-30")

    doc = venue_store.get_venue(db, vid)
    assert doc is not None
    payload = venue_store.venue_document_to_json(doc)
    assert payload["events_link"] == "https://www.thetriffid.com.au/whats-on"
    assert payload["last_event_date"] == "2026-09-30"

    # Simulate the admin editor saving the same payload back (renaming only).
    payload["name"] = "The Triffid (Newstead)"
    venue_store.update_venue(db, vid, payload)

    after = venue_store.get_venue(db, vid)
    assert after is not None
    assert after["name"] == "The Triffid (Newstead)"
    assert after["events_link"] == "https://www.thetriffid.com.au/whats-on"
    assert after["last_event_date"] == "2026-09-30"


def test_delete_venues_without_events() -> None:
    from agent.event_store import venue_to_mongo
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = "test-db"
    orphan = venue_store.create_venue(db, "Empty Hall")
    linked = venue_store.create_venue(db, "Busy Room")
    linked_id = str(linked["_id"])

    get_database(db)[EVENTS_COLLECTION].insert_one(
        {
            "_id": "evt-busy",
            "event": "Band",
            "venue": venue_to_mongo("Busy Room", linked_id),
            "url": "https://example.com/gig",
            "date": "2026-08-01",
        }
    )

    removed = venue_store.delete_venues_without_events(db)
    assert removed == 1
    assert venue_store.get_venue(db, str(orphan["_id"])) is None
    assert venue_store.get_venue(db, linked_id) is not None


def test_update_last_event_dates_uses_latest_event() -> None:
    from agent.event_store import venue_to_mongo
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = "test-db"
    created = venue_store.create_venue(db, "The Triffid")
    vid = str(created["_id"])
    coll = get_database(db)[EVENTS_COLLECTION]
    coll.insert_many(
        [
            {
                "_id": "evt-a",
                "event": "Band A",
                "venue": venue_to_mongo("The Triffid", vid),
                "url": "https://example.com/a",
                "date": "2026-07-01",
            },
            {
                "_id": "evt-b",
                "event": "Band B",
                "venue": venue_to_mongo("The Triffid", vid),
                "url": "https://example.com/b",
                "date": "2026-09-15",
            },
        ]
    )

    updated = venue_store.update_last_event_dates(db)
    assert updated == 1
    doc = venue_store.get_venue(db, vid)
    assert doc is not None
    assert doc["last_event_date"] == "2026-09-15"


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
