"""Date window and sorting for event-style research (Task 4).

The curator is asked to emit ISO dates (YYYY-MM-DD). These helpers parse
that field, keep only events in the next N days from "today" (user's
local timezone), and sort rows for display (soonest upcoming first).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from agent.display_time import display_timezone
from agent.models import Resource
from agent.prompt_guides import PromptGuides

# How far ahead to include events (matches subject-matter instructions).
DEFAULT_EVENT_HORIZON_DAYS = 30


def local_today() -> date:
    """Calendar date in the user's display timezone (e.g. Australia/Brisbane).

    Used for pruning past events, window filtering, and LLM date hints.
    This avoids the problem where UTC lags the user's local date — at
    7 AM AEST, UTC is still yesterday, so events from yesterday would
    not get pruned if we used UTC.
    """
    return datetime.now(display_timezone()).date()


# Keep the old name as an alias so existing tests still import it.
utc_today = local_today


def parse_event_sort_date(date_str: str) -> date | None:
    """Best-effort parse of the primary event day from the date string.

    Expects an ISO date prefix ``YYYY-MM-DD`` (optionally followed by more
    text). Returns None if the prefix is missing or invalid.
    """
    s = (date_str or "").strip()
    if len(s) < 10:
        return None
    prefix = s[:10]
    if prefix[4] != "-" or prefix[7] != "-":
        return None
    try:
        return date.fromisoformat(prefix)
    except ValueError:
        return None


def planner_date_instruction(
    guides: PromptGuides | None = None,
    *,
    days: int = DEFAULT_EVENT_HORIZON_DAYS,
) -> str:
    """Appended to the planner user message so queries target the right window."""
    g = guides or PromptGuides()
    today = local_today()
    end = today + timedelta(days=days)
    # When the window spans two months, remind the model to search both.
    month_hint = ""
    if today.month != end.month:
        from calendar import month_name
        month_hint = (
            f"\nIMPORTANT: the window spans **{month_name[today.month]}** and "
            f"**{month_name[end.month]}** — make sure some queries explicitly "
            f"mention {month_name[end.month]} {end.year} so you catch events in "
            "both months.\n"
        )
    body = (
        f"\n\nToday is {today.isoformat()}. Only plan queries for individual "
        f"{g.resource_label_plural} happening from {today.isoformat()} through "
        f"{end.isoformat()} inclusive — roughly the next {days} days. "
        f"{g.portal_avoid_hint}\n"
        f"{month_hint}"
    )
    suffix = (g.planner_date_suffix or "").strip()
    if suffix:
        body += suffix if suffix.endswith("\n") else suffix + "\n"
    return body


def curator_date_instruction(
    guides: PromptGuides | None = None,
    *,
    days: int = DEFAULT_EVENT_HORIZON_DAYS,
) -> str:
    """Prepended before search results for the normalisation step."""
    g = guides or PromptGuides()
    today = local_today()
    end = today + timedelta(days=days)
    body = (
        f"Today is {today.isoformat()}. Only include individual "
        f"{g.curator_resource_label_plural} "
        f"with a primary performance date from {today.isoformat()} through "
        f"{end.isoformat()} inclusive (next {days} days). "
        "Prefer a **specific show URL** when the excerpt names one (aggregator `/e/` "
        "links, Facebook events, ticketing pages over generic index pages — but **do not** "
        "skip multiple events visible on long listing pages.) "
        "When one shared listing URL wraps many dated shows seen in text, emit **one row "
        "per distinct show** (different act names or dates) even if the **url field repeats** "
        "for rows that genuinely only have that portal link. Omit rows without an ISO "
        "date — YYYY-MM-DD — in range.\n"
    )
    suffix = (g.curator_date_suffix or "").strip()
    if suffix:
        body += suffix if suffix.endswith("\n") else suffix + "\n"
    return body


def filter_events_in_upcoming_window(
    resources: list[Resource],
    *,
    days: int = DEFAULT_EVENT_HORIZON_DAYS,
) -> list[Resource]:
    """Drop rows whose ``date`` is missing or outside today..today+days (UTC)."""
    today = local_today()
    end = today + timedelta(days=days)
    out: list[Resource] = []
    for r in resources:
        d = parse_event_sort_date(r.date)
        if d is None:
            continue
        if today <= d <= end:
            out.append(r)
    return out


def sort_resources_by_event_date_asc(resources: list[Resource]) -> list[Resource]:
    """Sort by parsed event date ascending (earliest / soonest gig first)."""
    def key(r: Resource) -> date:
        d = parse_event_sort_date(r.date)
        return d if d is not None else date.max

    return sorted(resources, key=key, reverse=False)


_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_event_weekday_date(date_str: str) -> str:
    """Format ``YYYY-MM-DD...`` as ``Wed 7 May 2026`` for table display."""
    d = parse_event_sort_date(date_str)
    if d is None:
        s = (date_str or "").strip()
        return s if s else "—"
    return f"{_WEEKDAY_ABBR[d.weekday()]} {d.day} {_MONTH_ABBR[d.month - 1]} {d.year}"


def split_title_parts(title: str) -> tuple[str, str, str]:
    """Split ``Act @ Venue, Location`` into three parts for spreadsheet columns.

    Examples::

        "The Beths @ The Tivoli, Brisbane" → ("The Beths", "The Tivoli", "Brisbane")
        "The Beths @ The Tivoli"           → ("The Beths", "The Tivoli", "")
        "The Beths"                        → ("The Beths", "", "")

    The LLM is prompted to use the ``Act @ Venue, Location`` format.  If the
    location part is missing (no comma after the venue) we leave it blank.
    """
    t = (title or "").strip()
    if " @ " in t:
        act, rest = t.split(" @ ", 1)
        act = act.strip()
        rest = rest.strip()
        if ", " in rest:
            venue, location = rest.split(", ", 1)
            return act, venue.strip(), location.strip()
        return act, rest, ""
    return t, "", ""


def split_band_venue_title(title: str) -> tuple[str, str | None]:
    """Split ``The Beths @ The Tivoli`` into ``(\"The Beths\", \" @ The Tivoli\")``.

    Used so only the act name is hyperlinked to the event URL. If there is no
    `` @ `` separator, the whole title is the act and there is no suffix.
    """
    t = (title or "").strip()
    if not t:
        return "—", None
    if " @ " in t:
        act, rest = t.split(" @ ", 1)
        act = act.strip()
        rest = rest.strip()
        if not act:
            return t, None
        return act, f" @ {rest}" if rest else None
    return t, None
