"""Tests for planner query diversity helpers."""

import random

from agent.prompt_guides import PromptGuides
from agent.query_planner import (
    build_planner_variation_block,
    pick_query_angles,
)
from agent.report_store import recent_search_queries, save_run_report
from agent.local_output import MergeStats


def test_pick_query_angles_samples_without_replacement() -> None:
    angles = [f"angle-{i}" for i in range(10)]
    picked = pick_query_angles(angles, count=5, rng=random.Random(7))
    assert len(picked) == 5
    assert len(set(picked)) == 5


def test_variation_block_lists_recent_queries_to_avoid() -> None:
    guides = PromptGuides(
        planner_query_angles=["jazz nights", "site:facebook.com events"],
        planner_angle_pick_count=2,
    )
    block = build_planner_variation_block(
        guides,
        recent_queries=["HOTA Gold Coast what's on June 2026 music"],
        rng=random.Random(1),
    )
    assert "Do not repeat recent searches" in block
    assert "HOTA Gold Coast" in block
    assert "Query format variety" in block
    assert "Prioritise these fresh angles" in block


def test_recent_search_queries_deduplicates_newest_first() -> None:
    db = "test-db"
    save_run_report(
        db,
        queries=["Alpha query", "Beta query"],
        crawled_urls=[],
        merge_stats=MergeStats(
            added=0,
            skipped=0,
            removed_past=0,
            removed_exclusion=0,
            removed_dedupe=0,
            removed_orphan_venues=0,
            total_after=0,
        ),
    )
    save_run_report(
        db,
        queries=["Beta query", "Gamma query"],
        crawled_urls=[],
        merge_stats=MergeStats(
            added=0,
            skipped=0,
            removed_past=0,
            removed_exclusion=0,
            removed_dedupe=0,
            removed_orphan_venues=0,
            total_after=0,
        ),
    )
    recent = recent_search_queries(db, limit=10)
    assert recent[0] == "Gamma query"
    assert recent[1] == "Beta query"
    assert recent[2] == "Alpha query"
