"""Database search over the public events display window.

The Angular home page sends free-text; this module scores rows in the
``events`` collection (same 30-day window as ``GET /api/<db>/events``)
against ``event``, ``summary``, ``tags``, and ``venue.name`` — no LLM.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from agent import venue_store
from agent.display_time import display_timezone
from agent.event_store import (
    IDX_EVENT_ID,
    IDX_LOCATION,
    IDX_POSTER,
    doc_to_row,
    tags_from_doc,
    venue_id_from_doc,
    venue_name_from_doc,
)
from agent.event_window import api_window_iso_bounds
from agent.image_cache import api_image_url
from agent.json_output import build_events_payload_from_rows
from agent.mongodb import EVENTS_COLLECTION, ensure_collection_indexes, get_database

logger = logging.getLogger(__name__)

# Ignore very short tokens — they add noise without much recall.
_MIN_TERM_LEN = 2


def _terms_from_query(query: str) -> list[str]:
    """Split a search string into lowercase alphanumeric tokens."""
    return [t for t in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(t) >= _MIN_TERM_LEN]


def _searchable_text(doc: dict[str, Any]) -> str:
    """Concatenate the fields the task lists: event, summary, tags, venue.name."""
    tag_text = " ".join(tags_from_doc(doc))
    parts = [
        str(doc.get("event") or ""),
        str(doc.get("summary") or ""),
        tag_text,
        venue_name_from_doc(doc),
    ]
    return " ".join(parts).lower()


def _score_text(haystack: str, terms: list[str]) -> float:
    """Similarity score — terms need not match whole words exactly."""
    if not haystack or not terms:
        return 0.0

    words = re.findall(r"[a-z0-9]+", haystack)
    score = 0.0
    matched = 0

    for term in terms:
        term_score = 0.0
        # Whole-field substring (e.g. "classical" inside "Classical Quartet").
        if term in haystack:
            term_score = len(term) * 2.0
        else:
            for word in words:
                # Prefix / contains — catches partial and compound words.
                if word.startswith(term) or term.startswith(word):
                    term_score = max(term_score, len(term) * 1.5)
                elif term in word or word in term:
                    term_score = max(term_score, len(term) * 1.0)
        if term_score > 0:
            matched += 1
            score += term_score

    if matched == 0:
        return 0.0
    if matched == len(terms):
        score *= 1.25
    return score


def _window_query() -> dict[str, Any]:
    start_iso, end_iso = api_window_iso_bounds()
    return {"date": {"$gte": start_iso, "$lte": end_iso}}


def _regex_prefilter(terms: list[str]) -> dict[str, Any] | None:
    """Optional MongoDB pre-filter: at least one term appears in a search field."""
    if not terms:
        return None
    clauses: list[dict[str, Any]] = []
    for term in terms:
        pattern = {"$regex": re.escape(term), "$options": "i"}
        clauses.extend(
            [
                {"event": pattern},
                {"summary": pattern},
                {"tags": pattern},
                {"venue.name": pattern},
            ]
        )
    return {"$or": clauses}


def search_scored_docs(db_name: str, query: str) -> list[tuple[dict[str, Any], float]]:
    """Return display-window event docs scored against *query*, highest first."""
    terms = _terms_from_query(query)
    if not terms:
        return []

    ensure_collection_indexes(db_name)
    coll = get_database(db_name)[EVENTS_COLLECTION]

    mongo_query: dict[str, Any] = _window_query()
    text_filter = _regex_prefilter(terms)
    if text_filter:
        mongo_query = {"$and": [mongo_query, text_filter]}

    scored: list[tuple[dict[str, Any], float]] = []
    for doc in coll.find(mongo_query):
        url = str(doc.get("url") or "").strip().lower()
        if not url.startswith("http"):
            continue
        text = _searchable_text(doc)
        points = _score_text(text, terms)
        if points > 0:
            scored.append((doc, points))

    scored.sort(key=lambda item: (-item[1], str(item[0].get("date") or "")))
    return scored


def _build_payload_from_docs(db_name: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the API event list from an ordered sequence of MongoDB documents."""
    venue_locations = venue_store.locations_by_id(db_name)
    rows: dict[str, list] = {}
    thumbnail_urls: dict[str, str | None] = {}

    for doc in docs:
        row = doc_to_row(doc)
        venue_id = venue_id_from_doc(doc)
        legacy_location = str(doc.get("location") or "").strip()
        row[IDX_LOCATION] = venue_locations.get(venue_id, legacy_location)
        row[IDX_POSTER] = str(doc.get("poster_url") or "").strip()
        eid = str(row[IDX_EVENT_ID] or "").strip()
        image_id = str(doc.get("image_id") or "").strip()
        thumbnail_urls[eid] = api_image_url(db_name, image_id) if image_id else None
        rows[eid] = row

    return build_events_payload_from_rows(rows, thumbnail_urls=thumbnail_urls)


def load_search_api_payload(db_name: str, query: str) -> dict[str, Any]:
    """Build ``POST /api/<db>/events/search`` JSON from a database text search."""
    cleaned = (query or "").strip()
    now = datetime.now(display_timezone()).isoformat(timespec="seconds")

    if not cleaned:
        return {"generated": now, "events": [], "searchQuery": ""}

    scored = search_scored_docs(db_name, cleaned)
    docs = [doc for doc, _points in scored]
    payload = _build_payload_from_docs(db_name, docs)

    return {
        "generated": payload.get("generated") or now,
        "events": payload.get("events") or [],
        "searchQuery": cleaned,
    }
