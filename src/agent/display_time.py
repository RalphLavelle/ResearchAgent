"""Human-facing timestamps for research output (local timezone, not UTC)."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Queensland (Gold Coast / Brisbane) has no daylight saving — matches "AEST" year-round.
_DEFAULT_TZ = "Australia/Brisbane"


def display_timezone() -> ZoneInfo:
    """IANA zone from DISPLAY_TZ env, defaulting to Australia/Brisbane."""
    name = (os.environ.get("DISPLAY_TZ") or _DEFAULT_TZ).strip() or _DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(_DEFAULT_TZ)


def format_generated_timestamp() -> str:
    """String for the 'Generated' line in the events feed (local wall clock + zone)."""
    now = datetime.now(display_timezone())
    return now.strftime("%Y-%m-%d %H:%M %Z")
