"""Tests for the accumulating MongoDB event store (Tasks 8, 9, 13)."""

from datetime import date, timedelta
from pathlib import Path

import pytest

import agent.local_output as local_output
from agent.local_output import (
    _IDX_EVENT,
    _IDX_LOCATION,
    _IDX_POSTER,
    _IDX_SOURCES,
    _IDX_URL,
    _IDX_VENUE,
    _load_existing_rows,
    MergeStats,
    load_spreadsheet_resources,
    merge_and_write,
    write_output,
)
from agent.models import Resource


def _make_resource(title: str, url: str, days_ahead: int = 5) -> Resource:
    d = (date.today() + timedelta(days=days_ahead)).isoformat()
    return Resource(title=title, url=url, date=d)


def _rows() -> dict[str, list]:
    return _load_existing_rows(local_output.active_db_name())


def _urls_from_db() -> list[str]:
    return [str(row[_IDX_URL] or "") for row in _rows().values()]


def _sources_from_db() -> list[str]:
    return [str(row[_IDX_SOURCES] or "") for row in _rows().values()]


def _posters_from_db() -> list[str]:
    return [str(row[_IDX_POSTER] or "") for row in _rows().values()]


# ── Basic write / read ────────────────────────────────────────────────────────

def test_merge_creates_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r = _make_resource("Band A @ Venue X, Brisbane", "https://example.com/a")
    added, skipped, removed, _, _ = merge_and_write([r])
    assert added == 1
    assert skipped == 0
    assert removed == 0
    assert len(load_spreadsheet_resources()) == 1


def test_title_splits_into_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r = _make_resource("The Beths @ The Tivoli, Brisbane", "https://example.com/beths")
    merge_and_write([r])
    row = next(iter(_rows().values()))
    assert row[_IDX_EVENT] == "The Beths"
    assert row[_IDX_VENUE] == "The Tivoli"
    assert row[_IDX_LOCATION] == "Brisbane"


def test_new_url_added_on_second_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r1 = _make_resource("Band A", "https://example.com/a")
    r2 = _make_resource("Band B", "https://example.com/b")
    merge_and_write([r1])
    added, _, _, _, _ = merge_and_write([r2])
    assert added == 1
    urls = _urls_from_db()
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls


def test_past_events_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    past = Resource(title="Old Gig", url="https://example.com/old", date="2020-01-01")
    future = _make_resource("New Gig", "https://example.com/new")
    merge_and_write([past, future])
    _, _, removed, _, _ = merge_and_write([])
    assert removed == 1
    urls = _urls_from_db()
    assert "https://example.com/old" not in urls
    assert "https://example.com/new" in urls


def test_load_spreadsheet_resources_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r1 = _make_resource("The Beths @ The Tivoli, Brisbane", "https://example.com/beths")
    r2 = _make_resource("Open Mic Night @ Burleigh Bazaar, Gold Coast", "https://example.com/mic")
    merge_and_write([r1, r2])

    loaded = load_spreadsheet_resources()
    assert len(loaded) == 2
    urls = {r.url for r in loaded}
    assert "https://example.com/beths" in urls
    titles = {r.title for r in loaded}
    assert "The Beths @ The Tivoli, Brisbane" in titles


# ── Deduplication (Task 13) ───────────────────────────────────────────────────

def test_exact_url_duplicate_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r = _make_resource("Band A @ Venue X, Gold Coast", "https://example.com/a")
    merge_and_write([r])
    added, skipped, _, _, _ = merge_and_write([r])
    assert added == 0
    assert skipped == 1
    # No source should be added for same-domain same-URL
    sources = _sources_from_db()
    assert all(s == "" for s in sources)


def test_semantic_duplicate_adds_source_different_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same act+date from a different website → new row skipped, URL added to Sources."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    r1 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths", date=d)
    r2 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://oztix.com.au/beths", date=d)

    merge_and_write([r1])
    added, skipped, _, _, _ = merge_and_write([r2])

    assert added == 0
    assert skipped == 1
    urls = _urls_from_db()
    assert len([u for u in urls if u.startswith("http")]) == 1
    sources = _sources_from_db()
    assert any("oztix.com.au" in s for s in sources)


def test_semantic_duplicate_venue_variation_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same act+date with slightly different venue text → still treated as duplicate."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    r1 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths", date=d)
    # Venue text differs slightly ("Tivoli" vs "The Tivoli Theatre") — same gig.
    r2 = Resource(title="The Beths @ Tivoli Theatre, Brisbane", url="https://oztix.com.au/beths", date=d)

    merge_and_write([r1])
    added, skipped, _, _, _ = merge_and_write([r2])

    assert added == 0
    assert skipped == 1
    sources = _sources_from_db()
    assert any("oztix.com.au" in s for s in sources)


def test_semantic_duplicate_same_domain_no_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same act+date from the SAME domain → source not added (same domain rule)."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    r1 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths/1", date=d)
    r2 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths/2", date=d)

    merge_and_write([r1])
    merge_and_write([r2])

    sources = _sources_from_db()
    assert all("ticketek" not in s for s in sources)


def test_partial_act_name_same_venue_date_is_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Singer 1' and 'Singer 1, with Singer 2' at same venue+date → one row."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    r1 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths", date=d)
    r2 = Resource(title="The Beths, with Wax Chattels @ The Tivoli, Brisbane", url="https://oztix.com.au/beths", date=d)

    merge_and_write([r1])
    added, skipped, _, _, _ = merge_and_write([r2])

    assert added == 0
    assert skipped == 1
    urls = _urls_from_db()
    assert len([u for u in urls if u.startswith("http")]) == 1


def test_longer_act_name_becomes_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When partial match found, the longer act name replaces the shorter one."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    r1 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths", date=d)
    r2 = Resource(title="The Beths, with Wax Chattels @ The Tivoli, Brisbane", url="https://oztix.com.au/beths", date=d)

    merge_and_write([r1])
    merge_and_write([r2])

    row = next(iter(_rows().values()))
    assert row[_IDX_EVENT] == "The Beths, with Wax Chattels"


def test_shared_listing_url_two_distinct_gigs_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same portal URL allowed when act+date rows differ (Task 6 / aggregators)."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d1 = (date.today() + timedelta(days=5)).isoformat()
    d2 = (date.today() + timedelta(days=8)).isoformat()
    listing = "https://allevents.example/miami-au/concerts"
    r1 = Resource(title="Buzz Lovers @ Venue, Miami", url=listing, date=d1)
    r2 = Resource(title="Baggy Trousers @ Pub, Miami", url=listing, date=d2)

    merge_and_write([r1, r2])
    urls = _urls_from_db()
    assert urls.count(listing) == 2


def test_partial_act_name_different_venue_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial act-name match but different venue → two separate rows."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    r1 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths", date=d)
    r2 = Resource(title="The Beths, with Wax Chattels @ Fortitude Music Hall, Brisbane", url="https://oztix.com.au/beths", date=d)

    added1, _, _, _, _ = merge_and_write([r1])
    added2, _, _, _, _ = merge_and_write([r2])

    assert added1 == 1
    assert added2 == 1
    urls = _urls_from_db()
    assert len([u for u in urls if u.startswith("http")]) == 2


def test_different_date_not_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same act but different date → two separate rows."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d1 = (date.today() + timedelta(days=5)).isoformat()
    d2 = (date.today() + timedelta(days=12)).isoformat()
    r1 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths/1", date=d1)
    r2 = Resource(title="The Beths @ The Tivoli, Brisbane", url="https://ticketek.com.au/beths/2", date=d2)

    added1, _, _, _, _ = merge_and_write([r1])
    added2, _, _, _, _ = merge_and_write([r2])

    assert added1 == 1
    assert added2 == 1
    urls = _urls_from_db()
    assert len([u for u in urls if u.startswith("http")]) == 2


# ── write_output → MergeStats contract (Task 12 follow-up) ────────────────────

def test_write_output_returns_merge_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``write_output`` must hand back a populated ``MergeStats`` so the run report
    can show Section 4 counts."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)

    r1 = _make_resource("Band A @ Venue X, Brisbane", "https://example.com/a")
    r2 = _make_resource("Band B @ Venue Y, Brisbane", "https://example.com/b")

    stats = write_output([r1, r2])
    assert isinstance(stats, MergeStats)
    assert stats.added == 2
    assert stats.skipped == 0
    assert stats.removed_past == 0
    assert stats.removed_exclusion == 0
    assert stats.removed_dedupe == 0
    assert stats.total_after == 2


def test_merge_and_write_tracks_url_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-URL tallies distinguish new rows from duplicate skips."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r1 = _make_resource("Band A @ Venue X, Brisbane", "https://example.com/a")
    r2 = _make_resource("Band B @ Venue Y, Brisbane", "https://example.com/b")
    merge_and_write([r1])

    _, skipped, _, outcomes, distinct = merge_and_write([r1, r2])
    assert skipped == 1
    assert outcomes["https://example.com/a"] == (0, 1)
    assert outcomes["https://example.com/b"] == (1, 1)
    assert distinct["https://example.com/a"] == 1
    assert distinct["https://example.com/b"] == 1


def test_write_output_counts_duplicates_and_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)

    r = _make_resource("Band A @ Venue X, Brisbane", "https://example.com/a")
    write_output([r])  # seed
    stats = write_output([r])  # same resource again

    assert stats.added == 0
    assert stats.skipped == 1
    assert stats.total_after == 1


# ── Poster URL self-heal during dedupe (Task 13 follow-up) ────────────────────


def _make_resource_with_poster(
    title: str, url: str, poster: str | None, days_ahead: int = 5
) -> Resource:
    """Helper for self-heal tests where the thumbnail matters."""
    d = (date.today() + timedelta(days=days_ahead)).isoformat()
    return Resource(title=title, url=url, date=d, thumbnail_url=poster)


def test_exact_duplicate_upgrades_logo_poster_to_event_specific(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale 'logo' poster from an old run is replaced when the same gig
    arrives again with an event-specific filename (Task 13 follow-up)."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    bad = "https://toowongnews.com.au/uploads/toowong-logo-600px.jpg"
    good = "https://x/uploads/2026/05/Boy-Bear-with-The-Dreggs-May-8.jpg"

    seed = Resource(
        title="Boy & Bear @ Riverstage, Brisbane",
        url="https://ticketek.com.au/boy-bear",
        date=d,
        thumbnail_url=bad,
    )
    rerun = Resource(
        title="Boy & Bear @ Riverstage, Brisbane",
        url="https://oztix.com.au/boy-bear",
        date=d,
        thumbnail_url=good,
    )

    merge_and_write([seed])
    merge_and_write([rerun])

    posters = _posters_from_db()
    assert posters == [good]


def test_exact_duplicate_does_not_downgrade_good_poster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing event-specific poster must NOT be replaced by a logo on re-ingest."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    good = "https://x/uploads/2026/05/Boy-Bear-with-The-Dreggs.jpg"
    bad = "https://x/uploads/site-logo.png"

    seed = Resource(
        title="Boy & Bear @ Riverstage, Brisbane",
        url="https://ticketek.com.au/boy-bear",
        date=d,
        thumbnail_url=good,
    )
    rerun = Resource(
        title="Boy & Bear @ Riverstage, Brisbane",
        url="https://oztix.com.au/boy-bear",
        date=d,
        thumbnail_url=bad,
    )

    merge_and_write([seed])
    merge_and_write([rerun])

    posters = _posters_from_db()
    assert posters == [good]


def test_exact_duplicate_fills_empty_poster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty Poster URL is a free upgrade — anything beats nothing."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    good = "https://x/uploads/2026/05/Eskimo-Joe-Black-Fingernails.jpg"

    seed = Resource(
        title="Eskimo Joe @ The Triffid, Newstead",
        url="https://ticketek.com.au/eskimo-joe",
        date=d,
        thumbnail_url=None,
    )
    rerun = Resource(
        title="Eskimo Joe @ The Triffid, Newstead",
        url="https://oztix.com.au/eskimo-joe",
        date=d,
        thumbnail_url=good,
    )

    merge_and_write([seed])
    merge_and_write([rerun])

    posters = _posters_from_db()
    assert posters == [good]


def test_url_reingest_upgrades_stale_poster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same URL + same act+date re-ingested with a better poster heals the row.

    This is the (a) re-ingest branch — the row would otherwise be untouched,
    but its Poster URL now self-upgrades to a fresher event-specific image.
    """
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    url = "https://venue.example/whats-on"
    bad = "https://venue.example/uploads/header-banner.png"
    good = "https://venue.example/uploads/2026/05/Thundercat-May-8.jpg"

    seed = Resource(
        title="Thundercat @ Fortitude Music Hall, Brisbane",
        url=url, date=d, thumbnail_url=bad,
    )
    rerun = Resource(
        title="Thundercat @ Fortitude Music Hall, Brisbane",
        url=url, date=d, thumbnail_url=good,
    )

    merge_and_write([seed])
    merge_and_write([rerun])

    posters = _posters_from_db()
    assert posters == [good]


def test_partial_name_duplicate_upgrades_poster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The (c) partial-name dedupe path also upgrades posters by canonical act."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    d = (date.today() + timedelta(days=7)).isoformat()
    bad = "https://x/uploads/toowong-logo.png"
    good = "https://x/uploads/2026/05/The-Beths-Wax-Chattels.jpg"

    seed = Resource(
        title="The Beths @ The Tivoli, Brisbane",
        url="https://ticketek.com.au/beths",
        date=d,
        thumbnail_url=bad,
    )
    rerun = Resource(
        title="The Beths, with Wax Chattels @ The Tivoli, Brisbane",
        url="https://oztix.com.au/beths",
        date=d,
        thumbnail_url=good,
    )

    merge_and_write([seed])
    merge_and_write([rerun])

    posters = _posters_from_db()
    assert posters == [good]
