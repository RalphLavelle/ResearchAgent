"""Build the events JSON payload for the Angular web app (API-only).

The Angular UI loads events via ``GET /api/<db>/events``; this module builds
that response shape in memory. Nothing is written to ``OUTPUT_DIR`` here.

Shape::

    {
      "generated": "<ISO-8601 timestamp with timezone>",
      "events": [
        {
          "eventName": "...",
          "venue": "...",
          "location": "...",
          "date": "...",
          "url": "...",
          "summary": "...",
          "thumbnailUrl": "<url or null>",
          "venueId": "<venues collection id or null>",
          "id": "<stable uuid — for keys, not shown in UI>"
        }
      ]
    }

Field names use camelCase so the TypeScript client does not need key mapping.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from agent.display_time import display_timezone
from agent.event_store import (
    IDX_EVENT,
    IDX_EVENT_ID,
    IDX_LOCATION,
    IDX_POSTER,
    IDX_SUMMARY,
    IDX_TAGS,
    IDX_URL,
    IDX_VENUE,
    IDX_VENUE_ID,
    tags_from_row,
)
from agent.event_window import (
    format_event_weekday_date,
    sort_resources_by_event_date_asc,
    split_title_parts,
)
from agent.models import Resource
from agent.local_output import _row_date
from agent.youtube import youtube_eligible

logger = logging.getLogger(__name__)


def _event_item_from_row(
    row: list,
    *,
    thumbnail_url: str | None = None,
) -> dict[str, Any]:
    """Build one API event object — ``venue`` is always a plain name string."""
    act = str(row[IDX_EVENT] or "").strip() or "—"
    venue_name = str(row[IDX_VENUE] or "").strip()
    location = str(row[IDX_LOCATION] or "").strip()
    raw_date = _row_date(row)
    date_label = format_event_weekday_date(raw_date.isoformat() if raw_date else "")
    eid = str(row[IDX_EVENT_ID] or "").strip()
    thumb = (thumbnail_url or str(row[IDX_POSTER] or "")).strip()
    venue_id = str(row[IDX_VENUE_ID] or "").strip() if len(row) > IDX_VENUE_ID else ""

    return {
        "eventName": act,
        "venue": venue_name,
        "location": location,
        "date": date_label,
        # Machine-readable date for the client's schema.org MusicEvent markup.
        "isoDate": raw_date.isoformat() if raw_date else None,
        "url": str(row[IDX_URL] or "").strip(),
        "summary": str(row[IDX_SUMMARY] or "").strip(),
        "thumbnailUrl": thumb if thumb else None,
        "venueId": venue_id or None,
        "tags": tags_from_row(row),
        "youtubeEligible": youtube_eligible(act, tags_from_row(row)),
        "id": eid,
    }


def build_events_payload_from_rows(
    rows: dict[str, list],
    *,
    thumbnail_urls: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build the API payload directly from MongoDB rows (canonical venue names)."""
    thumb_map = thumbnail_urls or {}
    ordered = sorted(
        rows.values(),
        key=lambda row: (_row_date(row) is None, _row_date(row) or date.max),
    )
    events = [
        _event_item_from_row(row, thumbnail_url=thumb_map.get(str(row[IDX_EVENT_ID] or "")))
        for row in ordered
    ]
    now = datetime.now(display_timezone())
    return {
        "generated": now.isoformat(timespec="seconds"),
        "events": events,
    }


def build_events_payload(resources: list[Resource]) -> dict[str, Any]:
    """Build the dict that becomes the JSON document root."""
    ordered = sort_resources_by_event_date_asc(list(resources))
    events: list[dict[str, Any]] = []
    for r in ordered:
        act, venue, location = split_title_parts(r.title or "")
        events.append(
            {
                "eventName": act or "—",
                "venue": venue,
                "location": location,
                "date": format_event_weekday_date(r.date),
                "isoDate": (r.date or "").strip() or None,
                "url": (r.url or "").strip(),
                "summary": (r.summary or "").strip(),
                "thumbnailUrl": thumb if (thumb := (r.thumbnail_url or "").strip()) else None,
                # Stable key for clients; not shown in the Line-up table UI.
                "id": r.id,
            }
        )

    now = datetime.now(display_timezone())
    return {
        "generated": now.isoformat(timespec="seconds"),
        "events": events,
    }


def render_events_json(resources: list[Resource]) -> str:
    """Return pretty-printed JSON for ``resources`` (in-memory serialisation)."""
    payload = build_events_payload(resources)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
