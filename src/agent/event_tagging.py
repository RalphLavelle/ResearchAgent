"""Post-merge LLM tagging for events.

After merge and exclusions, assigns up to three tags per untagged event.
Prefers tags already used in the topic database; may introduce new ones sparingly.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent import config, local_output
from agent.event_store import IDX_TAGS, list_distinct_tags, tags_from_row
from agent.llm_factory import build_chat_llm
from agent.local_output import (
    _IDX_EVENT,
    _IDX_LOCATION,
    _IDX_SUMMARY,
    _IDX_URL,
    _IDX_VENUE,
    _load_existing_rows,
    _row_date,
    _row_event_id,
    _write_workbook,
)
from agent.structured_output import invoke_structured

logger = logging.getLogger(__name__)

MAX_TAGS_PER_EVENT = 3

_SYSTEM = """You assign short (lowercase, no punctuation or actual # symbols. Spaces allowed) hashtags to live-music event listings.

Input:
1. Existing tags already used in this database — **reuse these when they fit**.
2. JSON events that currently have no tags (id, name, venue, location, summary, url, date).

Task: return one assignment per input event with 0–3 tags each.

Rules:
- Prefer tags from the existing list when they accurately describe the gig.
- Prioritise the music genre, e.g. 'jazz', 'classical', 'punk', 'latin', 'rap', metal', etc.
- Only invent a **new** tag when no existing tag fits and the distinction is useful
  (e.g. "open mic", "tribute", "free", "festival", "dj set").
- Of particular importance are these tags:
    *'tribute'
    *'free'
    *'open mic'
    *'dj set'
    *'festival'
    *'club'
    *'bar'
    *'restaurant'
- Use lowercase phrases of one to three words; no hashtags or punctuation.
- Maximum three tags per event; omit generic tags like "live music" or "gig".
- Tags must only describe the **event type or theme**, not the venue or city.
- If nothing specific applies beyond a normal concert, return an empty tags list."""


class EventTagAssignment(BaseModel):
    event_id: str = Field(..., description="Stable event id from the input JSON.")
    tags: list[str] = Field(
        default_factory=list,
        description="Up to three short filter tags for this event.",
    )


class EventTaggingResult(BaseModel):
    assignments: list[EventTagAssignment] = Field(default_factory=list)


def normalize_tag(value: str) -> str:
    """Lowercase tag label with collapsed whitespace."""
    return " ".join((value or "").strip().lower().split())


def normalize_tags(values: list[str]) -> list[str]:
    """Return at most three unique normalised tags."""
    out: list[str] = []
    for raw in values:
        tag = normalize_tag(str(raw))
        if not tag or tag in out:
            continue
        out.append(tag)
        if len(out) >= MAX_TAGS_PER_EVENT:
            break
    return out


def _events_needing_tags(existing: dict[str, list]) -> list[dict]:
    """Build LLM payloads for rows with an empty ``tags`` array."""
    events: list[dict] = []
    for row in existing.values():
        if tags_from_row(row):
            continue
        eid = _row_event_id(row)
        if not eid:
            continue
        d = _row_date(row)
        events.append(
            {
                "id": eid,
                "name": str(row[_IDX_EVENT] or ""),
                "venue": str(row[_IDX_VENUE] or ""),
                "location": str(row[_IDX_LOCATION] or ""),
                "date": d.isoformat() if d else "",
                "url": str(row[_IDX_URL] or ""),
                "summary": str(row[_IDX_SUMMARY] or ""),
            }
        )
    return events


def _llm_tag_assignments(
    events: list[dict],
    existing_tags: list[str],
) -> dict[str, list[str]]:
    """Return ``event_id → tags`` from one structured LLM call."""
    if not events:
        return {}

    valid_ids = {str(e.get("id") or "").strip() for e in events}
    valid_ids.discard("")

    vocabulary = ", ".join(existing_tags) if existing_tags else "(none yet)"
    body = json.dumps(events, ensure_ascii=False, indent=2)
    if len(body) > 120_000:
        body = body[:120_000] + "\n…(truncated)"
        logger.warning("Event tagging prompt truncated for size.")

    llm = build_chat_llm()
    try:
        out: EventTaggingResult = invoke_structured(
            llm,
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=(
                        f"Existing tags in database: {vocabulary}\n\n"
                        "Events needing tags:\n"
                        f"{body}"
                    )
                ),
            ],
            EventTaggingResult,
        )
    except Exception as exc:
        logger.warning("Event tagging LLM call failed: %s", exc)
        return {}

    applied: dict[str, list[str]] = {}
    for item in out.assignments or []:
        eid = str(item.event_id or "").strip()
        if not eid or eid not in valid_ids:
            continue
        tags = normalize_tags(list(item.tags or []))
        applied[eid] = tags
    return applied


def apply_event_tags(db_name: str | None = None) -> int:
    """Tag untagged events after merge; returns how many rows received tags."""
    if not config.llm_inference_enabled():
        logger.debug("Event tagging skipped: no LLM backend configured.")
        return 0

    name = db_name or local_output.active_db_name()
    existing = _load_existing_rows(name)
    if not existing:
        return 0

    candidates = _events_needing_tags(existing)
    if not candidates:
        logger.debug("Event tagging: no untagged events.")
        return 0

    vocabulary = list_distinct_tags(name)
    assignments = _llm_tag_assignments(candidates, vocabulary)
    if not assignments:
        return 0

    tagged = 0
    for row in existing.values():
        eid = _row_event_id(row)
        tags = assignments.get(eid)
        if tags is None:
            continue
        while len(row) <= IDX_TAGS:
            row.append([] if len(row) == IDX_TAGS else "")
        row[IDX_TAGS] = tags
        tagged += 1

    if tagged:
        _write_workbook(name, existing)
        logger.info("Event tagging assigned tags to %d row(s).", tagged)
    return tagged
