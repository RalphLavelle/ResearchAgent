"""MongoDB persistence for URLs that have yielded curated events.

Documents are grouped by host — one row per site, with a ``urls`` array of
subdocuments for each fruitful page. At the start of a crawl step the pipeline
may pick one remembered URL as a weighted-random extra seed.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

from agent.mongodb import SOURCES_COLLECTION, get_database

logger = logging.getLogger(__name__)

# (events_added, events_seen) tallies for one URL in a single pipeline run.
UrlOutcome = tuple[int, int]

# Distinct-event key: normalised act name + event date (same shape as merge dedup).
DistinctEventKey = tuple[str, date]


def normalize_source_url(url: str) -> str:
    """Normalise a URL for stable storage (lowercase host, no fragment/trailing slash)."""
    raw = (url or "").strip()
    if not raw.lower().startswith("http"):
        return ""
    parsed = urlparse(raw)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, netloc, path, parsed.params, parsed.query, ""))


def host_from_url(url: str) -> str:
    """Return lowercase host for grouping, or a placeholder when missing."""
    try:
        return urlparse(url).netloc.lower() or "(unknown host)"
    except ValueError:
        return "(unparsable URL)"


def compute_source_weight(events_added: int, events_seen: int) -> float:
    """Sub-linear weight so high-yield URLs are favoured but not dominant."""
    added = max(0, events_added)
    seen = max(0, events_seen)
    return 1.0 + math.sqrt(added) + 0.25 * math.sqrt(seen)


def group_sources_by_host(docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return host → URL subdocuments from grouped source documents."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        host = str(doc.get("host") or "").strip()
        if not host:
            continue
        grouped[host] = list(doc.get("urls") or [])
    return grouped


def _new_url_subdoc(
    url: str,
    *,
    added: int,
    seen: int,
    iso: str,
) -> dict[str, Any]:
    """Shape one URL entry when first seen under a host."""
    return {
        "url": url,
        "events_added": max(0, added),
        "events_seen": max(0, seen),
        "runs_contributed": 1,
        "first_seen": iso,
        "last_seen": iso,
    }


def _iter_weighted_url_entries(
    host_docs: list[dict[str, Any]],
) -> list[tuple[str, str, float]]:
    """Flatten host documents into (host, url, weight) tuples."""
    pool: list[tuple[str, str, float]] = []
    for doc in host_docs:
        host = str(doc.get("host") or "").strip()
        if not host:
            continue
        for entry in doc.get("urls") or []:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            seen = int(entry.get("events_seen") or 0)
            if seen <= 0:
                continue
            weight = compute_source_weight(
                int(entry.get("events_added") or 0),
                seen,
            )
            pool.append((host, url, weight))
    return pool


def is_stale_source_entry(entry: dict[str, Any]) -> bool:
    """True when a URL has been revisited often without yielding new events."""
    runs = int(entry.get("runs_contributed") or 0)
    added = int(entry.get("events_added") or 0)
    return runs > 3 * max(added, 0)


def note_url_event(
    outcomes: dict[str, UrlOutcome],
    distinct_keys: dict[str, set[DistinctEventKey]],
    url: str,
    event_key: DistinctEventKey | None,
    *,
    added: bool,
) -> None:
    """Record one curator resource and its distinct (act, date) key for a URL."""
    key = normalize_source_url(url)
    if not key:
        return
    prev_added, prev_seen = outcomes.get(key, (0, 0))
    outcomes[key] = (
        prev_added + (1 if added else 0),
        prev_seen + 1,
    )
    if event_key is not None:
        distinct_keys.setdefault(key, set()).add(event_key)


def distinct_event_counts(distinct_keys: dict[str, set[DistinctEventKey]]) -> dict[str, int]:
    """Return per-URL counts of distinct events seen in one merge pass."""
    return {url: len(keys) for url, keys in distinct_keys.items()}


def _remove_url_entry(coll, host: str, url: str) -> None:
    """Drop one URL subdocument; delete the host row when ``urls`` becomes empty."""
    coll.update_one({"host": host}, {"$pull": {"urls": {"url": url}}})
    doc = coll.find_one({"host": host}, projection={"urls": 1})
    if doc is not None and not doc.get("urls"):
        coll.delete_one({"host": host})


def _maybe_prune_stale_url(coll, host: str, url: str) -> bool:
    """Delete a URL entry when ``runs_contributed`` exceeds ``3 * events_added``."""
    doc = coll.find_one({"host": host})
    if not doc:
        return False
    for entry in doc.get("urls") or []:
        if str(entry.get("url") or "") != url:
            continue
        if not is_stale_source_entry(entry):
            return False
        _remove_url_entry(coll, host, url)
        logger.info("Pruned stale source URL (host=%s): %s", host, url)
        return True
    return False


def prune_stale_source_urls(db_name: str) -> int:
    """Remove stale URL entries across the collection. Returns URLs removed."""
    coll = get_database(db_name)[SOURCES_COLLECTION]
    removed = 0
    for doc in coll.find({}, projection={"host": 1, "urls": 1}):
        host = str(doc.get("host") or "")
        for entry in list(doc.get("urls") or []):
            url = str(entry.get("url") or "")
            if not url or not is_stale_source_entry(entry):
                continue
            _remove_url_entry(coll, host, url)
            removed += 1
    if removed:
        logger.info(
            "Pruned %d stale source URL(s) from db=%s collection=%s",
            removed,
            db_name,
            SOURCES_COLLECTION,
        )
    return removed


def record_url_outcomes(
    db_name: str,
    outcomes: dict[str, UrlOutcome],
    *,
    distinct_counts: dict[str, int] | None = None,
    when: datetime | None = None,
) -> int:
    """Upsert cumulative counts for listing URLs that yielded 2+ distinct events.

    Single-event pages (e.g. one Songkick concert URL) are skipped. After each
    update, entries where ``runs_contributed > 3 * events_added`` are deleted.

    Returns the number of URL subdocuments touched (excluding pruned rows).
    """
    if not outcomes:
        prune_stale_source_urls(db_name)
        return 0

    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    iso = moment.isoformat()

    counts = distinct_counts or {}
    coll = get_database(db_name)[SOURCES_COLLECTION]
    touched = 0
    for raw_url, (added, seen) in outcomes.items():
        if added <= 0 and seen <= 0:
            continue
        url = normalize_source_url(raw_url)
        if not url:
            continue
        if counts.get(url, 0) <= 1:
            continue
        host = host_from_url(url)

        updated = coll.update_one(
            {"host": host, "urls.url": url},
            {
                "$inc": {
                    "urls.$.events_added": max(0, added),
                    "urls.$.events_seen": max(0, seen),
                    "urls.$.runs_contributed": 1,
                },
                "$set": {
                    "urls.$.last_seen": iso,
                    "last_seen": iso,
                },
            },
        )
        if updated.matched_count:
            if not _maybe_prune_stale_url(coll, host, url):
                touched += 1
            continue

        coll.update_one(
            {"host": host},
            {
                "$push": {"urls": _new_url_subdoc(url, added=added, seen=seen, iso=iso)},
                "$set": {"last_seen": iso},
                "$setOnInsert": {"host": host, "first_seen": iso},
            },
            upsert=True,
        )
        touched += 1

    prune_stale_source_urls(db_name)

    if touched:
        logger.info(
            "Recorded outcomes for %d multi-event source URL(s) in db=%s collection=%s",
            touched,
            db_name,
            SOURCES_COLLECTION,
        )
    return touched


def pick_weighted_seed_url(
    db_name: str,
    *,
    rng: random.Random | None = None,
) -> str | None:
    """Return one remembered URL, weighted slightly by past event yield."""
    coll = get_database(db_name)[SOURCES_COLLECTION]
    host_docs = list(coll.find({}, projection={"host": 1, "urls": 1}))
    pool = _iter_weighted_url_entries(host_docs)
    if not pool:
        return None

    hosts = [item[0] for item in pool]
    urls = [item[1] for item in pool]
    weights = [item[2] for item in pool]

    r = rng or random.Random()
    idx = r.choices(range(len(urls)), weights=weights, k=1)[0]
    chosen_host = hosts[idx]
    chosen_url = urls[idx]

    now = datetime.now(timezone.utc).isoformat()
    coll.update_one(
        {"host": chosen_host, "urls.url": chosen_url},
        {"$set": {"urls.$.last_picked": now}},
    )
    logger.info("Memory crawl seed picked: %s", chosen_url)
    return chosen_url
