"""AI-powered natural-language search over the public events display window.

The Angular home page sends a free-text query; this module loads events from the
same 30-day API window, asks the LLM which rows match the user's intent, and
returns only those ids (optionally with refined tags for display).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent import config
from agent.event_store import load_events_api_payload
from agent.event_tagging import normalize_tags
from agent.llm_factory import build_chat_llm
from agent.runner import LLMInvocationError, LLMNotReadyError
from agent.structured_output import invoke_structured

logger = logging.getLogger(__name__)

_SYSTEM = """You filter live-music event listings to match a user's natural-language search.

Input:
1. The user's search text — what they want to find.
2. JSON events from the upcoming display window (id, name, venue, location, date, summary, url, tags).

Task: return only events that genuinely match the search intent. For each match, optionally
refine tags (0–3 lowercase phrases) that help describe why it matched — reuse existing tags
when they fit.

Rules:
- Match on music style, event type, location, venue area, and free-text summary — use
  judgment, not just loose keyword overlap.
- "DJ sets on the Gold Coast" → gigs tagged or described as DJ/club/electronic in Gold
  Coast suburbs or nearby.
- "Brisbane classical music" → classical/orchestra/chamber events in Brisbane metro.
- Exclude clear non-matches even if a single word overlaps loosely.
- Return an empty matches list when nothing fits.
- Every returned event_id must exist in the input list.
- Tags must be lowercase; max 3 per event; omit generic tags like "live music"."""


class EventSearchMatch(BaseModel):
    event_id: str = Field(..., description="Stable event id from the input JSON.")
    tags: list[str] = Field(
        default_factory=list,
        description="Up to three refined filter tags explaining the match.",
    )


class EventSearchResult(BaseModel):
    matches: list[EventSearchMatch] = Field(default_factory=list)


def _events_for_llm(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Compact event payloads for the LLM prompt."""
    compact: list[dict[str, str]] = []
    for ev in events:
        eid = str(ev.get("id") or "").strip()
        if not eid:
            continue
        tags = ev.get("tags")
        tag_list = (
            [str(t).strip() for t in tags if str(t).strip()]
            if isinstance(tags, list)
            else []
        )
        compact.append(
            {
                "id": eid,
                "name": str(ev.get("eventName") or ev.get("event") or "").strip(),
                "venue": str(ev.get("venue") or "").strip(),
                "location": str(ev.get("location") or "").strip(),
                "date": str(ev.get("date") or "").strip(),
                "url": str(ev.get("url") or "").strip(),
                "summary": str(ev.get("summary") or "").strip(),
                "tags": ", ".join(tag_list),
            }
        )
    return compact


def search_matching_events(query: str, events: list[dict[str, Any]]) -> EventSearchResult:
    """Return LLM-selected matches for *query* against *events* (API-shaped dicts)."""
    cleaned = (query or "").strip()
    if not cleaned:
        return EventSearchResult(matches=[])

    compact = _events_for_llm(events)
    if not compact:
        return EventSearchResult(matches=[])

    if not config.llm_inference_enabled():
        raise LLMNotReadyError(
            "LLM backend is not configured — enable OpenAI or Ollama in .env."
        )

    valid_ids = {item["id"] for item in compact}
    body = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(body) > 120_000:
        body = body[:120_000] + "\n…(truncated)"
        logger.warning("Event search prompt truncated for size.")

    llm = build_chat_llm()
    try:
        out: EventSearchResult = invoke_structured(
            llm,
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=(
                        f"User search: {cleaned}\n\n"
                        "Events:\n"
                        f"{body}"
                    )
                ),
            ],
            EventSearchResult,
        )
    except Exception as exc:
        logger.warning("Event search LLM call failed: %s", exc)
        raise LLMInvocationError(f"Event search failed: {exc}") from exc

    matches: list[EventSearchMatch] = []
    for item in out.matches or []:
        eid = str(item.event_id or "").strip()
        if not eid or eid not in valid_ids:
            continue
        tags = normalize_tags(list(item.tags or []))
        matches.append(EventSearchMatch(event_id=eid, tags=tags))
    return EventSearchResult(matches=matches)


def _apply_refined_tags(
    events: list[dict[str, Any]],
    matches: list[EventSearchMatch],
) -> list[dict[str, Any]]:
    """Overlay LLM-refined tags onto matched event dicts for the API response."""
    tag_by_id = {m.event_id: m.tags for m in matches if m.tags}
    if not tag_by_id:
        return events

    updated: list[dict[str, Any]] = []
    for ev in events:
        eid = str(ev.get("id") or "").strip()
        refined = tag_by_id.get(eid)
        if refined:
            updated.append({**ev, "tags": refined})
        else:
            updated.append(ev)
    return updated


def load_search_api_payload(db_name: str, query: str) -> dict[str, Any]:
    """Build ``POST /api/<db>/events/search`` JSON from the display-window events."""
    payload = load_events_api_payload(db_name)
    events = list(payload.get("events") or [])
    cleaned = (query or "").strip()
    if not cleaned:
        return {
            "generated": payload.get("generated") or "",
            "events": [],
            "searchQuery": "",
        }

    result = search_matching_events(cleaned, events)
    matched_ids = {m.event_id for m in result.matches}
    filtered = [ev for ev in events if str(ev.get("id") or "").strip() in matched_ids]
    filtered = _apply_refined_tags(filtered, result.matches)

    return {
        "generated": payload.get("generated") or "",
        "events": filtered,
        "searchQuery": cleaned,
    }
