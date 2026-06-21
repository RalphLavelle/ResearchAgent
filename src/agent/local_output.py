"""Write curated events to MongoDB (replaces the spreadsheet).

Key behaviours (Tasks 8, 9, 11, 13, 14):
- Output is the topic's ``events`` collection — the source of truth for all events.
- Columns: Event, Venue, Location, Date, URL, Sources, Poster URL, Summary, Added, Event ID (hidden).
- The spreadsheet **accumulates**: existing rows are merged, past events removed,
  new events added.  Nothing is overwritten by new LLM text.  Rows are addressed
  by **Event ID** so independent gigs **may reuse the same listing URL** — only an
  identical URL **and** the same inferred act+date pair is skipped as a re-ingest.
- Semantic deduplication (Task 13): before adding a row the pipeline checks
  whether an event with the same (act, date) already exists (venue ignored for
  exact matches because venue text can vary slightly).  If so the new URL is
  appended to that row's Sources column (when the domain differs) rather than
  creating a duplicate row.
- Partial-name deduplication (Task 14): when act names only partially match
  (e.g. "Singer 1" vs "Singer 1, with Singer 2") but the venue AND date are
  identical, the two entries are treated as the same event.  The longer act
  name is kept as the canonical name; the duplicate URL is added to Sources if
  from a different domain.
- The single ``run_log.md`` is gone (Task 11). Each run now appends a document to
  the MongoDB ``reports`` collection via ``report_store.save_run_report``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from agent import config
from agent.event_store import IDX_VENUE_ID as _DB_IDX_VENUE_ID
from agent.event_store import load_existing_rows as _db_load_rows
from agent.event_store import save_existing_rows as _db_save_rows
from agent import venue_store
from agent.display_time import format_generated_timestamp
from agent.enrich import poster_quality_score
from agent.event_window import (
    parse_event_sort_date,
    split_title_parts,
    local_today,
)
from agent.models import Resource
from agent.source_store import (
    DistinctEventKey,
    UrlOutcome,
    distinct_event_counts,
    note_url_event,
)

logger = logging.getLogger(__name__)

# Legacy filename — kept for migration tooling only.
RESEARCH_FILENAME = "agent_research.xlsx"


def active_db_name() -> str:
    """MongoDB database name for the active topic."""
    return config.ACTIVE_TOPIC.db


@dataclass(frozen=True)
class MergeStats:
    """How a single run changed the spreadsheet (Task 12 follow-up).

    Captured by ``write_output`` and stored on each run report in MongoDB
    (``report_store.save_run_report``).

    Attributes:
        added:           New rows inserted from the curator's resources.
        skipped:         Curator resources skipped as duplicates of existing rows
                         (URL re-ingest, exact semantic match, or partial-name match).
        removed_past:      Rows deleted because their event date had passed.
        removed_exclusion: Rows removed after merge by ``apply_event_exclusions``
                         (``drop_terms`` literal match and/or LLM interpretation of ``exclusions``).
        removed_dedupe:    Rows merged away by the optional LLM semantic-dedupe pass.
        removed_orphan_venues: Venue documents deleted because they had zero linked events.
        total_after:       Final spreadsheet row count after all merging is done.
        url_outcomes:              Per-URL (events_added, events_seen) tallies from this run's merge.
        url_distinct_event_counts: Distinct (act, date) events per URL in this run's merge.
    """

    added: int
    skipped: int
    removed_past: int
    removed_exclusion: int
    removed_dedupe: int
    removed_orphan_venues: int
    total_after: int
    url_outcomes: dict[str, UrlOutcome] = field(default_factory=dict)
    url_distinct_event_counts: dict[str, int] = field(default_factory=dict)

# ── Schema ────────────────────────────────────────────────────────────────────
# Column names in display order.  The Date cell stores a Python date object
# so Excel can sort natively; the number format renders it as "Wed 7 May 2026".
# Sources stores newline-separated alternative URLs for the same gig.
_COLS = [
    "Event", "Venue", "Location", "Date", "URL",
    "Sources", "Poster URL", "Summary", "Added",
    "Event ID",
]
_DATE_FORMAT = "ddd d mmm yyyy"

# 0-based indices into _COLS
_IDX_EVENT    = 0
_IDX_VENUE    = 1
_IDX_LOCATION = 2
_IDX_DATE     = 3
_IDX_URL      = 4
_IDX_SOURCES  = 5
_IDX_POSTER   = 6
_IDX_SUMMARY  = 7
_IDX_ADDED    = 8
_IDX_EVENT_ID = 9
_IDX_VENUE_ID = _DB_IDX_VENUE_ID

# Column widths (0 = hide the column)
_COL_WIDTHS = [30, 22, 16, 18, 55, 45, 0, 40, 20, 0]


def output_directory() -> Path:
    """Active topic folder for run reports and snapshots (see ``config.OUTPUT_DIR``)."""
    return config.OUTPUT_DIR


# ── Domain / dedup helpers ────────────────────────────────────────────────────


def _domain(url: str) -> str:
    """Return the bare domain of a URL (lowercase, no 'www.')."""
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc.lstrip("www.") if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def _row_event_id(row: list) -> str:
    """Stable Event ID cell, if present."""
    if len(row) <= _IDX_EVENT_ID:
        return ""
    return str(row[_IDX_EVENT_ID] or "").strip()


def _dedup_key_from_row(row: list) -> tuple[str, date] | None:
    """(act_lower, date) used to detect semantic duplicates.

    Venue is intentionally excluded because venue text can vary slightly
    across sources while still referring to the same event.
    Returns None when the row lacks enough data to compare.
    """
    act = str(row[_IDX_EVENT] or "").strip().lower()
    d   = _row_date(row)
    if not act or d is None:
        return None
    return (act, d)


def _dedup_key_from_resource(r: Resource) -> tuple[str, date] | None:
    """Same key shape as ``_dedup_key_from_row``, but derived from a Resource."""
    act, _venue, _location = split_title_parts(r.title or "")
    d = parse_event_sort_date(r.date)
    if not act.strip() or d is None:
        return None
    return (act.strip().lower(), d)


def _add_source(row: list, url: str) -> bool:
    """Append *url* to a row's Sources cell when it is from a different domain.

    Returns True when a new source was actually added.
    """
    primary_url = str(row[_IDX_URL] or "").strip()
    new_domain  = _domain(url)
    if not new_domain or new_domain == _domain(primary_url):
        return False  # same domain — not worth recording as an alternative

    current = str(row[_IDX_SOURCES] or "").strip()
    existing_urls = [u.strip() for u in current.split("\n") if u.strip()]
    if url in existing_urls:
        return False

    existing_urls.append(url)
    row[_IDX_SOURCES] = "\n".join(existing_urls)
    return True


def _maybe_upgrade_poster(row: list, new_poster: str | None, act: str) -> bool:
    """Replace the row's Poster URL when the new candidate is a better fit.

    "Better" is defined by ``agent.enrich.poster_quality_score`` (which
    penalises empty / decoration / generic posters and rewards images
    whose filename matches the act). The intent is for the spreadsheet
    to *self-heal* over time: as the same event is re-ingested via the
    dedupe paths in ``merge_and_write``, a stale logo/ad poster from an
    old run gets swapped for a fresh, event-specific one.

    Never downgrades — if the new candidate scores lower or equal, the
    cell is left alone. Returns True only when the cell actually changed.
    """
    new_p = (new_poster or "").strip()
    if not new_p:
        return False  # never replace an existing poster with nothing
    existing_p = str(row[_IDX_POSTER] or "").strip()
    if poster_quality_score(new_p, act) > poster_quality_score(existing_p, act):
        row[_IDX_POSTER] = new_p
        return True
    return False


def _acts_partially_match(act1: str, act2: str) -> bool:
    """True when one act name is a substring of the other (case-insensitive).

    Requires both names to be at least 4 characters so trivially short strings
    (e.g. "The") don't produce false positives.  This catches common cases like
    "Singer 1" vs "Singer 1, with Singer 2" where one name simply adds support
    act information to the headline act.
    """
    a = act1.strip().lower()
    b = act2.strip().lower()
    if len(a) < 4 or len(b) < 4:
        return False
    return a in b or b in a


def _row_venue_id(row: list) -> str:
    """Venue document id when the row has been linked to the venues collection."""
    if len(row) <= _IDX_VENUE_ID:
        return ""
    return str(row[_IDX_VENUE_ID] or "").strip()


def _resolve_venue_for_row(db_name: str, row: list) -> None:
    """Ensure the row carries canonical venue text and a venues-collection link."""
    raw = str(row[_IDX_VENUE] or "").strip()
    if not raw:
        if len(row) <= _IDX_VENUE_ID:
            row.append("")
        else:
            row[_IDX_VENUE_ID] = ""
        return
    venue_id, canonical = venue_store.resolve_or_create(db_name, raw)
    row[_IDX_VENUE] = canonical
    if len(row) <= _IDX_VENUE_ID:
        row.append(venue_id)
    else:
        row[_IDX_VENUE_ID] = venue_id


def _venues_match_for_dedup(row: list, venue_id: str, venue_name: str) -> bool:
    """True when *row* and the incoming venue refer to the same place."""
    row_id = _row_venue_id(row)
    if row_id and venue_id:
        return row_id == venue_id
    row_name = str(row[_IDX_VENUE] or "").strip().lower()
    return row_name == venue_name.strip().lower()


def _find_partial_act_match(
    new_act: str,
    new_date: date,
    venue_id: str,
    venue_name: str,
    existing: dict[str, list],
) -> str | None:
    """Scan existing rows for a partial act-name match on the same venue + date.

    Venue is used as a required tie-breaker here (unlike exact-name dedup) to
    prevent merging genuinely different acts that happen to share a name fragment
    on different stages.

    Returns the row's **Event ID** dict key, or None.
    """
    for row_key, row in existing.items():
        if _row_date(row) != new_date:
            continue
        if not _venues_match_for_dedup(row, venue_id, venue_name):
            continue
        if _acts_partially_match(new_act, str(row[_IDX_EVENT] or "")):
            return row_key
    return None


def _already_have_url_and_show(
    existing: dict[str, list], url_lower: str, dk: tuple[str, date] | None
) -> bool:
    """True when the same URL already represents this act+date pair (re-ingest)."""
    if dk is None:
        return False
    for row in existing.values():
        if str(row[_IDX_URL] or "").strip().lower() != url_lower:
            continue
        if _dedup_key_from_row(row) == dk:
            return True
    return False


# ── Row ↔ Resource conversion ─────────────────────────────────────────────────


def _resource_to_row(r: Resource, *, db_name: str | None = None) -> list:
    """Convert a Resource to a list matching ``_COLS`` order."""
    act, venue, location = split_title_parts(r.title or "")
    raw_date = parse_event_sort_date(r.date)
    rid = (r.id or "").strip() or str(uuid4())
    venue_id = ""
    canonical_venue = venue
    if db_name and venue.strip():
        venue_id, canonical_venue = venue_store.resolve_or_create(db_name, venue)
        if venue_id and location.strip():
            venue_store.set_location(db_name, venue_id, location)
    return [
        act or "—",                           # Event
        canonical_venue,                      # Venue (canonical)
        location,                             # Location
        raw_date,                             # Date (Excel date cell)
        (r.url or "").strip(),                # URL
        "",                                   # Sources (populated by dedup logic)
        (r.thumbnail_url or "").strip(),      # Poster URL (hidden)
        (r.summary or "").strip(),            # Summary
        format_generated_timestamp(),         # Added
        rid,                                  # Event ID (hidden)
        venue_id,                             # Venue ID (hidden)
        [],                                   # Tags (MongoDB string array)
    ]


def _row_date(row: list) -> date | None:
    """Extract the date from a row; normalise datetime → date."""
    val = row[_IDX_DATE] if len(row) > _IDX_DATE else None
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def _row_to_resource(row: list) -> Resource:
    """Reconstruct a Resource from a spreadsheet row.

    The title is rebuilt in "Act @ Venue, Location" format so that
    ``split_title_parts`` in json_output can decompose it
    the same way it was originally split.
    """
    act      = str(row[_IDX_EVENT]    or "").strip()
    venue    = str(row[_IDX_VENUE]    or "").strip()
    location = str(row[_IDX_LOCATION] or "").strip()
    url      = str(row[_IDX_URL]      or "").strip()
    poster   = str(row[_IDX_POSTER]   or "").strip()
    summary  = str(row[_IDX_SUMMARY]  or "").strip()
    raw_date = _row_date(row)

    venue_loc = ", ".join(filter(None, [venue, location]))
    title = f"{act} @ {venue_loc}" if venue_loc else act

    eid = _row_event_id(row)
    if not eid:
        eid = str(uuid4())

    return Resource(
        id=eid,
        title=title,
        url=url,
        date=raw_date.isoformat() if raw_date else "",
        thumbnail_url=poster or None,
        summary=summary,
    )


def load_spreadsheet_resources(path: Path | None = None) -> list[Resource]:
    """Read the current event store and return rows as Resource objects.

    *path* is ignored (kept for call-site compatibility). This is the public
    API for getting the source-of-truth event list.
    """
    _ = path
    rows = _load_existing_rows(active_db_name())
    return [_row_to_resource(row) for row in rows.values()]


# ── Spreadsheet I/O ───────────────────────────────────────────────────────────


def _load_existing_rows(db_name: str | None = None) -> dict[str, list]:
    """Load events as ``{Event ID → row}`` from MongoDB."""
    name = db_name or active_db_name()
    return _db_load_rows(name)


def _write_workbook(db_name: str | None, rows: dict[str, list]) -> None:
    """Persist all rows to the topic's ``events`` collection."""
    name = db_name or active_db_name()
    _db_save_rows(name, rows)


def _pick_primary_pair(pairs: list[tuple[str, list]]) -> tuple[str, list]:
    """Prefer a row with a poster, then richer summary / title."""
    with_poster = [(uk, r) for uk, r in pairs if str(r[_IDX_POSTER] or "").strip()]
    pool = with_poster or pairs
    return max(
        pool,
        key=lambda x: (
            len(str(x[1][_IDX_SUMMARY] or "")),
            len(str(x[1][_IDX_EVENT] or "")),
        ),
    )


def _merge_cluster_rows(pairs: list[tuple[str, list]]) -> tuple[str, list]:
    """Return (canonical row storage key (= Event ID), merged row).

    Maximises text + keeps a poster URL.
    """
    primary_uk, primary_row = _pick_primary_pair(pairs)
    rows = [r for _, r in pairs]
    merged = list(primary_row)

    merged[_IDX_EVENT] = max((str(r[_IDX_EVENT] or "") for r in rows), key=len)
    merged[_IDX_VENUE] = max((str(r[_IDX_VENUE] or "") for r in rows), key=len)
    merged[_IDX_LOCATION] = max((str(r[_IDX_LOCATION] or "") for r in rows), key=len)
    venue_ids = [_row_venue_id(r) for r in rows if _row_venue_id(r)]
    if venue_ids:
        merged[_IDX_VENUE_ID] = venue_ids[0]
    elif len(merged) > _IDX_VENUE_ID:
        merged[_IDX_VENUE_ID] = ""

    summaries = [
        str(r[_IDX_SUMMARY] or "").strip()
        for r in rows
        if str(r[_IDX_SUMMARY] or "").strip()
    ]
    merged[_IDX_SUMMARY] = (
        max(summaries, key=len) if summaries else str(merged[_IDX_SUMMARY] or "")
    )

    poster = ""
    for r in rows:
        p = str(r[_IDX_POSTER] or "").strip()
        if p:
            poster = p
            break
    merged[_IDX_POSTER] = poster

    merged[_IDX_EVENT_ID] = str(primary_row[_IDX_EVENT_ID] or "").strip() or str(uuid4())
    merged[_IDX_DATE] = primary_row[_IDX_DATE]
    merged[_IDX_URL] = str(primary_row[_IDX_URL] or "").strip()

    merged[_IDX_SOURCES] = str(primary_row[_IDX_SOURCES] or "").strip()
    for uk, r in pairs:
        if uk == primary_uk:
            continue
        u = str(r[_IDX_URL] or "").strip()
        if u:
            _add_source(merged, u)
        for line in str(r[_IDX_SOURCES] or "").split("\n"):
            u2 = line.strip()
            if u2.startswith("http"):
                _add_source(merged, u2)

    return primary_uk, merged


def run_llm_semantic_dedupe(db_name: str | None = None) -> int:
    """Second-pass dedup: LLM clusters same-day semantic duplicates; merge rows.

    Returns the number of event rows removed (not counting the kept row).
    """
    from agent.semantic_dedupe import find_same_event_clusters

    name = db_name or active_db_name()
    existing = _load_existing_rows(name)
    if len(existing) < 2:
        return 0

    events: list[dict] = []
    for _uk, row in existing.items():
        d = _row_date(row)
        events.append(
            {
                "id": _row_event_id(row),
                "name": str(row[_IDX_EVENT] or ""),
                "venue": str(row[_IDX_VENUE] or ""),
                "location": str(row[_IDX_LOCATION] or ""),
                "date": d.isoformat() if d else "",
                "url": str(row[_IDX_URL] or ""),
                "summary": str(row[_IDX_SUMMARY] or ""),
                "poster_url": str(row[_IDX_POSTER] or ""),
            }
        )

    clusters = find_same_event_clusters(events)
    if not clusters:
        return 0

    removed = 0
    for cluster in clusters:
        id_set = {i.strip() for i in cluster if i.strip()}
        if len(id_set) < 2:
            continue
        pairs = [(uk, row) for uk, row in existing.items() if _row_event_id(row) in id_set]
        if len(pairs) < 2:
            continue
        primary_uk, merged = _merge_cluster_rows(pairs)
        for uk, _ in pairs:
            if uk != primary_uk:
                del existing[uk]
                removed += 1
        existing[primary_uk] = merged

    if removed:
        _write_workbook(name, existing)
    return removed


# ── Merge + expire + dedup ────────────────────────────────────────────────────


def merge_and_write(
    new_resources: list[Resource],
) -> tuple[int, int, int, dict[str, UrlOutcome], dict[str, int]]:
    """Merge new events into the persistent spreadsheet.

    Steps:
    1. Load existing rows from disk (keyed by Event ID).
    2. Drop rows whose event date is in the past.
    3. For each new resource:
       a. Same URL **and** identical act+date as an existing row → skip (re-ingest).
       b. Same act name + date (venue ignored) → exact semantic duplicate:
          add URL to Sources when domain differs.
       c. One act name is a substring of the other + same venue + same date →
          partial-name duplicate: keep the longer act name; add URL to Sources.
       d. Otherwise → insert as a new row (several rows may share one listing URL).
    4. Sort soonest-first and save.

    Returns:
        (added, skipped_duplicate, removed_past, url_outcomes, url_distinct_counts)
        counts and per-URL tallies.
    """
    db_name = active_db_name()
    today = local_today()
    url_outcomes: dict[str, UrlOutcome] = {}
    url_distinct_keys: dict[str, set[DistinctEventKey]] = {}

    existing = _load_existing_rows(db_name)
    for row in existing.values():
        if not _row_venue_id(row) and str(row[_IDX_VENUE] or "").strip():
            _resolve_venue_for_row(db_name, row)

    # Remove past events
    before = len(existing)
    existing = {k: v for k, v in existing.items() if (_row_date(v) or date.max) >= today}
    removed_past = before - len(existing)

    # Build semantic dedup index: act+date → row key (Event ID)
    dedup_index: dict[tuple[str, date], str] = {}
    for row_key, row in existing.items():
        dk = _dedup_key_from_row(row)
        if dk:
            dedup_index[dk] = row_key

    added = 0
    skipped = 0
    for r in new_resources:
        url = (r.url or "").strip()
        url_key = url.lower()
        if not url_key.startswith("http"):
            continue

        # Decompose title once — used by several checks below.
        act, venue, _location = split_title_parts(r.title or "")
        r_date = parse_event_sort_date(r.date)
        venue_id = ""
        canonical_venue = venue
        if venue.strip():
            venue_id, canonical_venue = venue_store.resolve_or_create(db_name, venue)

        dk = _dedup_key_from_resource(r)
        new_poster = (r.thumbnail_url or "").strip() or None

        # (a) Identical gig re-submitted — same portal URL cannot mean duplicate *shows*
        #     anymore; skip only when act+date also match an existing URL row.
        if _already_have_url_and_show(existing, url_key, dk):
            # Re-ingests still get a chance to upgrade a stale logo/ad poster
            # to the fresh thumbnail produced by the new (improved) Pass 1.
            for row_key, row in existing.items():
                if (
                    str(row[_IDX_URL] or "").strip().lower() == url_key
                    and _dedup_key_from_row(row) == dk
                ):
                    if _maybe_upgrade_poster(row, new_poster, act):
                        logger.debug(
                            "URL re-ingest for '%s' — upgraded Poster URL.", r.title,
                        )
                    break
            note_url_event(url_outcomes, url_distinct_keys, url, dk, added=False)
            skipped += 1
            continue

        # (b) Exact semantic duplicate — same act name + date (venue ignored).
        if dk and dk in dedup_index:
            match_key = dedup_index[dk]
            source_added = _add_source(existing[match_key], url)
            if source_added:
                logger.debug(
                    "Exact-name duplicate for '%s' — added %s to Sources.",
                    r.title, url,
                )
            if _maybe_upgrade_poster(existing[match_key], new_poster, act):
                logger.debug(
                    "Exact-name duplicate for '%s' — upgraded Poster URL.", r.title,
                )
            note_url_event(url_outcomes, url_distinct_keys, url, dk, added=False)
            skipped += 1
            continue

        # (c) Partial-name duplicate — one act name contains the other, AND the
        #     venue + date are identical.  Keep the longer (more informative)
        #     act name as the canonical one.
        if act and r_date:
            partial_key = _find_partial_act_match(
                act, r_date, venue_id, canonical_venue, existing
            )
            if partial_key:
                existing_act = str(existing[partial_key][_IDX_EVENT] or "")
                if len(act) > len(existing_act):
                    existing[partial_key][_IDX_EVENT] = act
                    logger.debug(
                        "Partial-name duplicate: upgraded canonical name '%s' → '%s'.",
                        existing_act, act,
                    )
                _add_source(existing[partial_key], url)
                # Score the poster against the *canonical* (longer) act name
                # so e.g. "The Beths, with Wax Chattels" still rewards posters
                # whose filename mentions "Beths" or "Wax Chattels".
                canonical_act = max(act, existing_act, key=len)
                if _maybe_upgrade_poster(
                    existing[partial_key], new_poster, canonical_act
                ):
                    logger.debug(
                        "Partial-name duplicate for '%s' — upgraded Poster URL.",
                        r.title,
                    )
                note_url_event(url_outcomes, url_distinct_keys, url, dk, added=False)
                skipped += 1
                continue

        # (d) Genuinely new event
        new_row = _resource_to_row(r, db_name=db_name)
        sid = str(new_row[_IDX_EVENT_ID] or "").strip()
        if not sid:
            sid = str(uuid4())
            new_row[_IDX_EVENT_ID] = sid
        while sid in existing:
            sid = str(uuid4())
            new_row[_IDX_EVENT_ID] = sid
        existing[sid] = new_row
        if dk:
            dedup_index[dk] = sid
        note_url_event(url_outcomes, url_distinct_keys, url, dk, added=True)
        added += 1

    _write_workbook(db_name, existing)
    logger.info(
        "Events DB: +%d added, %d duplicate/skipped, %d expired removed → %d total",
        added, skipped, removed_past, len(existing),
    )
    return added, skipped, removed_past, url_outcomes, distinct_event_counts(url_distinct_keys)


# ── Public API (called from graph_nodes) ─────────────────────────────────────


def write_output(resources: list[Resource]) -> MergeStats:
    """Merge events in MongoDB, apply exclusions, cache posters, then dedupe.

    Event exclusions run **after** ``merge_and_write`` via ``apply_event_exclusions``:
    deterministic ``drop_terms`` plus optional LLM phrase rules over every row.

    The Angular UI reads from the HTTP API backed by the same database.

    Per-run reports are written to MongoDB by ``report_store.save_run_report``
    from ``node_local_output``;
    it consumes the returned ``MergeStats`` so each report can show a count
    of events added, skipped, pruned, exclusion-dropped, and de-duplicated.

    Posters are downloaded into the ``images`` collection so the Angular app
    loads them same-origin via ``/api/<db>/images/...``.
    """
    from agent.image_cache import cache_thumbnails, garbage_collect

    db_name = active_db_name()

    added, skipped, removed_past, url_outcomes, url_distinct_counts = merge_and_write(resources)

    removed_exclusion = 0
    try:
        from agent.exclusion_prune import apply_event_exclusions

        removed_exclusion = apply_event_exclusions()
    except Exception as exc:
        logger.warning("Event exclusions skipped: %s", exc)
        removed_exclusion = 0

    tagged = 0
    try:
        from agent.event_tagging import apply_event_tags

        tagged = apply_event_tags(db_name)
        if tagged:
            logger.info("Tagged %d event row(s) via LLM.", tagged)
    except Exception as exc:
        logger.warning("Event tagging skipped: %s", exc)
        tagged = 0

    synced = load_spreadsheet_resources()
    synced = cache_thumbnails(synced, db_name=db_name)
    garbage_collect({r.id for r in synced}, db_name=db_name)

    removed_dedupe = 0
    if config.llm_inference_enabled():
        try:
            removed_dedupe = run_llm_semantic_dedupe(db_name)
            if removed_dedupe:
                logger.info(
                    "LLM semantic dedupe removed %d duplicate event row(s).",
                    removed_dedupe,
                )
                synced = load_spreadsheet_resources()
                synced = cache_thumbnails(synced, db_name=db_name)
                garbage_collect({r.id for r in synced}, db_name=db_name)
        except Exception as exc:
            logger.warning("LLM semantic dedupe skipped: %s", exc)
            removed_dedupe = 0

    # Record each venue's latest event date (Task 1) so future runs know how
    # far ahead a venue's listings already reach.
    try:
        from agent import venue_store

        venue_store.update_last_event_dates(db_name)
    except Exception as exc:
        logger.warning("Venue last_event_date update skipped: %s", exc)

    removed_orphan_venues = 0
    try:
        from agent import venue_store

        removed_orphan_venues = venue_store.delete_venues_without_events(db_name)
    except Exception as exc:
        logger.warning("Venue tidy-up skipped: %s", exc)

    total_after = len(synced)
    return MergeStats(
        added=added,
        skipped=skipped,
        removed_past=removed_past,
        removed_exclusion=removed_exclusion,
        removed_dedupe=removed_dedupe,
        removed_orphan_venues=removed_orphan_venues,
        total_after=total_after,
        url_outcomes=url_outcomes,
        url_distinct_event_counts=url_distinct_counts,
    )
