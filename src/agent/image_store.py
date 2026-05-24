"""MongoDB storage for self-hosted event poster images.

Images are stored as BSON Binary blobs (efficient for files up to 16 MB).
Many events can share one document when they point at the same upstream URL.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from agent.mongodb import IMAGES_COLLECTION, get_database

logger = logging.getLogger(__name__)

_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}
_EXT_TO_CONTENT_TYPE = {v: k for k, v in _CONTENT_TYPE_TO_EXT.items()}
_KNOWN_EXTS: tuple[str, ...] = tuple(sorted(set(_CONTENT_TYPE_TO_EXT.values())))


def file_name_for_source(url: str, ext: str) -> str:
    """Stable document id from the upstream URL."""
    digest = hashlib.sha256(url.strip().encode()).hexdigest()[:16]
    return f"{digest}{ext}"


def content_type_for_ext(ext: str) -> str:
    return _EXT_TO_CONTENT_TYPE.get(ext.lower(), "application/octet-stream")


def ext_for_content_type(content_type: str) -> str | None:
    return _CONTENT_TYPE_TO_EXT.get((content_type or "").split(";", 1)[0].strip().lower())


def store_image(
    db_name: str,
    *,
    image_id: str,
    source_url: str,
    data: bytes,
    content_type: str,
) -> None:
    """Upsert one poster blob."""
    coll = get_database(db_name)[IMAGES_COLLECTION]
    coll.replace_one(
        {"_id": image_id},
        {
            "_id": image_id,
            "source_url": source_url.strip(),
            "content_type": content_type,
            "data": data,
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        upsert=True,
    )


def fetch_image(db_name: str, image_id: str) -> tuple[bytes, str] | None:
    """Return ``(bytes, content_type)`` or None when missing."""
    doc = get_database(db_name)[IMAGES_COLLECTION].find_one({"_id": image_id})
    if not doc:
        return None
    raw = doc.get("data")
    if raw is None:
        return None
    ctype = str(doc.get("content_type") or "application/octet-stream")
    if isinstance(raw, bytes):
        return raw, ctype
    # PyMongo Binary exposes .decode() in some versions; bytes() always works.
    return bytes(raw), ctype


def find_image_by_source(db_name: str, source_url: str) -> str | None:
    """Return image document id for a cached upstream URL, if any."""
    doc = get_database(db_name)[IMAGES_COLLECTION].find_one(
        {"source_url": source_url.strip()},
        {"_id": 1},
    )
    if doc:
        return str(doc["_id"])
    return None


def list_image_ids(db_name: str) -> set[str]:
    coll = get_database(db_name)[IMAGES_COLLECTION]
    return {str(doc["_id"]) for doc in coll.find({}, {"_id": 1})}


def delete_images_not_in(db_name: str, keep_ids: set[str]) -> int:
    """Garbage-collect poster documents no longer referenced."""
    coll = get_database(db_name)[IMAGES_COLLECTION]
    if keep_ids:
        result = coll.delete_many({"_id": {"$nin": list(keep_ids)}})
    else:
        result = coll.delete_many({})
    return int(result.deleted_count)


def load_event_image_map(db_name: str) -> dict[str, dict[str, str]]:
    """Return ``event_id → {source, image_id}`` from event documents."""
    coll = get_database(db_name)[IMAGES_COLLECTION]
    # Event → image link lives on event docs; rebuild from events collection.
    from agent.mongodb import EVENTS_COLLECTION

    out: dict[str, dict[str, str]] = {}
    events = get_database(db_name)[EVENTS_COLLECTION]
    for doc in events.find({}, {"_id": 1, "poster_url": 1, "image_id": 1}):
        eid = str(doc.get("_id") or "")
        poster = str(doc.get("poster_url") or "").strip()
        image_id = str(doc.get("image_id") or "").strip()
        if eid and poster and image_id:
            out[eid] = {"source": poster, "image_id": image_id}
    # Silence unused import warning for coll
    _ = coll
    return out


def update_event_image_id(db_name: str, event_id: str, image_id: str | None) -> None:
    """Set ``image_id`` on one event document."""
    from agent.mongodb import EVENTS_COLLECTION

    get_database(db_name)[EVENTS_COLLECTION].update_one(
        {"_id": event_id},
        {"$set": {"image_id": image_id or None}},
    )


def bulk_update_event_image_ids(db_name: str, mapping: dict[str, str | None]) -> None:
    """Batch-update ``image_id`` on multiple events."""
    from agent.mongodb import EVENTS_COLLECTION

    coll = get_database(db_name)[EVENTS_COLLECTION]
    for eid, image_id in mapping.items():
        coll.update_one({"_id": eid}, {"$set": {"image_id": image_id or None}})
