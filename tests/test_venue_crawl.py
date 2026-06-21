"""Tests for venue-first mining (Task 1) — no network access."""

from __future__ import annotations

import pytest

from agent import venue_crawl, venue_store


def test_parse_ddg_results_extracts_blocks() -> None:
    blob = """
## Query: gigs
title: The Triffid - What's On
snippet: Upcoming shows in Newstead
link: https://www.thetriffid.com.au/
---
title: Random News
snippet: An article
link: https://news.example/story
---
"""
    results = venue_crawl.parse_ddg_results(blob)
    assert len(results) == 2
    assert results[0]["title"] == "The Triffid - What's On"
    assert results[0]["link"] == "https://www.thetriffid.com.au/"


def test_find_whats_on_link_picks_best_same_host_anchor() -> None:
    html = """
    <html><body>
      <a href="/about">About</a>
      <a href="/whats-on">What's On</a>
      <a href="https://external.example/x">External</a>
    </body></html>
    """
    link = venue_crawl.find_whats_on_link("https://www.thetriffid.com.au", html)
    assert link == "https://www.thetriffid.com.au/whats-on"


def test_find_whats_on_link_returns_none_when_absent() -> None:
    html = "<html><body><a href='/about'>About</a></body></html>"
    assert venue_crawl.find_whats_on_link("https://venue.example", html) is None


def test_gather_seeds_reuses_stored_events_link() -> None:
    """A venue with a fresh stored events_link is returned without any fetch."""
    db = "test-db"
    created = venue_store.create_venue(db, "The Triffid")
    vid = str(created["_id"])
    venue_store.set_venue_web_fields(
        db,
        vid,
        website="https://www.thetriffid.com.au",
        events_link="https://www.thetriffid.com.au/whats-on",
        checked_iso="2026-06-21T00:00:00+00:00",
    )

    # No DDG blob needed — the link comes from memory.
    seeds = venue_crawl.gather_venue_seed_urls(db, "")
    assert seeds == ["https://www.thetriffid.com.au/whats-on"]


def test_gather_seeds_discovers_and_persists_new_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = "test-db"
    created = venue_store.create_venue(db, "The Triffid")
    vid = str(created["_id"])

    # Avoid real HTTP: pretend the homepage yielded a What's On link.
    monkeypatch.setattr(
        venue_crawl,
        "_discover_for_venue",
        lambda _client, _doc, root: f"{root}/whats-on",
    )

    blob = """
title: The Triffid Brisbane
snippet: Live music venue
link: https://www.thetriffid.com.au/
---
title: Tickets on Eventbrite
snippet: buy now
link: https://www.eventbrite.com.au/e/the-triffid-12345
---
"""
    seeds = venue_crawl.gather_venue_seed_urls(db, blob)
    assert seeds == ["https://www.thetriffid.com.au/whats-on"]

    doc = venue_store.get_venue(db, vid)
    assert doc is not None
    assert doc["events_link"] == "https://www.thetriffid.com.au/whats-on"
    assert doc["website"] == "https://www.thetriffid.com.au"


def test_gather_seeds_ignores_aggregator_only_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = "test-db"
    venue_store.create_venue(db, "The Triffid")

    monkeypatch.setattr(
        venue_crawl,
        "_discover_for_venue",
        lambda _client, _doc, root: f"{root}/whats-on",
    )

    blob = """
title: The Triffid
snippet: event
link: https://www.facebook.com/events/12345
---
"""
    seeds = venue_crawl.gather_venue_seed_urls(db, blob)
    assert seeds == []


def test_gather_seeds_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.VENUE_MINING_ENABLED", False)
    db = "test-db"
    venue_store.create_venue(db, "The Triffid")
    assert venue_crawl.gather_venue_seed_urls(db, "anything") == []
