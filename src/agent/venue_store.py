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


# Optional venue fields populated by the venue-mining pipeline (Task 1).
# Kept as passthrough so admin edits (which replace the whole document) never
# wipe the agent's learned "What's On" link or last-seen event date.
_MINING_FIELDS = ("website", "events_link", "events_link_checked", "last_event_date")


def venue_document_to_json(doc: dict[str, Any]) -> dict[str, Any]:
    """Serialise a venue document for admin JSON editing."""
    payload: dict[str, Any] = {
        "_id": str(doc.get("_id") or ""),
        "name": str(doc.get("name") or ""),
        "aliases": [str(alias) for alias in (doc.get("aliases") or [])],
        "location": str(doc.get("location") or ""),
    }
    for field in _MINING_FIELDS:
        value = doc.get(field)
        if value:
            payload[field] = str(value)
    return payload


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
    doc: dict[str, Any] = {
        "_id": venue_id,
        "name": name,
        "aliases": aliases,
        "location": location,
    }
    # Preserve agent-learned mining fields through admin replace_one edits.
    for field in _MINING_FIELDS:
        value = raw.get(field)
        if value not in (None, ""):
            doc[field] = str(value)
    return doc


def update_venue(db_name: str, venue_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Replace a venue document with validated admin JSON."""
    doc = normalize_venue_document(raw, venue_id)
    coll = get_database(db_name)[VENUES_COLLECTION]
    if not coll.find_one({"_id": venue_id}):
        raise KeyError(f"Venue not found: {venue_id}")
    coll.replace_one({"_id": venue_id}, doc)
    logger.info("Updated venue %s in db=%s", venue_id, db_name)
    return doc


def _events_for_venue_filter(venue_id: str) -> dict[str, Any]:
    """MongoDB filter for events linked to a venue id."""
    return {
        "$or": [
            {"venue.id": venue_id},
            {"venue_id": venue_id},
        ]
    }


def count_events_for_venue(db_name: str, venue_id: str) -> int:
    """Count events linked to a venues-collection id."""
    coll = get_database(db_name)[EVENTS_COLLECTION]
    return coll.count_documents(_events_for_venue_filter(venue_id))


def linked_event_ids(db_name: str, venue_id: str) -> list[str]:
    """Return event ids linked to *venue_id*."""
    coll = get_database(db_name)[EVENTS_COLLECTION]
    return [
        str(doc["_id"])
        for doc in coll.find(_events_for_venue_filter(venue_id), {"_id": 1})
    ]


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
    for doc in coll.find(_events_for_venue_filter(from_venue_id)):
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
    replacement_venue_id: str | None = None,
    delete_linked_events: bool = False,
) -> dict[str, int]:
    """Delete *venue_id* after reassigning or removing its linked events."""
    current = get_venue(db_name, venue_id)
    if not current:
        raise KeyError(f"Venue not found: {venue_id}")
    if replacement_venue_id and venue_id == replacement_venue_id:
        raise ValueError("Replacement venue must differ from the venue being deleted.")

    linked_count = count_events_for_venue(db_name, venue_id)
    events_updated = 0
    events_deleted = 0

    if linked_count:
        if delete_linked_events:
            from agent.event_store import delete_events_by_ids
            from agent.image_cache import garbage_collect

            to_remove = set(linked_event_ids(db_name, venue_id))
            events_deleted = delete_events_by_ids(db_name, to_remove)
            coll = get_database(db_name)[EVENTS_COLLECTION]
            remaining = [str(doc["_id"]) for doc in coll.find({}, {"_id": 1})]
            garbage_collect(remaining, db_name=db_name)
        else:
            if not replacement_venue_id:
                raise ValueError(
                    "replacementVenueId is required when reassigning linked events."
                )
            replacement = get_venue(db_name, replacement_venue_id)
            if not replacement:
                raise KeyError(
                    f"Replacement venue not found: {replacement_venue_id}"
                )
            events_updated = reassign_events_venue(
                db_name,
                venue_id,
                replacement_venue_id,
                str(replacement.get("name") or ""),
            )

    get_database(db_name)[VENUES_COLLECTION].delete_one({"_id": venue_id})
    if delete_linked_events:
        logger.info(
            "Deleted venue %s in db=%s (%d linked event(s) removed)",
            venue_id,
            db_name,
            events_deleted,
        )
    else:
        logger.info(
            "Deleted venue %s in db=%s (%d event(s) reassigned to %s)",
            venue_id,
            db_name,
            events_updated,
            replacement_venue_id or "(none)",
        )
    return {
        "events_updated": events_updated,
        "events_deleted": events_deleted,
        "venues_deleted": 1,
    }


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


def venue_name_tokens_key(name: str) -> str:
    """Collapse a venue name to alphanumerics for host matching.

    ``"The Triffid"`` → ``"triffid"`` (leading ``the`` dropped), so it can be
    compared against a domain label like ``thetriffid`` or ``triffid``.
    """
    core = without_leading_the(normalize_venue_key(name))
    return "".join(ch for ch in core if ch.isalnum())


def host_matches_venue(host: str, doc: dict[str, Any]) -> bool:
    """True when *host*'s domain label looks like this venue's own site.

    Compares the registrable-ish domain label (e.g. ``thetriffid`` from
    ``www.thetriffid.com.au``) against the venue name and each alias.
    """
    label = (host or "").lower().split(":")[0]
    parts = [p for p in label.split(".") if p and p not in ("www",)]
    # Use the longest non-TLD label as the brand candidate.
    candidates = sorted(parts, key=len, reverse=True)
    if not candidates:
        return False
    brand = candidates[0]
    names = [str(doc.get("name") or "")] + [str(a) for a in (doc.get("aliases") or [])]
    for nm in names:
        key = venue_name_tokens_key(nm)
        if len(key) >= 4 and (key in brand or brand in key):
            return True
    return False


def text_mentions_venue(text: str, doc: dict[str, Any]) -> bool:
    """True when *text* (a search result title/snippet) names this venue."""
    key = normalize_venue_key(text)
    if not key:
        return False
    names = [str(doc.get("name") or "")] + [str(a) for a in (doc.get("aliases") or [])]
    for nm in names:
        nm_key = without_leading_the(normalize_venue_key(nm))
        if len(nm_key) >= 4 and nm_key in key:
            return True
    return False


def set_venue_web_fields(
    db_name: str,
    venue_id: str,
    *,
    website: str | None = None,
    events_link: str | None = None,
    checked_iso: str | None = None,
) -> None:
    """Persist the venue's own site and discovered "What's On" link (Task 1)."""
    updates: dict[str, str] = {}
    if website:
        updates["website"] = website.strip()
    if events_link:
        updates["events_link"] = events_link.strip()
        updates["events_link_checked"] = (checked_iso or "").strip()
    if not updates:
        return
    get_database(db_name)[VENUES_COLLECTION].update_one(
        {"_id": venue_id}, {"$set": updates}
    )


def venues_with_events_link(db_name: str) -> list[dict[str, Any]]:
    """Return venues that already have a stored ``events_link`` (mining memory)."""
    coll = get_database(db_name)[VENUES_COLLECTION]
    out: list[dict[str, Any]] = []
    for doc in coll.find({"events_link": {"$exists": True, "$nin": ["", None]}}):
        out.append(doc)
    return out


def set_last_event_date(db_name: str, venue_id: str, iso_date: str) -> None:
    """Store the latest event date crawled for a venue (Task 1)."""
    value = (iso_date or "").strip()
    if not value:
        return
    get_database(db_name)[VENUES_COLLECTION].update_one(
        {"_id": venue_id}, {"$set": {"last_event_date": value}}
    )


def update_last_event_dates(db_name: str) -> int:
    """Recompute each venue's ``last_event_date`` from its linked events.

    Returns the number of venue documents updated.
    """
    from agent.event_store import venue_id_from_doc

    events = get_database(db_name)[EVENTS_COLLECTION]
    latest: dict[str, str] = {}
    for doc in events.find({}, {"venue": 1, "venue_id": 1, "date": 1}):
        vid = venue_id_from_doc(doc)
        if not vid:
            continue
        iso = str(doc.get("date") or "").strip()[:10]
        if len(iso) != 10:
            continue
        if vid not in latest or iso > latest[vid]:
            latest[vid] = iso

    coll = get_database(db_name)[VENUES_COLLECTION]
    updated = 0
    for vid, iso in latest.items():
        result = coll.update_one(
            {"_id": vid, "last_event_date": {"$ne": iso}},
            {"$set": {"last_event_date": iso}},
        )
        updated += int(result.modified_count)
    if updated:
        logger.info("Updated last_event_date on %d venue(s) in db=%s", updated, db_name)
    return updated


def delete_venues_without_events(db_name: str) -> int:
    """Remove venue documents with zero linked events (Task 2 tidy-up).

    Runs at the end of each pipeline pass after exclusions and dedupe so venues
    left behind when all their events were culled do not clutter the admin UI.
    """
    from agent.event_store import venue_id_from_doc

    events = get_database(db_name)[EVENTS_COLLECTION]
    linked_ids: set[str] = set()
    for doc in events.find({}, {"venue": 1, "venue_id": 1}):
        vid = venue_id_from_doc(doc)
        if vid:
            linked_ids.add(vid)

    coll = get_database(db_name)[VENUES_COLLECTION]
    removed = 0
    for doc in coll.find({}, {"_id": 1}):
        vid = str(doc["_id"])
        if vid not in linked_ids:
            coll.delete_one({"_id": vid})
            removed += 1
    if removed:
        logger.info(
            "Tidy-up: removed %d venue(s) with no linked events in db=%s",
            removed,
            db_name,
        )
    return removed


def strip_lookup_keys(db_name: str) -> int:
    """Remove legacy ``name_key`` / ``alias_keys`` fields from venue documents."""
    coll = get_database(db_name)[VENUES_COLLECTION]
    result = coll.update_many(
        {},
        {"$unset": {"name_key": "", "alias_keys": ""}},
    )
    return int(result.modified_count)
