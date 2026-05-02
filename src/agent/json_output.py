"""Serialise spreadsheet-backed events to JSON for the Angular web app.

Writes ``events.json`` alongside the spreadsheet (default ``data/events.json``).
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
from pathlib import Path
from typing import Any

from agent import config
from agent.display_time import display_timezone
from agent.event_window import (
    format_event_weekday_date,
    sort_resources_by_event_date_asc,
    split_title_parts,
)
from agent.models import Resource

logger = logging.getLogger(__name__)

JSON_FILENAME = "events.json"


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
    """Return pretty-printed JSON for ``resources`` (spreadsheet order)."""
    payload = build_events_payload(resources)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_events_json(resources: list[Resource]) -> Path:
    """Write ``events.json`` to the configured output directory."""
    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / JSON_FILENAME
    try:
        path.write_text(render_events_json(resources), encoding="utf-8")
        logger.info("Events JSON written: %s", path)
    except Exception as exc:
        logger.error("Failed to write events JSON to %s: %s", path, exc)
        raise
    return path
