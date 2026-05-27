"""MongoDB persistence for per-run pipeline reports."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from agent.local_output import MergeStats
from agent.mongodb import REPORTS_COLLECTION, get_database

logger = logging.getLogger(__name__)


def group_urls_by_host(urls: list[str]) -> dict[str, list[str]]:
    """Group crawled URLs by host, preserving order within each host."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        if not url:
            continue
        try:
            host = urlparse(url).netloc.lower() or "(unknown host)"
        except ValueError:
            host = "(unparsable URL)"
        grouped[host].append(url)
    return dict(grouped)


def merge_stats_to_changes(stats: MergeStats | None) -> dict[str, int]:
    """Convert merge counters to the report ``changes`` document."""
    if stats is None:
        return {}
    return {
        "added (new rows)": stats.added,
        "skipped as duplicate": stats.skipped,
        "past events pruned": stats.removed_past,
        "removed by event exclusions": stats.removed_exclusion,
        "removed by llm semantic dedupe": stats.removed_dedupe,
        "total rows after merge": stats.total_after,
    }


def build_report_document(
    *,
    queries: list[str],
    crawled_urls: list[str],
    merge_stats: MergeStats | None = None,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Shape one report row before insert."""
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return {
        "datetime": moment.isoformat(),
        "searches": list(queries),
        "urls": group_urls_by_host(crawled_urls),
        "changes": merge_stats_to_changes(merge_stats),
    }


def save_run_report(
    db_name: str,
    *,
    queries: list[str],
    crawled_urls: list[str],
    merge_stats: MergeStats | None = None,
    when: datetime | None = None,
) -> str:
    """Insert a report into the topic's ``reports`` collection."""
    doc = build_report_document(
        queries=queries,
        crawled_urls=crawled_urls,
        merge_stats=merge_stats,
        when=when,
    )
    coll = get_database(db_name)[REPORTS_COLLECTION]
    result = coll.insert_one(doc)
    report_id = str(result.inserted_id)
    logger.info(
        "Run report saved to MongoDB: db=%s collection=%s id=%s",
        db_name,
        REPORTS_COLLECTION,
        report_id,
    )
    return report_id


def _serialize_report(doc: dict[str, Any]) -> dict[str, Any]:
    """API-friendly dict (ObjectId → string id field)."""
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc.get("_id", ""))
    return out


def list_reports(db_name: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return recent reports newest-first."""
    coll = get_database(db_name)[REPORTS_COLLECTION]
    cursor = coll.find().sort("datetime", -1).limit(max(1, limit))
    return [_serialize_report(doc) for doc in cursor]


def recent_search_queries(db_name: str, *, limit: int = 30) -> list[str]:
    """Distinct search strings from recent run reports, preserving newest-first order."""
    if limit <= 0:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for report in list_reports(db_name, limit=200):
        searches = list(report.get("searches") or [])
        for query in reversed(searches):
            text = str(query).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                return out
    return out
