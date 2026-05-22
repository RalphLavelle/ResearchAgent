"""Per-run markdown report (Task 11).

Replaces the previous single ``run_log.md`` file with one self-contained
markdown report per run, named ``Run_<AEST timestamp>.md`` under the active
output directory.

Each report has three sections that mirror the three LLM-driven steps in the
pipeline plus a final summary so the user can audit what the model did:

1. **Searches** — the planner's ``PlanQueries.queries`` (capped at
   ``MAX_SEARCH_QUERIES``).
2. **Search and crawl** — every URL the bounded same-site crawler actually
   fetched, grouped by host.
3. **Normalize** — every curated ``Resource`` Pydantic model the curator LLM
   produced, serialised as JSON, with its source URL prominently shown so we
   can trace each event back to the page it came from.
4. **Spreadsheet changes** *(when stats are supplied)* — count of events
   added, skipped as duplicates, pruned for being in the past, dropped after merge by event exclusions
   (literal ``drop_terms`` plus optional LLM phrase rules), merged away by semantic dedupe, plus the
   final spreadsheet total.

The module is intentionally a small, pure builder + a thin file-writer so it
is easy to unit-test without touching the network or other modules.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from agent.display_time import display_timezone
from agent.models import Resource

if TYPE_CHECKING:  # avoid an import cycle at runtime; only used for type hints.
    from agent.local_output import MergeStats

logger = logging.getLogger(__name__)


def _now_in_display_tz() -> datetime:
    """Wrap ``datetime.now`` so tests can monkeypatch a single seam."""
    return datetime.now(display_timezone())


def _safe_filename_timestamp(now: datetime | None = None) -> str:
    """AEST timestamp safe for use in filenames (no colons, no spaces).

    Example: ``2026-05-06_19-20-15_AEST``. Windows file systems disallow ``:``
    in filenames, so the standard ISO format ``2026-05-06T19:20:15`` would be
    rejected — we substitute hyphens and underscores instead.
    """
    when = now or _now_in_display_tz()
    return when.strftime("%Y-%m-%d_%H-%M-%S_%Z")


def _human_timestamp(now: datetime | None = None) -> str:
    """Human-friendly AEST timestamp for the markdown header."""
    when = now or _now_in_display_tz()
    return when.strftime("%Y-%m-%d %H:%M:%S %Z")


def report_filename(now: datetime | None = None) -> str:
    """Return ``Run_<timestamp>.md`` for the current (or supplied) instant."""
    return f"Run_{_safe_filename_timestamp(now)}.md"


def _group_urls_by_host(urls: list[str]) -> dict[str, list[str]]:
    """Group URLs by their network location, preserving original order."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for u in urls:
        if not u:
            continue
        try:
            host = urlparse(u).netloc.lower() or "(unknown host)"
        except ValueError:
            host = "(unparsable URL)"
        grouped[host].append(u)
    return dict(grouped)


def _format_stats_section(stats: "MergeStats") -> list[str]:
    """Render the spreadsheet-change footer as markdown table lines."""
    rows = [
        ("Added (new rows)", stats.added),
        ("Skipped as duplicate", stats.skipped),
        ("Past events pruned", stats.removed_past),
        ("Removed by event exclusions", stats.removed_exclusion),
        ("Removed by LLM semantic dedupe", stats.removed_dedupe),
        ("Total rows after merge", stats.total_after),
    ]
    parts: list[str] = []
    parts.append("## 4. Spreadsheet changes")
    parts.append("")
    parts.append(
        "Counts from `merge_and_write`, post-merge `apply_event_exclusions`, "
        "and semantic-dedupe — under `OUTPUT_DIR/agent_research.xlsx`."
    )
    parts.append("")
    parts.append("| Metric | Count |")
    parts.append("|--------|------:|")
    for label, value in rows:
        parts.append(f"| {label} | {value} |")
    parts.append("")
    return parts


def build_run_report(
    *,
    queries: list[str],
    crawled_urls: list[str],
    resources: list[Resource],
    merge_stats: "MergeStats | None" = None,
    now: datetime | None = None,
) -> str:
    """Produce the markdown body of one per-run report.

    Pure function: no I/O. Tests construct the inputs directly and assert on
    the returned string so this stays predictable to maintain.
    """
    when = _human_timestamp(now)
    parts: list[str] = []
    parts.append(f"# Run report — {when}")
    parts.append("")
    parts.append(
        "Generated automatically. Each section corresponds to one LLM-driven "
        "step in the pipeline so you can see exactly what the model decided."
    )
    parts.append("")

    # ── Section 1: Searches ─────────────────────────────────────────────
    parts.append("## 1. Searches")
    parts.append("")
    parts.append(
        "Queries the **planner LLM** produced via `PlanQueries.queries`. "
        "Capped at `MAX_SEARCH_QUERIES` (default 8)."
    )
    parts.append("")
    if queries:
        for q in queries:
            parts.append(f"- {q}")
    else:
        parts.append("_No queries produced (planner skipped or failed)._")
    parts.append("")

    # ── Section 2: Search and crawl ─────────────────────────────────────
    parts.append("## 2. Search and crawl")
    parts.append("")
    parts.append(
        "URLs the **bounded same-site crawler** actually fetched (HTTP 200 + "
        "HTML content), grouped by host. Seeds are extracted from DuckDuckGo "
        "`link:` lines; same-host links are followed up to `MAX_CRAWL_DEPTH`."
    )
    parts.append("")
    if crawled_urls:
        grouped = _group_urls_by_host(crawled_urls)
        for host in sorted(grouped):
            host_urls = grouped[host]
            parts.append(f"### {host} — {len(host_urls)} page(s)")
            for u in host_urls:
                parts.append(f"- {u}")
            parts.append("")
    else:
        parts.append("_No URLs were crawled (crawl disabled, no seeds, or all fetches failed)._")
        parts.append("")

    # ── Section 3: Normalize ────────────────────────────────────────────
    parts.append("## 3. Normalize")
    parts.append("")
    parts.append(
        "`Resource` Pydantic models the **curator LLM** produced from the "
        "search + crawl text. Each entry shows the source URL plus the full "
        "serialised model so you can analyse which URL generated each event."
    )
    parts.append("")
    if resources:
        for i, r in enumerate(resources, start=1):
            title = r.title or "(untitled)"
            url = r.url or "(no url)"
            parts.append(f"### {i}. {title}")
            parts.append("")
            parts.append(f"- **Source URL:** {url}")
            parts.append("- **Pydantic fields:**")
            parts.append("")
            payload = json.dumps(
                r.model_dump(),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            parts.append("```json")
            parts.append(payload)
            parts.append("```")
            parts.append("")
    else:
        parts.append("_No resources curated this run._")
        parts.append("")

    # ── Section 4: Spreadsheet changes (optional) ───────────────────────
    if merge_stats is not None:
        parts.extend(_format_stats_section(merge_stats))

    return "\n".join(parts)


def write_run_report(
    out_dir: Path,
    *,
    queries: list[str],
    crawled_urls: list[str],
    resources: list[Resource],
    merge_stats: "MergeStats | None" = None,
    now: datetime | None = None,
) -> Path:
    """Write a ``Run_<timestamp>.md`` file under ``out_dir`` and return its path.

    The directory is created if missing. The file is overwritten only if the
    same timestamped filename already exists — collisions are extremely rare
    because seconds are part of the name. When ``merge_stats`` is supplied the
    file ends with a "Spreadsheet changes" table.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / report_filename(now)
    body = build_run_report(
        queries=queries,
        crawled_urls=crawled_urls,
        resources=resources,
        merge_stats=merge_stats,
        now=now,
    )
    path.write_text(body, encoding="utf-8")
    logger.info("Run report written: %s", path.resolve())
    return path
