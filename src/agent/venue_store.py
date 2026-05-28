"""MongoDB storage for canonical venue names and aliases.

Each venue document has a canonical ``name`` and optional ``aliases`` (strings).
Events store a nested ``venue`` subdocument ``{name, id}`` so the UI does not
need a join. Aliases are curated manually; new ingests match by name or alias.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from agent.mongodb import EVENTS_COLLECTION, VENUES_COLLECTION, get_database

logger = logging.getLogger(__name__)


def normalize_venue_key(text: str) -> str:
    """Lowercase key used for case-insensitive name/alias comparisons."""
    return " ".join((text or "").strip().lower().split())


def without_leading_the(key: str) -> str:
    """Drop a leading ``the `` token from a normalized venue key."""
    if key.startswith("the "):
        return key[4:]
    return key


def venue_keys_match(left: str, right: str) -> bool:
    """True when two normalized keys are equal, with or without a leading ``the ``."""
    if left == right:
        return True
    if not left or not right:
        return False
    left_core = without_leading_the(left)
    right_core = without_leading_the(right)
    return bool(left_core) and left_core == right_core


def _matches_key(doc: dict[str, Any], key: str) -> bool:
    """True when *key* matches the venue canonical name or any alias."""
    name_key = normalize_venue_key(str(doc.get("name") or ""))
    if venue_keys_match(name_key, key):
        return True
    for alias in doc.get("aliases") or []:
        if venue_keys_match(normalize_venue_key(str(alias)), key):
            return True
    return False


def find_by_name_or_alias(db_name: str, raw_text: str) -> dict[str, Any] | None:
    """Return a venue document when *raw_text* matches name or any alias."""
    key = normalize_venue_key(raw_text)
    if not key:
        return None
    coll = get_database(db_name)[VENUES_COLLECTION]
    for doc in coll.find():
        if _matches_key(doc, key):
            return doc
    return None


def create_venue(db_name: str, name: str) -> dict[str, Any]:
    """Insert a venue with *name* as the canonical label and no aliases yet."""
    canonical = (name or "").strip()
    if not canonical:
        raise ValueError("Venue name cannot be empty.")
    doc = {
        "_id": str(uuid4()),
        "name": canonical,
        "aliases": [],
        "location": "",
    }
    get_database(db_name)[VENUES_COLLECTION].insert_one(doc)
    logger.debug("Created venue %r → id=%s", canonical, doc["_id"])
    return doc


def resolve_or_create(db_name: str, raw_text: str) -> tuple[str, str]:
    """Match *raw_text* to an existing venue or create one.

    Returns ``(venue_id, canonical_name)``.
    """
    text = (raw_text or "").strip()
    if not text:
        return "", ""

    existing = find_by_name_or_alias(db_name, text)
    if existing:
        return str(existing["_id"]), str(existing.get("name") or text)

    created = create_venue(db_name, text)
    return str(created["_id"]), str(created["name"])


def list_venues(db_name: str) -> list[dict[str, Any]]:
    """Return all venue documents sorted by canonical name."""
    coll = get_database(db_name)[VENUES_COLLECTION]
    return list(coll.find().sort("name", 1))


def set_location(db_name: str, venue_id: str, location: str) -> None:
    """Set canonical suburb/city on a venue (safe overwrite)."""
    loc = (location or "").strip()
    if not loc:
        return
    coll = get_database(db_name)[VENUES_COLLECTION]
    coll.update_one({"_id": venue_id}, {"$set": {"location": loc}})


def locations_by_id(db_name: str) -> dict[str, str]:
    """Return ``venue_id → location`` for API row hydration."""
    coll = get_database(db_name)[VENUES_COLLECTION]
    return {
        str(doc["_id"]): str(doc.get("location") or "").strip()
        for doc in coll.find({}, {"_id": 1, "location": 1})
    }


def list_venues_page(
    db_name: str,
    *,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return one page of venues (sorted by name) and the total count."""
    coll = get_database(db_name)[VENUES_COLLECTION]
    total = coll.count_documents({})
    docs = list(coll.find().sort("name", 1).skip(skip).limit(limit))
    return docs, total


def get_venue(db_name: str, venue_id: str) -> dict[str, Any] | None:
    """Return one venue document by id."""
    return get_database(db_name)[VENUES_COLLECTION].find_one({"_id": venue_id})


def venue_document_to_json(doc: dict[str, Any]) -> dict[str, Any]:
    """Serialise a venue document for admin JSON editing."""
    return {
        "_id": str(doc.get("_id") or ""),
        "name": str(doc.get("name") or ""),
        "aliases": [str(alias) for alias in (doc.get("aliases") or [])],
        "location": str(doc.get("location") or ""),
    }


def normalize_venue_document(raw: dict[str, Any], venue_id: str) -> dict[str, Any]:
    """Validate and normalise a venue document from the admin editor."""
    doc_id = str(raw.get("_id") or venue_id).strip()
    if doc_id != venue_id:
        raise ValueError("Document _id must match the venue being updated.")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Venue name cannot be empty.")
    aliases_raw = raw.get("aliases")
    if aliases_raw is None:
        aliases_raw = []
    if not isinstance(aliases_raw, list):
        raise ValueError("aliases must be a list of strings.")
    aliases = [str(alias).strip() for alias in aliases_raw if str(alias).strip()]
    location = str(raw.get("location") or "").strip()
    return {"_id": venue_id, "name": name, "aliases": aliases, "location": location}


def update_venue(db_name: str, venue_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Replace a venue document with validated admin JSON."""
    doc = normalize_venue_document(raw, venue_id)
    coll = get_database(db_name)[VENUES_COLLECTION]
    if not coll.find_one({"_id": venue_id}):
        raise KeyError(f"Venue not found: {venue_id}")
    coll.replace_one({"_id": venue_id}, doc)
    logger.info("Updated venue %s in db=%s", venue_id, db_name)
    return doc


def count_events_for_venue(db_name: str, venue_id: str) -> int:
    """Count events linked to a venues-collection id."""
    coll = get_database(db_name)[EVENTS_COLLECTION]
    return coll.count_documents(
        {
            "$or": [
                {"venue.id": venue_id},
                {"venue_id": venue_id},
            ]
        }
    )


def reassign_events_venue(
    db_name: str,
    from_venue_id: str,
    to_venue_id: str,
    to_name: str,
) -> int:
    """Point events from one venue id to another canonical venue."""
    from agent.event_store import venue_to_mongo

    coll = get_database(db_name)[EVENTS_COLLECTION]
    venue_doc = venue_to_mongo(to_name, to_venue_id)
    if venue_doc is None:
        raise ValueError("Replacement venue name and id are required.")
    updated = 0
    for doc in coll.find(
        {
            "$or": [
                {"venue.id": from_venue_id},
                {"venue_id": from_venue_id},
            ]
        }
    ):
        coll.update_one(
            {"_id": doc["_id"]},
            {"$set": {"venue": venue_doc}, "$unset": {"venue_id": ""}},
        )
        updated += 1
    return updated


def delete_venue(
    db_name: str,
    venue_id: str,
    *,
    replacement_venue_id: str,
) -> dict[str, int]:
    """Reassign linked events, then delete *venue_id*."""
    if venue_id == replacement_venue_id:
        raise ValueError("Replacement venue must differ from the venue being deleted.")
    current = get_venue(db_name, venue_id)
    if not current:
        raise KeyError(f"Venue not found: {venue_id}")
    replacement = get_venue(db_name, replacement_venue_id)
    if not replacement:
        raise KeyError(f"Replacement venue not found: {replacement_venue_id}")
    events_updated = reassign_events_venue(
        db_name,
        venue_id,
        replacement_venue_id,
        str(replacement.get("name") or ""),
    )
    get_database(db_name)[VENUES_COLLECTION].delete_one({"_id": venue_id})
    logger.info(
        "Deleted venue %s in db=%s (%d event(s) reassigned to %s)",
        venue_id,
        db_name,
        events_updated,
        replacement_venue_id,
    )
    return {"events_updated": events_updated, "venues_deleted": 1}


def add_alias(db_name: str, venue_id: str, alias: str) -> bool:
    """Append *alias* to a venue when it is not already represented."""
    alias_text = (alias or "").strip()
    if not alias_text:
        return False
    alias_key = normalize_venue_key(alias_text)
    coll = get_database(db_name)[VENUES_COLLECTION]
    doc = coll.find_one({"_id": venue_id})
    if not doc:
        return False
    if _matches_key(doc, alias_key):
        return False
    aliases = list(doc.get("aliases") or [])
    aliases.append(alias_text)
    coll.update_one({"_id": venue_id}, {"$set": {"aliases": aliases}})
    return True


def strip_lookup_keys(db_name: str) -> int:
    """Remove legacy ``name_key`` / ``alias_keys`` fields from venue documents."""
    coll = get_database(db_name)[VENUES_COLLECTION]
    result = coll.update_many(
        {},
        {"$unset": {"name_key": "", "alias_keys": ""}},
    )
    return int(result.modified_count)
