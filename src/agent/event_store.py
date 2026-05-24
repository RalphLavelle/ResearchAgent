"""MongoDB persistence for curated event rows (replaces the spreadsheet).

Internal row shape matches ``local_output._COLS`` so merge/dedupe logic stays
unchanged. Documents are keyed by Event ID in the ``events`` collection.
"""

from __future__ import annotations

import logging
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


def doc_to_row(doc: dict[str, Any]) -> list:
    """Convert a MongoDB document to a spreadsheet-style row list."""
    eid = str(doc.get("_id") or doc.get("event_id") or "").strip() or str(uuid4())
    return [
        doc.get("event") or "—",
        doc.get("venue") or "",
        doc.get("location") or "",
        _parse_date(doc.get("date")),
        str(doc.get("url") or "").strip(),
        _sources_to_list(doc.get("sources")),
        str(doc.get("poster_url") or "").strip(),
        str(doc.get("summary") or "").strip(),
        str(doc.get("added") or "").strip(),
        eid,
    ]


def row_to_doc(row: list) -> dict[str, Any]:
    """Convert a spreadsheet-style row to a MongoDB document."""
    eid = str(row[IDX_EVENT_ID] or "").strip() or str(uuid4())
    d = _parse_date(row[IDX_DATE] if len(row) > IDX_DATE else None)
    return {
        "_id": eid,
        "event": str(row[IDX_EVENT] or "").strip() or "—",
        "venue": str(row[IDX_VENUE] or "").strip(),
        "location": str(row[IDX_LOCATION] or "").strip(),
        "date": d.isoformat() if d else None,
        "url": str(row[IDX_URL] or "").strip(),
        "sources": _sources_to_mongo(str(row[IDX_SOURCES] or "")),
        "poster_url": str(row[IDX_POSTER] or "").strip(),
        "summary": str(row[IDX_SUMMARY] or "").strip(),
        "added": str(row[IDX_ADDED] or "").strip(),
    }


def load_existing_rows(db_name: str) -> dict[str, list]:
    """Load all events as ``{Event ID → row}``."""
    rows: dict[str, list] = {}
    try:
        coll = get_database(db_name)[EVENTS_COLLECTION]
        for doc in coll.find():
            row = doc_to_row(doc)
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


def save_existing_rows(db_name: str, rows: dict[str, list]) -> None:
    """Replace the events collection with *rows* (sorted soonest-first on write)."""
    coll = get_database(db_name)[EVENTS_COLLECTION]

    def sort_key(row: list) -> date:
        d = _parse_date(row[IDX_DATE] if len(row) > IDX_DATE else None)
        return d if d is not None else date.max

    sorted_rows = sorted(rows.values(), key=sort_key)
    docs = [row_to_doc(r) for r in sorted_rows]
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
