"""YouTube video lookup for recognisable live-music acts (Task 6).

Uses the official YouTube Data API v3 ``search.list`` endpoint. Results are
cached on each event document so repeat clicks do not consume quota.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from agent import config
from agent.event_store import tags_from_doc
from agent.mongodb import EVENTS_COLLECTION, get_database

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
TIMEOUT = httpx.Timeout(12.0, connect=4.0)

# Tags that mean there is no single band worth searching for.
_NON_BAND_TAGS = frozenset({"tribute", "open mic", "dj set"})

# Event-name fragments that indicate tribute/cover nights (case-insensitive).
_COVER_NAME_RE = re.compile(
    r"(tribute\s+to|tribute\s+band|tribute\s+show|"
    r"covers?\s+(night|show|act|band)|"
    r"cover\s+songs?|"
    r"celebration\s+of\s+(the\s+)?music\s+of)",
    re.IGNORECASE,
)

# Generic listings — not a searchable band/artist name.
_GENERIC_ACT_RE = re.compile(
    r"^(live\s+music|weekly\s+sessions?|acoustic\s+sessions?|"
    r"open\s+mic(\s+night)?|jam\s+session|karaoke|"
    r"dj\s+set|house\s+dj|resident\s+dj|"
    r"sunday\s+sessions?|friday\s+sessions?|"
    r"entertainment|variety\s+show)$",
    re.IGNORECASE,
)

# Strip trailing parenthetical hints like "(melb)" or "[USA]".
_TRAILING_PARENS_RE = re.compile(r"[\[(][^[\]()]*[\])]\s*$")


def normalise_act_for_search(act: str) -> str:
    """Return a clean artist query string from the stored event name."""
    name = (act or "").strip()
    if " @ " in name:
        name = name.split(" @ ", 1)[0].strip()
    while True:
        trimmed = _TRAILING_PARENS_RE.sub("", name).strip()
        if trimmed == name:
            break
        name = trimmed
    return name


def _tags_lower(tags: list[str]) -> set[str]:
    return {" ".join(str(tag).strip().lower().split()) for tag in tags if str(tag).strip()}


def is_cover_or_tribute(act: str, tags: list[str] | None = None) -> bool:
    """True when the gig is a tribute/cover act rather than the original artist."""
    tag_set = _tags_lower(tags or [])
    if "tribute" in tag_set:
        return True
    if any("cover" in tag for tag in tag_set):
        return True
    cleaned = normalise_act_for_search(act)
    return bool(cleaned and _COVER_NAME_RE.search(cleaned))


def is_recognisable_act_name(act: str, tags: list[str] | None = None) -> bool:
    """True when the event name looks like a specific band or artist."""
    tag_set = _tags_lower(tags or [])
    if tag_set & _NON_BAND_TAGS:
        return False

    cleaned = normalise_act_for_search(act)
    if len(cleaned) < 2:
        return False
    if not re.search(r"[a-zA-Z]", cleaned):
        return False
    if _GENERIC_ACT_RE.match(cleaned):
        return False
    # Skip names that are mostly punctuation or digits.
    letters = sum(ch.isalpha() for ch in cleaned)
    if letters < 2:
        return False
    return True


def youtube_eligible(act: str, tags: list[str] | None = None) -> bool:
    """Whether the UI should offer a YouTube button for this event."""
    return is_recognisable_act_name(act, tags) and not is_cover_or_tribute(act, tags)


def _search_youtube(query: str) -> dict[str, str] | None:
    """Call YouTube ``search.list`` and return the first embeddable video."""
    api_key = (config.YOUTUBE_API_KEY or "").strip()
    if not api_key:
        return None

    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": 5,
        "videoEmbeddable": "true",
        "safeSearch": "moderate",
        "key": api_key,
    }
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.get(SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("YouTube search failed for %r: %s", query, exc)
        return None

    for item in payload.get("items") or []:
        video_id = str(item.get("id", {}).get("videoId") or "").strip()
        if not video_id:
            continue
        snippet = item.get("snippet") or {}
        title = str(snippet.get("title") or "").strip()
        return {"videoId": video_id, "title": title or query}
    return None


def lookup_youtube_for_act(act: str) -> dict[str, str] | None:
    """Search YouTube for a music video matching *act*."""
    cleaned = normalise_act_for_search(act)
    if not cleaned:
        return None
    return _search_youtube(f"{cleaned} music")


def resolve_event_youtube(db_name: str, event_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(payload, error_code)`` for ``GET .../events/<id>/youtube``.

    *error_code* is ``not_found``, ``not_eligible``, ``not_configured``, or
    ``no_video`` when *payload* is ``None``.
    """
    coll = get_database(db_name)[EVENTS_COLLECTION]
    doc = coll.find_one({"_id": event_id})
    if not doc:
        return None, "not_found"

    act = str(doc.get("event") or "").strip()
    tags = tags_from_doc(doc)
    if not youtube_eligible(act, tags):
        return None, "not_eligible"

    cached_id = str(doc.get("youtube_video_id") or "").strip()
    if cached_id:
        return {
            "videoId": cached_id,
            "title": str(doc.get("youtube_video_title") or act).strip() or act,
            "cached": True,
        }, None

    if doc.get("youtube_lookup_attempted"):
        return None, "no_video"

    if not (config.YOUTUBE_API_KEY or "").strip():
        return None, "not_configured"

    result = lookup_youtube_for_act(act)
    update: dict[str, Any] = {"youtube_lookup_attempted": True}
    if result:
        update["youtube_video_id"] = result["videoId"]
        update["youtube_video_title"] = result.get("title") or ""
        coll.update_one({"_id": event_id}, {"$set": update})
        return {**result, "cached": False}, None

    coll.update_one({"_id": event_id}, {"$set": update})
    return None, "no_video"
