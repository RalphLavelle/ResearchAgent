"""Deterministic strategy scorecards for recursive self-improvement.

The scorecards are intentionally simple counters derived from data the run
already produced. Later planner/crawler steps can read them without asking the
LLM to invent strategy changes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Literal, Mapping, TypedDict

from agent.local_output import MergeStats
from agent.mongodb import STRATEGY_SCORES_COLLECTION, get_database
from agent.source_store import host_from_url, normalize_source_url

ScoreKind = Literal["source_url", "host", "venue", "query"]

# Minimum weight so every entity keeps some exploration chance.
EXPLORATION_FLOOR = 0.15
# Per-week decay factor applied from ``last_seen`` (matches docs/ideas/02. sri sketch).
DECAY_PER_WEEK = 0.85


class ScoreDelta(TypedDict, total=False):
    """One idempotent score update derived from a single report."""

    kind: ScoreKind
    key: str
    label: str
    events_added: int
    events_seen: int
    duplicates: int
    zero_yield_runs: int
    metadata: dict[str, str]


def _coerce_utc_iso(raw: Any) -> str:
    """Return an ISO UTC timestamp from report data or now."""
    if isinstance(raw, datetime):
        moment = raw
    else:
        text = str(raw or "").strip()
        if text:
            try:
                moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                moment = datetime.now(timezone.utc)
        else:
            moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _safe_int(raw: Any) -> int:
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _normalise_query(raw: Any) -> tuple[str, str]:
    label = " ".join(str(raw or "").strip().split())
    return label.lower(), label


def _score_id(kind: ScoreKind, key: str) -> str:
    return f"{kind}:{key}"


def _source_url_deltas(
    report_doc: dict[str, Any],
    merge_stats: MergeStats | None,
) -> list[ScoreDelta]:
    urls_by_host = report_doc.get("urls") or {}
    crawled: list[str] = []
    if isinstance(urls_by_host, dict):
        for urls in urls_by_host.values():
            crawled.extend(str(url) for url in (urls or []))

    outcomes = (merge_stats.url_outcomes if merge_stats else {}) or {}
    by_url: dict[str, tuple[int, int]] = {}
    for raw_url, outcome in outcomes.items():
        url = normalize_source_url(raw_url)
        if not url:
            continue
        added, seen = outcome
        by_url[url] = (_safe_int(added), _safe_int(seen))

    ordered_urls: list[str] = []
    seen_urls: set[str] = set()
    for raw_url in [*crawled, *by_url.keys()]:
        url = normalize_source_url(raw_url)
        if not url or url in seen_urls:
            continue
        ordered_urls.append(url)
        seen_urls.add(url)

    rows: list[ScoreDelta] = []
    for url in ordered_urls:
        added, seen = by_url.get(url, (0, 0))
        duplicates = max(0, seen - added)
        rows.append(
            {
                "kind": "source_url",
                "key": url,
                "label": url,
                "events_added": added,
                "events_seen": seen,
                "duplicates": duplicates,
                "zero_yield_runs": 1 if added == 0 else 0,
                "metadata": {"host": host_from_url(url)},
            }
        )
    return rows


def _host_deltas(source_rows: list[ScoreDelta]) -> list[ScoreDelta]:
    grouped: dict[str, dict[str, int]] = defaultdict(
        lambda: {"events_added": 0, "events_seen": 0, "duplicates": 0}
    )
    for row in source_rows:
        host = str((row.get("metadata") or {}).get("host") or "").strip()
        if not host:
            continue
        grouped[host]["events_added"] += _safe_int(row.get("events_added"))
        grouped[host]["events_seen"] += _safe_int(row.get("events_seen"))
        grouped[host]["duplicates"] += _safe_int(row.get("duplicates"))

    rows: list[ScoreDelta] = []
    for host, totals in grouped.items():
        rows.append(
            {
                "kind": "host",
                "key": host,
                "label": host,
                "events_added": totals["events_added"],
                "events_seen": totals["events_seen"],
                "duplicates": totals["duplicates"],
                "zero_yield_runs": 1 if totals["events_added"] == 0 else 0,
            }
        )
    return rows


def _venue_deltas(merge_stats: MergeStats | None) -> list[ScoreDelta]:
    rows: list[ScoreDelta] = []
    for key, outcome in ((merge_stats.venue_outcomes if merge_stats else {}) or {}).items():
        label = str(outcome.get("name") or key).strip()
        venue_id = str(outcome.get("venue_id") or "").strip()
        score_key = venue_id or label.lower()
        if not score_key:
            continue
        added = _safe_int(outcome.get("events_added"))
        seen = _safe_int(outcome.get("events_seen"))
        duplicates = _safe_int(outcome.get("duplicates"))
        rows.append(
            {
                "kind": "venue",
                "key": score_key,
                "label": label or score_key,
                "events_added": added,
                "events_seen": seen,
                "duplicates": duplicates,
                "zero_yield_runs": 1 if added == 0 else 0,
                "metadata": {"venue_id": venue_id},
            }
        )
    return rows


def _query_deltas(report_doc: dict[str, Any], merge_stats: MergeStats | None) -> list[ScoreDelta]:
    if merge_stats is None:
        added = seen = duplicates = 0
    else:
        added = _safe_int(merge_stats.added)
        duplicates = _safe_int(merge_stats.skipped)
        seen = added + duplicates

    rows: list[ScoreDelta] = []
    seen_queries: set[str] = set()
    for raw_query in report_doc.get("searches") or []:
        key, label = _normalise_query(raw_query)
        if not key or key in seen_queries:
            continue
        seen_queries.add(key)
        rows.append(
            {
                "kind": "query",
                "key": key,
                "label": label,
                "events_added": added,
                "events_seen": seen,
                "duplicates": duplicates,
                "zero_yield_runs": 1 if added == 0 else 0,
            }
        )
    return rows


def build_score_deltas(
    report_doc: dict[str, Any],
    merge_stats: MergeStats | None,
) -> list[ScoreDelta]:
    """Build every score update for one completed run."""
    source_rows = _source_url_deltas(report_doc, merge_stats)
    return [
        *source_rows,
        *_host_deltas(source_rows),
        *_venue_deltas(merge_stats),
        *_query_deltas(report_doc, merge_stats),
    ]


def apply_strategy_scores_for_report(
    db_name: str,
    *,
    report_id: str,
    report_doc: dict[str, Any],
    merge_stats: MergeStats | None,
) -> int:
    """Persist scorecard counters once for *report_id*.

    Idempotency is per score document: if a report id is already present in
    ``applied_report_ids`` for that score, the counter update is skipped.
    """
    rid = str(report_id or "").strip()
    if not rid:
        return 0

    coll = get_database(db_name)[STRATEGY_SCORES_COLLECTION]
    iso = _coerce_utc_iso(report_doc.get("datetime"))
    touched = 0
    for delta in build_score_deltas(report_doc, merge_stats):
        kind = delta["kind"]
        key = delta["key"]
        doc_id = _score_id(kind, key)
        existing = coll.find_one({"_id": doc_id}, {"applied_report_ids": 1})
        if existing and rid in set(existing.get("applied_report_ids") or []):
            continue

        metadata = delta.get("metadata") or {}
        update: dict[str, Any] = {
            "$set": {
                "kind": kind,
                "key": key,
                "label": delta["label"],
                "last_seen": iso,
                "metadata": metadata,
            },
            "$setOnInsert": {
                "first_seen": iso,
            },
            "$inc": {
                "runs": 1,
                "events_added": _safe_int(delta.get("events_added")),
                "events_seen": _safe_int(delta.get("events_seen")),
                "duplicates": _safe_int(delta.get("duplicates")),
                "zero_yield_runs": _safe_int(delta.get("zero_yield_runs")),
            },
            "$addToSet": {"applied_report_ids": rid},
        }
        coll.update_one({"_id": doc_id}, update, upsert=True)
        touched += 1
    return touched


def serialize_score(doc: dict[str, Any]) -> dict[str, Any]:
    """Return one scorecard document in API-safe JSON shape."""
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc.get("_id", ""))
    out["runs"] = _safe_int(out.get("runs"))
    out["events_added"] = _safe_int(out.get("events_added"))
    out["events_seen"] = _safe_int(out.get("events_seen"))
    out["duplicates"] = _safe_int(out.get("duplicates"))
    out["zero_yield_runs"] = _safe_int(out.get("zero_yield_runs"))
    return out


def list_strategy_scores(
    db_name: str,
    *,
    kind: ScoreKind | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return scorecards sorted by kind and strongest observed yield."""
    query: dict[str, Any] = {}
    if kind:
        query["kind"] = kind
    cursor = (
        get_database(db_name)[STRATEGY_SCORES_COLLECTION]
        .find(query)
        .sort([("kind", 1), ("events_added", -1), ("events_seen", -1), ("label", 1)])
        .limit(max(1, limit))
    )
    return [serialize_score(doc) for doc in cursor]


def load_scores_by_key(db_name: str, kind: ScoreKind) -> dict[str, dict[str, Any]]:
    """Return raw score documents keyed by their strategy ``key`` field."""
    rows: dict[str, dict[str, Any]] = {}
    for doc in get_database(db_name)[STRATEGY_SCORES_COLLECTION].find({"kind": kind}):
        key = str(doc.get("key") or "").strip()
        if key:
            rows[key] = doc
    return rows


def _weeks_since(raw: Any, *, now: datetime) -> float:
    """Return fractional weeks between *raw* ISO timestamp and *now*."""
    iso = _coerce_utc_iso(raw)
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = now.astimezone(timezone.utc) - moment.astimezone(timezone.utc)
    return max(0.0, delta.total_seconds() / (7 * 24 * 3600))


def score_weight(
    doc: Mapping[str, Any],
    *,
    now: datetime | None = None,
    exploration_floor: float = EXPLORATION_FLOOR,
) -> float:
    """Convert one scorecard into a non-negative selection weight."""
    added = _safe_int(doc.get("events_added"))
    seen = _safe_int(doc.get("events_seen"))
    zero_yield = _safe_int(doc.get("zero_yield_runs"))
    base = 1.0 + 1.5 * math.sqrt(added) + 0.25 * math.sqrt(seen) - 1.0 * zero_yield

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    weeks = _weeks_since(doc.get("last_seen"), now=moment)
    decayed = base * (DECAY_PER_WEEK**weeks)
    return max(exploration_floor, decayed)


def _venue_score_lookup(
    venue: Mapping[str, Any],
    scores_by_key: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Find a venue score doc by ``venue_id`` first, then normalised name."""
    venue_id = str(venue.get("_id") or venue.get("venue_id") or "").strip()
    if venue_id and venue_id in scores_by_key:
        return scores_by_key[venue_id]
    name_key = str(venue.get("name") or "").strip().lower()
    if name_key and name_key in scores_by_key:
        return scores_by_key[name_key]
    return None


def venue_selection_weight(
    venue: Mapping[str, Any],
    scores_by_key: Mapping[str, Mapping[str, Any]],
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> float:
    """Blend historical venue yield with freshness signals for query sampling."""
    score_doc = _venue_score_lookup(venue, scores_by_key)
    if score_doc is not None:
        weight = score_weight(score_doc, now=now)
    else:
        weight = 1.0

    # Discovery boosts: unlinked venues and never-mined venues rank higher.
    if not str(venue.get("events_link") or "").strip():
        weight += 0.5
    if not str(venue.get("last_mined") or "").strip():
        weight += 0.5

    # Weak future coverage: missing or soon ``last_event_date``.
    base = today or (now or datetime.now(timezone.utc)).date()
    last_event_raw = str(venue.get("last_event_date") or "").strip()[:10]
    if not last_event_raw:
        weight += 0.75
    else:
        try:
            last_event = date.fromisoformat(last_event_raw)
            days_until = (last_event - base).days
            if days_until < 30:
                weight += 0.5
        except ValueError:
            weight += 0.75

    return max(EXPLORATION_FLOOR, weight)


def build_venue_query_weights(
    db_name: str,
    venues: list[Mapping[str, Any]],
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> dict[str, float]:
    """Return per-venue weights keyed by ``venue_id`` or lowercased name."""
    scores_by_key = load_scores_by_key(db_name, "venue")
    weights: dict[str, float] = {}
    for venue in venues:
        venue_id = str(venue.get("_id") or venue.get("venue_id") or "").strip()
        name_key = str(venue.get("name") or "").strip().lower()
        key = venue_id or name_key
        if not key:
            continue
        weights[key] = venue_selection_weight(
            venue,
            scores_by_key,
            today=today,
            now=now,
        )
    return weights
