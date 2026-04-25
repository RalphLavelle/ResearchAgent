"""Tests for URL extraction from DuckDuckGo blob (no network)."""

from agent.site_crawl import extract_seed_urls_from_ddg_blob


def test_extract_seeds_from_link_lines() -> None:
    blob = """
## Query: test

title: A
snippet: s
link: https://venue.example/events/summer-gig
---
link: https://other.example/page
link: https://venue.example/whats-on
---
"""
    seeds = extract_seed_urls_from_ddg_blob(blob, max_seeds=5)
    assert "https://venue.example/events/summer-gig" in seeds
    assert len(seeds) >= 1
