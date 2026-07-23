"""In-process cache for ``GET /api/<db>/events`` JSON payloads.

The public events list applies a read-time 30-day window over MongoDB on every
uncached request. After each pipeline run (or admin dedupe) we **warm** the cache
so the next visitor request is served from memory instead of scanning the
``events`` collection again.

Cache keys include the active display-window ISO bounds so entries expire
automatically when the calendar day rolls over in ``DISPLAY_TZ``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from agent import config
from agent.event_window import api_window_iso_bounds

logger = logging.getLogger(__name__)

# (db_name, window_start_iso, window_end_iso) → pre-built API JSON
_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
_lock = threading.Lock()


def _cache_key(db_name: str) -> tuple[str, str, str]:
    start_iso, end_iso = api_window_iso_bounds()
    return db_name, start_iso, end_iso


def reset_events_api_cache() -> None:
    """Clear all cached payloads (tests only)."""
    with _lock:
        _cache.clear()


def invalidate_events_api_cache(db_name: str | None = None) -> None:
    """Drop cached payloads for one topic database, or all topics when *db_name* is None."""
    with _lock:
        if db_name is None:
            _cache.clear()
            return
        stale = [key for key in _cache if key[0] == db_name]
        for key in stale:
            del _cache[key]


def get_events_api_payload(
    db_name: str,
    loader: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Return the cached events JSON for *db_name*, loading from MongoDB on a miss."""
    if not config.EVENTS_API_CACHE_ENABLED:
        return loader(db_name)

    key = _cache_key(db_name)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached

    payload = loader(db_name)
    with _lock:
        _cache[key] = payload
        # Drop older window keys for this db (e.g. after midnight).
        stale = [k for k in _cache if k[0] == db_name and k != key]
        for old_key in stale:
            del _cache[old_key]
    return payload


def warm_events_api_cache(
    db_name: str,
    loader: Callable[[str], dict[str, Any]],
) -> None:
    """Pre-build the events JSON after a pipeline write or admin remediation."""
    if not config.EVENTS_API_CACHE_ENABLED:
        return

    key = _cache_key(db_name)
    try:
        payload = loader(db_name)
    except Exception:
        logger.exception("Events API cache warm failed for db=%s", db_name)
        invalidate_events_api_cache(db_name)
        return

    with _lock:
        _cache[key] = payload
        stale = [k for k in _cache if k[0] == db_name and k != key]
        for old_key in stale:
            del _cache[old_key]
    logger.info(
        "Warmed events API cache for db=%s (%d event(s), window %s..%s).",
        db_name,
        len(payload.get("events") or []),
        key[1],
        key[2],
    )
