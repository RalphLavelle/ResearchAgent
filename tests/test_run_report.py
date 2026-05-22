"""Unit tests for the per-run report (Task 11).

These tests cover the pure builder (`build_run_report`) and the file-writing
wrapper (`write_run_report`). Nothing touches the network or other agent
modules — we construct ``Resource`` objects and a fixed ``datetime`` so the
output is deterministic.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent.local_output import MergeStats
from agent.models import Resource
from agent.run_report import (
    _group_urls_by_host,
    build_run_report,
    report_filename,
    write_run_report,
)


# Brisbane is the project's default display timezone — use it everywhere here.
_AEST = ZoneInfo("Australia/Brisbane")
_FIXED_NOW = datetime(2026, 5, 6, 19, 20, 15, tzinfo=_AEST)


# ── Filename ──────────────────────────────────────────────────────────────────


def test_report_filename_uses_safe_chars() -> None:
    """Windows file systems disallow ``:`` — make sure the timestamp doesn't include one."""
    name = report_filename(_FIXED_NOW)
    assert name.startswith("Run_")
    assert name.endswith(".md")
    assert ":" not in name
    assert " " not in name
    assert "2026-05-06" in name


def test_report_filename_contains_timezone_label() -> None:
    """Filename should label the timezone so two regions don't collide silently."""
    name = report_filename(_FIXED_NOW)
    # %Z for Brisbane resolves to "AEST" on supported platforms.
    assert "AEST" in name


# ── Host grouping ─────────────────────────────────────────────────────────────


def test_group_urls_by_host_buckets_correctly() -> None:
    urls = [
        "https://hota.com.au/whats-on/live",
        "https://hota.com.au/whats-on/live/event/123",
        "https://qso.com.au/events",
        "https://qso.com.au/events/2026/x",
    ]
    grouped = _group_urls_by_host(urls)
    assert sorted(grouped.keys()) == ["hota.com.au", "qso.com.au"]
    assert len(grouped["hota.com.au"]) == 2
    assert len(grouped["qso.com.au"]) == 2


def test_group_urls_by_host_handles_empty_input() -> None:
    assert _group_urls_by_host([]) == {}


def test_group_urls_by_host_skips_blank_entries() -> None:
    grouped = _group_urls_by_host(["", "https://example.com/a"])
    assert list(grouped.keys()) == ["example.com"]


# ── Builder: section presence ────────────────────────────────────────────────


def _sample_resources() -> list[Resource]:
    return [
        Resource(
            title="The Beths @ The Tivoli, Brisbane",
            url="https://ticketek.com.au/beths",
            date="2026-05-20",
            summary="Indie rock from Auckland.",
        ),
        Resource(
            title="QSO @ HOTA, Gold Coast",
            url="https://qso.com.au/events/2026/symphony-stars",
            date="2026-06-01",
        ),
    ]


def test_build_report_has_three_main_sections() -> None:
    md = build_run_report(
        queries=["q1", "q2"],
        crawled_urls=["https://example.com/a"],
        resources=_sample_resources(),
        now=_FIXED_NOW,
    )
    assert "## 1. Searches" in md
    assert "## 2. Search and crawl" in md
    assert "## 3. Normalize" in md


def test_build_report_lists_planner_queries() -> None:
    md = build_run_report(
        queries=["Brisbane jazz May 2026", "Gold Coast tickets"],
        crawled_urls=[],
        resources=[],
        now=_FIXED_NOW,
    )
    assert "- Brisbane jazz May 2026" in md
    assert "- Gold Coast tickets" in md


def test_build_report_groups_crawl_urls_by_host() -> None:
    md = build_run_report(
        queries=[],
        crawled_urls=[
            "https://hota.com.au/a",
            "https://hota.com.au/b",
            "https://qso.com.au/c",
        ],
        resources=[],
        now=_FIXED_NOW,
    )
    assert "### hota.com.au — 2 page(s)" in md
    assert "### qso.com.au — 1 page(s)" in md
    assert "https://hota.com.au/a" in md
    assert "https://qso.com.au/c" in md


def test_build_report_serialises_resources_with_source_url() -> None:
    md = build_run_report(
        queries=[],
        crawled_urls=[],
        resources=_sample_resources(),
        now=_FIXED_NOW,
    )
    assert "**Source URL:** https://ticketek.com.au/beths" in md
    assert "**Source URL:** https://qso.com.au/events/2026/symphony-stars" in md
    # The Pydantic model_dump should appear inside a fenced JSON block.
    assert "```json" in md
    assert '"title": "The Beths @ The Tivoli, Brisbane"' in md
    assert '"url": "https://ticketek.com.au/beths"' in md


def test_build_report_handles_empty_inputs_gracefully() -> None:
    md = build_run_report(queries=[], crawled_urls=[], resources=[], now=_FIXED_NOW)
    assert "_No queries produced" in md
    assert "_No URLs were crawled" in md
    assert "_No resources curated this run._" in md


def test_build_report_header_includes_human_timestamp() -> None:
    md = build_run_report(queries=[], crawled_urls=[], resources=[], now=_FIXED_NOW)
    assert md.startswith("# Run report — 2026-05-06 19:20:15")
    assert "AEST" in md.splitlines()[0]


# ── Writer: file I/O ─────────────────────────────────────────────────────────


def test_write_run_report_creates_file_with_expected_name(tmp_path: Path) -> None:
    written = write_run_report(
        tmp_path,
        queries=["q"],
        crawled_urls=["https://example.com/a"],
        resources=_sample_resources(),
        now=_FIXED_NOW,
    )
    assert written.exists()
    assert written.name == report_filename(_FIXED_NOW)
    assert written.parent == tmp_path
    body = written.read_text(encoding="utf-8")
    assert "## 1. Searches" in body
    assert "https://example.com/a" in body


def test_write_run_report_creates_missing_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "data"
    assert not nested.exists()
    written = write_run_report(
        nested, queries=[], crawled_urls=[], resources=[], now=_FIXED_NOW
    )
    assert written.parent == nested
    assert nested.is_dir()


def test_two_writes_with_distinct_seconds_produce_distinct_files(tmp_path: Path) -> None:
    """Per-run files should not stomp each other across normal-cadence runs."""
    later = _FIXED_NOW.replace(second=_FIXED_NOW.second + 1)
    p1 = write_run_report(tmp_path, queries=[], crawled_urls=[], resources=[], now=_FIXED_NOW)
    p2 = write_run_report(tmp_path, queries=[], crawled_urls=[], resources=[], now=later)
    assert p1 != p2
    assert p1.exists() and p2.exists()


# ── Spreadsheet-changes footer (Task 12 follow-up) ────────────────────────────


_SAMPLE_STATS = MergeStats(
    added=3,
    skipped=2,
    removed_past=4,
    removed_exclusion=2,
    removed_dedupe=1,
    total_after=27,
)


def test_report_omits_stats_section_when_no_stats_supplied() -> None:
    """Existing call sites without stats should still produce a 3-section report."""
    md = build_run_report(
        queries=["q"],
        crawled_urls=[],
        resources=[],
        now=_FIXED_NOW,
    )
    assert "## 4. Spreadsheet changes" not in md


def test_report_appends_stats_footer_when_supplied() -> None:
    md = build_run_report(
        queries=["q"],
        crawled_urls=[],
        resources=[],
        merge_stats=_SAMPLE_STATS,
        now=_FIXED_NOW,
    )
    assert "## 4. Spreadsheet changes" in md
    # Each metric label appears with its count.
    assert "| Added (new rows) | 3 |" in md
    assert "| Skipped as duplicate | 2 |" in md
    assert "| Past events pruned | 4 |" in md
    assert "| Removed by event exclusions | 2 |" in md
    assert "| Removed by LLM semantic dedupe | 1 |" in md
    assert "| Total rows after merge | 27 |" in md


def test_stats_section_is_the_last_thing_in_the_file() -> None:
    """User asked for the counts to *end* the report — make sure no other section follows."""
    md = build_run_report(
        queries=[],
        crawled_urls=[],
        resources=[],
        merge_stats=_SAMPLE_STATS,
        now=_FIXED_NOW,
    )
    last_heading = max(md.rfind(line) for line in md.splitlines() if line.startswith("## "))
    assert md[last_heading:].startswith("## 4. Spreadsheet changes")


def test_write_run_report_persists_stats_footer(tmp_path: Path) -> None:
    written = write_run_report(
        tmp_path,
        queries=[],
        crawled_urls=[],
        resources=[],
        merge_stats=_SAMPLE_STATS,
        now=_FIXED_NOW,
    )
    body = written.read_text(encoding="utf-8")
    assert "## 4. Spreadsheet changes" in body
    assert "| Total rows after merge | 27 |" in body
