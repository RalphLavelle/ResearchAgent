"""MongoDB persistence for curated event rows (replaces the spreadsheet).

Internal row shape matches ``local_output._COLS`` so merge/dedupe logic stays
unchanged. Documents are keyed by Event ID in the ``events`` collection.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from agent.mongodb import EVENTS_COLLECTION, get_database

logger = logging.getLogger(__name__)

# Column indices — must stay aligned with ``local_output._COLS``.
IDX_EVENT = 0
IDX_VENUE = 1
IDX_LOCATION = 2
IDX_DATE = 3
IDX_URL = 4
IDX_SOURCES = 5
IDX_POSTER = 6
IDX_SUMMARY = 7
IDX_ADDED = 8
IDX_EVENT_ID = 9
IDX_VENUE_ID = 10
IDX_TAGS = 11


def tags_from_doc(doc: dict[str, Any]) -> list[str]:
    """Normalise ``tags`` from a MongoDB event document."""
    raw = doc.get("tags")
    if not isinstance(raw, list):
        return []
    return [str(tag).strip() for tag in raw if str(tag).strip()]


def tags_from_row(row: list) -> list[str]:
    """Read tags from an internal row (defaults to empty)."""
    if len(row) <= IDX_TAGS:
        return []
    raw = row[IDX_TAGS]
    if isinstance(raw, list):
        return [str(tag).strip() for tag in raw if str(tag).strip()]
    return []


def list_distinct_tags(db_name: str) -> list[str]:
    """Return sorted unique tags used across all events in a topic database."""
    coll = get_database(db_name)[EVENTS_COLLECTION]
    found: set[str] = set()
    for doc in coll.find({}, {"tags": 1}):
        for tag in tags_from_doc(doc):
            normalised = " ".join(tag.strip().lower().split())
            if normalised:
                found.add(normalised)
    return sorted(found)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _sources_to_list(raw: Any) -> str:
    """Normalise Sources to newline-separated string (spreadsheet shape)."""
    if raw is None:
        return ""
    if isinstance(raw, list):
        return "\n".join(str(u).strip() for u in raw if str(u).strip())
    return str(raw).strip()


def _sources_to_mongo(raw: str) -> list[str]:
    return [u.strip() for u in (raw or "").split("\n") if u.strip()]


def venue_name_from_doc(doc: dict[str, Any]) -> str:
    """Read canonical venue name from nested or legacy flat event documents."""
    venue = doc.get("venue")
    if isinstance(venue, dict):
        return str(venue.get("name") or "").strip()
    return str(venue or "").strip()


def venue_id_from_doc(doc: dict[str, Any]) -> str:
    """Read venues-collection id from nested or legacy flat event documents."""
    venue = doc.get("venue")
    if isinstance(venue, dict):
        return str(venue.get("id") or "").strip()
    return str(doc.get("venue_id") or "").strip()


def venue_to_mongo(name: str, venue_id: str) -> dict[str, str] | None:
    """Build the nested ``venue`` subdocument stored on event documents."""
    canonical = (name or "").strip()
    vid = (venue_id or "").strip()
    if not canonical and not vid:
        return None
    return {"name": canonical, "id": vid}


def doc_to_row(doc: dict[str, Any]) -> list:
    """Convert a MongoDB document to a spreadsheet-style row list."""
    eid = str(doc.get("_id") or doc.get("event_id") or "").strip() or str(uuid4())
    row = [
        doc.get("event") or "—",
        venue_name_from_doc(doc),
        "",
        _parse_date(doc.get("date")),
        str(doc.get("url") or "").strip(),
        _sources_to_list(doc.get("sources")),
        "",
        str(doc.get("summary") or "").strip(),
        str(doc.get("added") or "").strip(),
        eid,
    ]
    row.append(venue_id_from_doc(doc))
    row.append(tags_from_doc(doc))
    return row


def row_to_doc(row: list, *, db_name: str = "") -> dict[str, Any]:
    """Convert a spreadsheet-style row to a MongoDB document."""
    eid = str(row[IDX_EVENT_ID] or "").strip() or str(uuid4())
    d = _parse_date(row[IDX_DATE] if len(row) > IDX_DATE else None)
    venue_name = str(row[IDX_VENUE] or "").strip()
    venue_id = str(row[IDX_VENUE_ID] or "").strip() if len(row) > IDX_VENUE_ID else ""
    doc: dict[str, Any] = {
        "_id": eid,
        "event": str(row[IDX_EVENT] or "").strip() or "—",
        "date": d.isoformat() if d else None,
        "url": str(row[IDX_URL] or "").strip(),
        "sources": _sources_to_mongo(str(row[IDX_SOURCES] or "")),
        "summary": str(row[IDX_SUMMARY] or "").strip(),
        "added": str(row[IDX_ADDED] or "").strip(),
        "tags": tags_from_row(row),
    }
    poster = str(row[IDX_POSTER] or "").strip()
    if db_name and poster.lower().startswith("http"):
        from agent import image_store

        doc["image_id"] = image_store.ensure_source_registered(db_name, poster)
    venue_doc = venue_to_mongo(venue_name, venue_id)
    if venue_doc is not None:
        doc["venue"] = venue_doc
    return doc


def load_events_api_payload(db_name: str) -> dict[str, Any]:
    """Build ``GET /api/<db>/events`` JSON with one events scan (+ venue lookup).

    Only events whose date falls within the next month (``API_EVENT_WINDOW_DAYS``)
    are returned — the store keeps all future events, but this read-time query
    applies the display window (Task 7).

    Unlike ``load_existing_rows``, this skips poster N+1 lookups — thumbnail URLs
    are derived from each event's ``image_id`` without hitting the images collection.
    """
    from agent import venue_store
    from agent.event_window import api_window_iso_bounds
    from agent.image_cache import api_image_url
    from agent.json_output import build_events_payload_from_rows
    from agent.mongodb import ensure_collection_indexes

    ensure_collection_indexes(db_name)

    venue_locations = venue_store.locations_by_id(db_name)
    rows: dict[str, list] = {}
    thumbnail_urls: dict[str, str | None] = {}
    coll = get_database(db_name)[EVENTS_COLLECTION]

    # Read-time window (Task 7): the store holds all future events, but the
    # public list only shows the next month. ISO date strings sort lexically,
    # so a simple string range filters the window and excludes undated rows.
    start_iso, end_iso = api_window_iso_bounds()
    window_query = {"date": {"$gte": start_iso, "$lte": end_iso}}

    for doc in coll.find(window_query):
        row = doc_to_row(doc)
        url = str(row[IDX_URL] or "").strip().lower()
        if not url.startswith("http"):
            continue
        venue_id = venue_id_from_doc(doc)
        legacy_location = str(doc.get("location") or "").strip()
        row[IDX_LOCATION] = venue_locations.get(venue_id, legacy_location)
        legacy_poster = str(doc.get("poster_url") or "").strip()
        row[IDX_POSTER] = legacy_poster
        eid = str(row[IDX_EVENT_ID] or "").strip()
        image_id = str(doc.get("image_id") or "").strip()
        thumbnail_urls[eid] = api_image_url(db_name, image_id) if image_id else None
        sid = eid or str(uuid4())
        while sid in rows:
            sid = str(uuid4())
        row[IDX_EVENT_ID] = sid
        rows[sid] = row

    return build_events_payload_from_rows(rows, thumbnail_urls=thumbnail_urls)


def load_spotlight_api_payload(
    db_name: str,
    *,
    limit: int = 4,
    exclude_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build ``GET /api/<db>/events/spotlight`` — only events with cached posters.

    Candidates must have ``image_id`` (poster successfully cached in MongoDB),
    a valid ``http(s)`` listing URL, and a date on or after ``local_today()``.
    Up to *limit* rows are chosen at random from that pool.
    """
    from agent import venue_store
    from agent.event_window import local_today
    from agent.image_cache import api_image_url
    from agent.json_output import build_events_payload_from_rows
    from agent.mongodb import ensure_collection_indexes

    ensure_collection_indexes(db_name)
    cap = max(1, min(4, int(limit)))
    today_iso = local_today().isoformat()
    skip = exclude_ids or set()

    query: dict[str, Any] = {
        "image_id": {"$exists": True, "$type": "string", "$ne": ""},
        "url": {"$regex": r"^https?://", "$options": "i"},
        "date": {"$gte": today_iso},
    }
    if skip:
        query["_id"] = {"$nin": list(skip)}

    venue_locations = venue_store.locations_by_id(db_name)
    coll = get_database(db_name)[EVENTS_COLLECTION]
    candidates: list[tuple[dict[str, Any], list]] = []

    for doc in coll.find(query):
        row = doc_to_row(doc)
        url = str(row[IDX_URL] or "").strip().lower()
        if not url.startswith("http"):
            continue
        venue_id = venue_id_from_doc(doc)
        legacy_location = str(doc.get("location") or "").strip()
        row[IDX_LOCATION] = venue_locations.get(venue_id, legacy_location)
        row[IDX_POSTER] = str(doc.get("poster_url") or "").strip()
        candidates.append((doc, row))

    if not candidates:
        return {"events": []}

    chosen = random.sample(candidates, k=min(cap, len(candidates)))
    rows: dict[str, list] = {}
    thumbnail_urls: dict[str, str | None] = {}

    for doc, row in chosen:
        eid = str(row[IDX_EVENT_ID] or "").strip() or str(uuid4())
        image_id = str(doc.get("image_id") or "").strip()
        thumbnail_urls[eid] = api_image_url(db_name, image_id) if image_id else None
        sid = eid
        while sid in rows:
            sid = str(uuid4())
        row[IDX_EVENT_ID] = sid
        rows[sid] = row

    payload = build_events_payload_from_rows(rows, thumbnail_urls=thumbnail_urls)
    return {"events": payload.get("events") or []}


def load_existing_rows(db_name: str) -> dict[str, list]:
    """Load all events as ``{Event ID → row}``."""
    from agent import venue_store
    from agent import image_store

    rows: dict[str, list] = {}
    try:
        coll = get_database(db_name)[EVENTS_COLLECTION]
        venue_locations = venue_store.locations_by_id(db_name)
        poster_sources = image_store.source_urls_by_event_id(db_name)
        for doc in coll.find():
            row = doc_to_row(doc)
            venue_id = venue_id_from_doc(doc)
            legacy_location = str(doc.get("location") or "").strip()
            row[IDX_LOCATION] = venue_locations.get(venue_id, legacy_location)
            eid = str(row[IDX_EVENT_ID] or "").strip()
            row[IDX_POSTER] = poster_sources.get(eid, "")
            url = str(row[IDX_URL] or "").strip().lower()
            if not url.startswith("http"):
                continue
            sid = str(row[IDX_EVENT_ID] or "").strip() or str(uuid4())
            while sid in rows:
                sid = str(uuid4())
            row[IDX_EVENT_ID] = sid
            rows[sid] = row
    except Exception as exc:
        logger.warning("Could not read events from MongoDB (%s); starting fresh.", exc)
    return rows


def sync_venue_locations_from_rows(db_name: str, rows: dict[str, list]) -> None:
    """Persist row location values onto linked venue documents."""
    from agent import venue_store

    for row in rows.values():
        venue_id = str(row[IDX_VENUE_ID] or "").strip() if len(row) > IDX_VENUE_ID else ""
        location = str(row[IDX_LOCATION] or "").strip()
        if venue_id and location:
            venue_store.set_location(db_name, venue_id, location)


def save_existing_rows(db_name: str, rows: dict[str, list]) -> None:
    """Replace the events collection with *rows* (sorted soonest-first on write)."""
    sync_venue_locations_from_rows(db_name, rows)
    coll = get_database(db_name)[EVENTS_COLLECTION]

    def sort_key(row: list) -> date:
        d = _parse_date(row[IDX_DATE] if len(row) > IDX_DATE else None)
        return d if d is not None else date.max

    sorted_rows = sorted(rows.values(), key=sort_key)
    docs = [row_to_doc(r, db_name=db_name) for r in sorted_rows]
    coll.delete_many({})
    if docs:
        coll.insert_many(docs)
    logger.info("MongoDB events written: %d → db=%s", len(docs), db_name)


def delete_events_by_ids(db_name: str, event_ids: set[str]) -> int:
    """Remove events whose ids are in *event_ids*."""
    if not event_ids:
        return 0
    coll = get_database(db_name)[EVENTS_COLLECTION]
    result = coll.delete_many({"_id": {"$in": list(event_ids)}})
    return int(result.deleted_count)
