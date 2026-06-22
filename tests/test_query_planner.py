"""Tests for planner query diversity helpers."""

import random

from agent.prompt_guides import PromptGuides
from agent.query_planner import (
    build_planner_variation_block,
    build_targeted_venue_queries,
    load_targeted_venue_queries,
    merge_queries,
    pick_query_angles,
)
from agent.report_store import recent_search_queries, save_run_report
from agent.local_output import MergeStats
from agent.venue_store import create_venue, set_location


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


def _venue_guides() -> PromptGuides:
    return PromptGuides(
        venue_query_template="What's on in {venue} in {location}, Australia",
        venue_query_locations=["Brisbane", "Gold Coast"],
        venue_query_min=3,
        venue_query_max=6,
    )


def test_build_targeted_venue_queries_uses_venue_location() -> None:
    venues = [{"name": "The Triffid", "location": "Newstead, Brisbane"}]
    out = build_targeted_venue_queries(
        venues, _venue_guides(), rng=random.Random(1)
    )
    assert out == ["What's on in The Triffid in Newstead, Brisbane, Australia"]


def test_build_targeted_venue_queries_falls_back_to_region() -> None:
    venues = [{"name": "Mystery Bar", "location": ""}]
    out = build_targeted_venue_queries(
        venues, _venue_guides(), rng=random.Random(0)
    )
    assert len(out) == 1
    assert out[0].startswith("What's on in Mystery Bar in ")
    assert out[0].endswith(", Australia")
    assert ("Brisbane" in out[0]) or ("Gold Coast" in out[0])


def test_build_targeted_venue_queries_blank_location_no_dangling_in() -> None:
    guides = PromptGuides(
        venue_query_template="What's on in {venue} in {location}, Australia",
        venue_query_locations=[],
        venue_query_min=1,
        venue_query_max=1,
    )
    venues = [{"name": "Solo Venue", "location": ""}]
    out = build_targeted_venue_queries(venues, guides, rng=random.Random(2))
    assert out == ["What's on in Solo Venue, Australia"]


def test_build_targeted_venue_queries_count_in_range() -> None:
    venues = [{"name": f"Venue {i}", "location": "Brisbane"} for i in range(20)]
    out = build_targeted_venue_queries(
        venues, _venue_guides(), rng=random.Random(5)
    )
    assert 3 <= len(out) <= 6
    assert len(set(out)) == len(out)


def test_build_targeted_venue_queries_empty_without_venues_or_template() -> None:
    assert build_targeted_venue_queries([], _venue_guides()) == []
    guides = PromptGuides(venue_query_template="")
    assert build_targeted_venue_queries([{"name": "X"}], guides) == []


def test_merge_queries_prioritises_targeted_and_caps_limit() -> None:
    targeted = ["What's on in A", "What's on in B"]
    planned = ["planner one", "planner two", "planner three"]
    out = merge_queries(targeted, planned, limit=4)
    assert out[:2] == targeted
    assert len(out) == 4
    assert "planner three" not in out  # discarded by the cap


def test_merge_queries_drops_case_insensitive_duplicates() -> None:
    out = merge_queries(["What's on in A"], ["whats", "WHAT'S ON IN A"], limit=10)
    assert out == ["What's on in A", "whats"]


def test_load_targeted_venue_queries_reads_from_mongo() -> None:
    db = "test-db"
    venue = create_venue(db, "The Zoo")
    set_location(db, venue["_id"], "Fortitude Valley")
    out = load_targeted_venue_queries(db, _venue_guides(), rng=random.Random(3))
    assert out == ["What's on in The Zoo in Fortitude Valley, Australia"]
