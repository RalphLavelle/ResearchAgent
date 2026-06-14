"""Tests for MongoDB poster cache."""

from __future__ import annotations

import pytest

from agent import image_cache, image_store
from agent.image_cache import api_image_url, cache_thumbnails, garbage_collect
from agent.models import Resource

DB = "test-db"


def _resource(eid: str, thumb: str | None) -> Resource:
    return Resource(
        id=eid,
        title=f"Act {eid} @ Venue, City",
        url=f"https://example.com/event/{eid}",
        date="2099-12-31",
        thumbnail_url=thumb,
    )


def test_cache_thumbnails_writes_to_mongodb(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.mongodb import EVENTS_COLLECTION, get_database

    url = "https://upstream.example/the-beths-tour.jpg"
    fake_bytes = b"\xff\xd8\xff\xe0fakejpeg"
    monkeypatch.setattr(image_cache, "_download", lambda u: (fake_bytes, "image/jpeg"))

    get_database(DB)[EVENTS_COLLECTION].insert_one({"_id": "evt-1", "event": "The Beths"})

    resource = Resource(
        id="evt-1",
        title="The Beths @ Venue, City",
        url="https://example.com/event/evt-1",
        date="2099-12-31",
        thumbnail_url=url,
    )
    out = cache_thumbnails([resource], db_name=DB)

    fname = image_store.file_name_for_source(url, ".jpg")
    assert out[0].thumbnail_url == api_image_url(DB, fname)
    fetched = image_store.fetch_image(DB, fname)
    assert fetched is not None
    assert fetched[0] == fake_bytes

    doc = get_database(DB)[EVENTS_COLLECTION].find_one({"_id": "evt-1"})
    assert doc is not None
    assert doc["poster_quality"] >= 2
    assert doc["poster_url"] == url


def test_cache_thumbnails_sets_poster_quality_negative_on_download_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.mongodb import EVENTS_COLLECTION, get_database

    monkeypatch.setattr(image_cache, "_download", lambda url: None)
    get_database(DB)[EVENTS_COLLECTION].insert_one({"_id": "evt-fail", "event": "The Beths"})

    cache_thumbnails(
        [
            Resource(
                id="evt-fail",
                title="The Beths @ Venue, City",
                url="https://example.com/event/evt-fail",
                date="2099-12-31",
                thumbnail_url="https://upstream.example/missing.jpg",
            )
        ],
        db_name=DB,
    )

    doc = get_database(DB)[EVENTS_COLLECTION].find_one({"_id": "evt-fail"})
    assert doc is not None
    assert doc.get("poster_quality") == -1


def test_cache_thumbnails_reuses_blob_for_same_source_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://upstream.example/shared.webp"
    calls: list[str] = []
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda u: calls.append(u) or (b"webp-bytes", "image/webp"),
    )

    out = cache_thumbnails(
        [_resource("evt-a", url), _resource("evt-b", url)],
        db_name=DB,
    )

    assert len(calls) == 1
    assert out[0].thumbnail_url == out[1].thumbnail_url


def test_cache_thumbnails_sets_none_on_download_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_cache, "_download", lambda url: None)

    out = cache_thumbnails(
        [_resource("evt-fail", "https://upstream.example/missing.jpg")],
        db_name=DB,
    )

    assert out[0].thumbnail_url is None


def test_cache_thumbnails_is_idempotent_when_url_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    url = "https://upstream.example/poster.jpg"

    def fake_download(u: str) -> tuple[bytes, str]:
        calls.append(u)
        return (b"jpeg", "image/jpeg")

    monkeypatch.setattr(image_cache, "_download", fake_download)

    r = _resource("evt-cache", url)
    cache_thumbnails([r], db_name=DB)
    cache_thumbnails([r], db_name=DB)

    assert len(calls) == 1


def test_garbage_collect_deletes_unreferenced_images() -> None:
    from agent.event_store import save_existing_rows
    from agent.local_output import _resource_to_row

    image_store.store_image(
        DB,
        image_id="keep.webp",
        source_url="https://x/a",
        data=b"keep",
        content_type="image/webp",
    )
    image_store.store_image(
        DB,
        image_id="drop.webp",
        source_url="https://x/s",
        data=b"drop",
        content_type="image/webp",
    )
    row = _resource_to_row(_resource("evt-active", "https://x/a"))
    save_existing_rows(DB, {"evt-active": row})
    image_store.bulk_update_event_image_ids(DB, {"evt-active": "keep.webp"})

    removed = garbage_collect({"evt-active"}, db_name=DB)

    assert removed == 1
    assert image_store.fetch_image(DB, "keep.webp") is not None
    assert image_store.fetch_image(DB, "drop.webp") is None


def test_source_urls_by_event_id_batches_image_lookups() -> None:
    """Poster URLs resolve with one images query, not one find per event."""
    from agent.mongodb import EVENTS_COLLECTION, get_database

    db = DB
    image_store.store_image(
        db,
        image_id="poster-a.jpg",
        source_url="https://cdn.example/a.jpg",
        data=b"a",
        content_type="image/jpeg",
    )
    image_store.store_image(
        db,
        image_id="poster-b.jpg",
        source_url="https://cdn.example/b.jpg",
        data=b"b",
        content_type="image/jpeg",
    )
    coll = get_database(db)[EVENTS_COLLECTION]
    coll.insert_many(
        [
            {"_id": "evt-a", "image_id": "poster-a.jpg"},
            {"_id": "evt-b", "image_id": "poster-b.jpg"},
            {"_id": "evt-legacy", "poster_url": "https://legacy.example/p.jpg"},
        ]
    )

    mapping = image_store.source_urls_by_event_id(db)

    assert mapping["evt-a"] == "https://cdn.example/a.jpg"
    assert mapping["evt-b"] == "https://cdn.example/b.jpg"
    assert mapping["evt-legacy"] == "https://legacy.example/p.jpg"
