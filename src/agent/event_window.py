"""Date parsing, sorting, and the API display window for event research.

The curator is asked to emit ISO dates (YYYY-MM-DD). These helpers parse
that field and sort rows for display (soonest upcoming first).

Storage policy (Task 7): the pipeline keeps **all future events** it finds,
no matter how far ahead. The only date *window* left is applied at read time
by the API (``API_EVENT_WINDOW_DAYS``) so the public list shows just the next
month. Past events are still pruned at merge time; there is no future pruning.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from agent.display_time import display_timezone
from agent.models import Resource
from agent.prompt_guides import PromptGuides

# How far ahead the **API** includes events in the public list (one month).
# This is purely a read-time filter — every future event is still stored.
API_EVENT_WINDOW_DAYS = 30


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


def api_window_iso_bounds(days: int = API_EVENT_WINDOW_DAYS) -> tuple[str, str]:
    """ISO ``(start, end)`` dates for the API read-time window: today..today+days."""
    today = local_today()
    end = today + timedelta(days=days)
    return today.isoformat(), end.isoformat()


def planner_date_instruction(guides: PromptGuides | None = None) -> str:
    """Appended to the planner user message so queries target **all** future events."""
    g = guides or PromptGuides()
    today = local_today()
    body = (
        f"\n\nToday is {today.isoformat()}. Plan queries for individual upcoming "
        f"{g.resource_label_plural} happening on or after {today.isoformat()} — "
        "cover the near term **and** events announced further ahead (next month, "
        "next season, on-sale tours). Do not restrict to only the next few weeks. "
        f"{g.portal_avoid_hint}\n"
    )
    suffix = (g.planner_date_suffix or "").strip()
    if suffix:
        body += suffix if suffix.endswith("\n") else suffix + "\n"
    return body


def curator_date_instruction(guides: PromptGuides | None = None) -> str:
    """Prepended before search results for the normalisation step."""
    g = guides or PromptGuides()
    today = local_today()
    body = (
        f"Today is {today.isoformat()}. Include every individual "
        f"{g.curator_resource_label_plural} "
        f"with a primary performance date on or after {today.isoformat()} — "
        "keep events far in the future too; do not impose an end date. "
        "Prefer a **specific show URL** when the excerpt names one (aggregator `/e/` "
        "links, Facebook events, ticketing pages over generic index pages — but **do not** "
        "skip multiple events visible on long listing pages.) "
        "When one shared listing URL wraps many dated shows seen in text, emit **one row "
        "per distinct show** (different act names or dates) even if the **url field repeats** "
        "for rows that genuinely only have that portal link. Omit only rows without an ISO "
        "date — YYYY-MM-DD — or whose date has already passed.\n"
    )
    suffix = (g.curator_date_suffix or "").strip()
    if suffix:
        body += suffix if suffix.endswith("\n") else suffix + "\n"
    return body


def filter_future_events(resources: list[Resource]) -> list[Resource]:
    """Keep every event dated today or later; drop undated and past rows.

    No upper bound — events far in the future are retained (Task 7). The API
    applies the one-month display window at read time, not here.
    """
    today = local_today()
    out: list[Resource] = []
    for r in resources:
        d = parse_event_sort_date(r.date)
        if d is None:
            continue
        if d >= today:
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
