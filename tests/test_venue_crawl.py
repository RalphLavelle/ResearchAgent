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


# ── Rotation: least-recently-mined venues first ───────────────────────────────


def _add_fresh_venue(db: str, name: str, link: str) -> str:
    """Create a venue with a freshly-verified events_link; return its id."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    doc = venue_store.create_venue(db, name)
    vid = str(doc["_id"])
    venue_store.set_venue_web_fields(
        db,
        vid,
        website=f"https://{name.lower()}.example",
        events_link=link,
        checked_iso=now,
    )
    return vid


def test_gather_seeds_rotates_least_recently_mined_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within the seed cap, never-mined venues come first, then oldest mined.

    This stops the agent re-crawling only the alphabetically-first venues every
    run; coverage rotates across all venues over successive runs.
    """
    db = "test-db"
    alpha = _add_fresh_venue(db, "Alpha", "https://alpha.example/whats-on")
    beta = _add_fresh_venue(db, "Beta", "https://beta.example/whats-on")
    gamma = _add_fresh_venue(db, "Gamma", "https://gamma.example/whats-on")

    # Alpha mined most recently, Beta a while ago, Gamma never mined.
    venue_store.mark_venues_mined(db, [alpha], "2026-06-20T00:00:00+00:00")
    venue_store.mark_venues_mined(db, [beta], "2026-06-01T00:00:00+00:00")

    monkeypatch.setattr("agent.config.MAX_VENUE_SEEDS", 2)
    seeds = venue_crawl.gather_venue_seed_urls(db, "")

    # Gamma (never mined) first, then Beta (older than Alpha); Alpha is dropped.
    assert seeds == [
        "https://gamma.example/whats-on",
        "https://beta.example/whats-on",
    ]


def test_gather_seeds_records_last_mined_on_chosen_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The venues actually mined this run get a fresh last_mined stamp."""
    db = "test-db"
    alpha = _add_fresh_venue(db, "Alpha", "https://alpha.example/whats-on")
    beta = _add_fresh_venue(db, "Beta", "https://beta.example/whats-on")
    venue_store.mark_venues_mined(db, [alpha], "2026-06-20T00:00:00+00:00")
    venue_store.mark_venues_mined(db, [beta], "2026-06-01T00:00:00+00:00")

    monkeypatch.setattr("agent.config.MAX_VENUE_SEEDS", 1)
    venue_crawl.gather_venue_seed_urls(db, "")

    # Only Beta (the oldest) was seeded, so only Beta's stamp moved forward.
    beta_doc = venue_store.get_venue(db, beta)
    alpha_doc = venue_store.get_venue(db, alpha)
    assert beta_doc is not None and alpha_doc is not None
    assert beta_doc.get("last_mined") != "2026-06-01T00:00:00+00:00"
    assert alpha_doc.get("last_mined") == "2026-06-20T00:00:00+00:00"


def test_discovery_runs_even_when_memory_seeds_are_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New venues must keep being discovered when the memory tier is full.

    Regression guard: the old code returned early once it had MAX_VENUE_SEEDS
    remembered venues, so it never discovered new ones — the linked-venue pool
    froze and the crawler hit the same handful of venues on every run.
    """
    db = "test-db"
    # Two remembered venues fill the memory tier (cap = 2)...
    _add_fresh_venue(db, "Alpha", "https://alpha.example/whats-on")
    _add_fresh_venue(db, "Beta", "https://beta.example/whats-on")
    # ...and a third venue with no link yet, present in this run's search blob.
    crowbar = venue_store.create_venue(db, "Crowbar")

    monkeypatch.setattr("agent.config.MAX_VENUE_SEEDS", 2)
    monkeypatch.setattr("agent.config.MAX_VENUE_DISCOVERIES_PER_RUN", 3)
    monkeypatch.setattr(
        venue_crawl,
        "_discover_for_venue",
        lambda _client, _doc, root: f"{root}/whats-on",
    )

    blob = """
title: Crowbar Brisbane
snippet: Live music venue
link: https://www.crowbar.com.au/
---
"""
    seeds = venue_crawl.gather_venue_seed_urls(db, blob)

    # Crowbar was discovered and persisted despite the memory tier being full.
    crowbar_doc = venue_store.get_venue(db, str(crowbar["_id"]))
    assert crowbar_doc is not None
    assert crowbar_doc.get("events_link") == "https://www.crowbar.com.au/whats-on"
    assert "https://www.crowbar.com.au/whats-on" in seeds
    # Memory tier (2) + the 1 discovery = 3 seeds in total.
    assert len(seeds) == 3
