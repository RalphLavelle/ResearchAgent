"""MongoDB storage for canonical venue names and aliases.

Each venue document has a canonical ``name`` and optional ``aliases`` (strings).
Events store a nested ``venue`` subdocument ``{name, id}`` so the UI does not
need a join. Aliases are curated manually; new ingests match by name or alias.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from agent.mongodb import VENUES_COLLECTION, get_database

logger = logging.getLogger(__name__)


def normalize_venue_key(text: str) -> str:
    """Lowercase key used for name/alias lookups."""
    return " ".join((text or "").strip().lower().split())


def find_by_name_or_alias(db_name: str, raw_text: str) -> dict[str, Any] | None:
    """Return a venue document when *raw_text* matches name or any alias."""
    key = normalize_venue_key(raw_text)
    if not key:
        return None
    coll = get_database(db_name)[VENUES_COLLECTION]
    return coll.find_one(
        {
            "$or": [
                {"name_key": key},
                {"alias_keys": key},
            ]
        }
    )


def create_venue(db_name: str, name: str) -> dict[str, Any]:
    """Insert a venue with *name* as the canonical label and no aliases yet."""
    canonical = (name or "").strip()
    if not canonical:
        raise ValueError("Venue name cannot be empty.")
    key = normalize_venue_key(canonical)
    doc = {
        "_id": str(uuid4()),
        "name": canonical,
        "name_key": key,
        "aliases": [],
        "alias_keys": [],
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
    if alias_key == normalize_venue_key(str(doc.get("name") or "")):
        return False
    alias_keys = list(doc.get("alias_keys") or [])
    if alias_key in alias_keys:
        return False
    aliases = list(doc.get("aliases") or [])
    aliases.append(alias_text)
    alias_keys.append(alias_key)
    coll.update_one(
        {"_id": venue_id},
        {"$set": {"aliases": aliases, "alias_keys": alias_keys}},
    )
    return True
