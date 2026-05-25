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
          "date": "...",
          "url": "...",
          "summary": "...",
          "thumbnailUrl": "<url or null>",
          "id": "<stable uuid — for keys, not shown in UI>"
        }
      ]
    }

Field names use camelCase so the TypeScript client does not need key mapping.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from agent.display_time import display_timezone
from agent.event_window import (
    format_event_weekday_date,
    sort_resources_by_event_date_asc,
    split_title_parts,
)
from agent.models import Resource

logger = logging.getLogger(__name__)


def build_events_payload(resources: list[Resource]) -> dict[str, Any]:
    """Build the dict that becomes the JSON document root."""
    ordered = sort_resources_by_event_date_asc(list(resources))
    events: list[dict[str, Any]] = []
    for r in ordered:
        act, venue, location = split_title_parts(r.title or "")
        venue_str = ", ".join(filter(None, [venue, location]))
        thumb = (r.thumbnail_url or "").strip()
        events.append(
            {
                "eventName": act or "—",
                "venue": venue_str,
                "date": format_event_weekday_date(r.date),
                "url": (r.url or "").strip(),
                "summary": (r.summary or "").strip(),
                "thumbnailUrl": thumb if thumb else None,
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
