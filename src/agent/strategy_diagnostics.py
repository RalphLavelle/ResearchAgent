"""Read-only diagnostics for recursive self-improvement strategy.

The functions here turn existing MongoDB memory into an operator-friendly
summary. They deliberately do not update scores or change pipeline behaviour;
that keeps the first self-improvement step safe and easy to inspect.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from agent.mongodb import SOURCES_COLLECTION, get_database
from agent.report_store import list_reports
from agent.source_store import compute_source_weight
from agent.venue_store import list_venues


@dataclass(frozen=True)
class RememberedSource:
    """One fruitful listing URL remembered from previous merge outcomes."""

    host: str
    url: str
    events_added: int
    events_seen: int
    runs_contributed: int
    weight: float
    last_seen: str


@dataclass(frozen=True)
class VenueCoverage:
    """Venue mining state useful for deciding where to look next."""

    venue_id: str
    name: str
    location: str
    has_events_link: bool
    last_event_date: str
    days_until_last_event: int | None
    last_mined: str


@dataclass(frozen=True)
class HostYield:
    """Recent crawl host compared with fruitful source memory."""

    host: str
    crawled_urls: int
    fruitful_urls: int
    source_events_added: int


@dataclass(frozen=True)
class RepeatedQuery:
    """A search query that appears more than once in recent reports."""

    query: str
    count: int


@dataclass(frozen=True)
class StrategyDiagnostics:
    """All read-only strategy signals for one topic database."""

    remembered_sources: list[RememberedSource]
    venue_coverage: list[VenueCoverage]
    low_yield_hosts: list[HostYield]
    repeated_queries: list[RepeatedQuery]


def _parse_iso_date(raw: Any) -> date | None:
    text = str(raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def remembered_sources(db_name: str, *, limit: int = 10) -> list[RememberedSource]:
    """Return top source URLs sorted by remembered yield weight."""

    coll = get_database(db_name)[SOURCES_COLLECTION]
    rows: list[RememberedSource] = []
    for doc in coll.find({}, {"host": 1, "urls": 1}):
        host = str(doc.get("host") or "").strip()
        if not host:
            continue
        for entry in doc.get("urls") or []:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            events_added = int(entry.get("events_added") or 0)
            events_seen = int(entry.get("events_seen") or 0)
            rows.append(
                RememberedSource(
                    host=host,
                    url=url,
                    events_added=events_added,
                    events_seen=events_seen,
                    runs_contributed=int(entry.get("runs_contributed") or 0),
                    weight=compute_source_weight(events_added, events_seen),
                    last_seen=str(entry.get("last_seen") or doc.get("last_seen") or ""),
                )
            )
    rows.sort(key=lambda row: (row.weight, row.events_added, row.events_seen), reverse=True)
    return rows[: max(0, limit)]


def venue_coverage(
    db_name: str,
    *,
    today: date | None = None,
    limit: int = 20,
) -> list[VenueCoverage]:
    """Return venues ordered by weakest future coverage first."""

    base = today or datetime.now(timezone.utc).date()
    rows: list[VenueCoverage] = []
    for doc in list_venues(db_name):
        last_event = str(doc.get("last_event_date") or "").strip()
        parsed = _parse_iso_date(last_event)
        rows.append(
            VenueCoverage(
                venue_id=str(doc.get("_id") or ""),
                name=str(doc.get("name") or "").strip(),
                location=str(doc.get("location") or "").strip(),
                has_events_link=bool(str(doc.get("events_link") or "").strip()),
                last_event_date=last_event,
                days_until_last_event=(parsed - base).days if parsed else None,
                last_mined=str(doc.get("last_mined") or ""),
            )
        )

    def sort_key(row: VenueCoverage) -> tuple[int, int, str, str]:
        # Missing dates first, then the soonest known calendar coverage.
        missing_date = 0 if row.days_until_last_event is None else 1
        days = row.days_until_last_event if row.days_until_last_event is not None else -999_999
        return (missing_date, days, row.last_mined, row.name.lower())

    rows.sort(key=sort_key)
    return rows[: max(0, limit)]


def low_yield_hosts(db_name: str, *, report_limit: int = 25, limit: int = 10) -> list[HostYield]:
    """Return recent crawled hosts with little or no fruitful source memory."""

    crawled: Counter[str] = Counter()
    for report in list_reports(db_name, limit=report_limit):
        urls_by_host = report.get("urls") or {}
        if not isinstance(urls_by_host, dict):
            continue
        for host, urls in urls_by_host.items():
            name = str(host or "").strip()
            if not name:
                continue
            crawled[name] += len(list(urls or []))

    sources_by_host: dict[str, tuple[int, int]] = {}
    for source in remembered_sources(db_name, limit=10_000):
        fruitful_urls, added = sources_by_host.get(source.host, (0, 0))
        sources_by_host[source.host] = (
            fruitful_urls + 1,
            added + source.events_added,
        )

    rows = [
        HostYield(
            host=host,
            crawled_urls=count,
            fruitful_urls=sources_by_host.get(host, (0, 0))[0],
            source_events_added=sources_by_host.get(host, (0, 0))[1],
        )
        for host, count in crawled.items()
    ]
    rows.sort(
        key=lambda row: (
            row.source_events_added,
            row.fruitful_urls,
            -row.crawled_urls,
            row.host,
        )
    )
    return rows[: max(0, limit)]


def repeated_queries(
    db_name: str,
    *,
    report_limit: int = 50,
    limit: int = 10,
) -> list[RepeatedQuery]:
    """Return repeated recent search queries, case-insensitive."""

    display: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for report in list_reports(db_name, limit=report_limit):
        for raw_query in report.get("searches") or []:
            query = str(raw_query or "").strip()
            if not query:
                continue
            key = " ".join(query.lower().split())
            display.setdefault(key, " ".join(query.split()))
            counts[key] += 1

    rows = [
        RepeatedQuery(query=display[key], count=count)
        for key, count in counts.items()
        if count > 1
    ]
    rows.sort(key=lambda row: (-row.count, row.query.lower()))
    return rows[: max(0, limit)]


def build_strategy_diagnostics(db_name: str) -> StrategyDiagnostics:
    """Collect all read-only strategy diagnostics for *db_name*."""

    return StrategyDiagnostics(
        remembered_sources=remembered_sources(db_name),
        venue_coverage=venue_coverage(db_name),
        low_yield_hosts=low_yield_hosts(db_name),
        repeated_queries=repeated_queries(db_name),
    )


def format_strategy_diagnostics(diagnostics: StrategyDiagnostics) -> str:
    """Render diagnostics as plain text for terminal use."""

    lines: list[str] = ["Recursive self-improvement diagnostics", ""]

    lines.append("Top remembered source URLs")
    if diagnostics.remembered_sources:
        for row in diagnostics.remembered_sources:
            lines.append(
                f"- {row.url} ({row.host}): added={row.events_added}, "
                f"seen={row.events_seen}, runs={row.runs_contributed}, weight={row.weight:.2f}"
            )
    else:
        lines.append("- None yet.")

    lines.extend(["", "Venues with weakest future coverage"])
    if diagnostics.venue_coverage:
        for row in diagnostics.venue_coverage:
            days = "unknown" if row.days_until_last_event is None else str(row.days_until_last_event)
            link = "yes" if row.has_events_link else "no"
            lines.append(
                f"- {row.name}: last_event_date={row.last_event_date or 'missing'}, "
                f"days_until={days}, events_link={link}, last_mined={row.last_mined or 'never'}"
            )
    else:
        lines.append("- No venues found.")

    lines.extend(["", "Recently crawled low-yield hosts"])
    if diagnostics.low_yield_hosts:
        for row in diagnostics.low_yield_hosts:
            lines.append(
                f"- {row.host}: crawled_urls={row.crawled_urls}, "
                f"fruitful_urls={row.fruitful_urls}, source_events_added={row.source_events_added}"
            )
    else:
        lines.append("- No recent crawl host data.")

    lines.extend(["", "Repeated recent search queries"])
    if diagnostics.repeated_queries:
        for row in diagnostics.repeated_queries:
            lines.append(f"- {row.query} ({row.count} runs)")
    else:
        lines.append("- No repeated queries found.")

    return "\n".join(lines)
