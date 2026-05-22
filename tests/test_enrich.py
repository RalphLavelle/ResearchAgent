"""Tests for thumbnail enrichment (Task 12).

These tests cover the pure helpers and the orchestration of
``enrich_thumbnails`` without hitting the network — HTTP calls are
monkeypatched to return fixed HTML strings.
"""

from __future__ import annotations

from typing import Iterable

import pytest

from agent import enrich
from agent.enrich import (
    _best_img_for_title,
    _extract_img_candidates,
    _filename_keywords,
    _is_candidate_image,
    _title_keywords,
    enrich_thumbnails,
    poster_quality_score,
)
from agent.models import Resource


# ── Pure helpers ──────────────────────────────────────────────────────────────


def test_is_candidate_image_drops_decoration_paths() -> None:
    assert _is_candidate_image("https://x/logo.png", {}) is False
    assert _is_candidate_image("https://x/favicon.ico", {}) is False
    assert _is_candidate_image("https://x/sprite-icons.svg", {}) is False
    assert _is_candidate_image("data:image/png;base64,abc", {}) is False
    assert _is_candidate_image("", {}) is False


def test_is_candidate_image_drops_embedded_decoration_keywords() -> None:
    """Real-world filenames embed logo/ad/banner mid-name (Task 13 follow-up).

    Previously these slipped through the filter and dominated per-event
    matching on toowongnews-style pages, pushing every actual event poster
    one slot down the assignment list.
    """
    assert _is_candidate_image("https://x/uploads/toowong-logo-600px.jpg", {}) is False
    assert _is_candidate_image("https://x/images/ad-set-1-laptop-tower-ad.png", {}) is False
    assert _is_candidate_image("https://x/uploads/header-strip.png", {}) is False
    assert _is_candidate_image("https://x/uploads/sidebar-promo.png", {}) is False
    assert _is_candidate_image("https://x/uploads/share-twitter.png", {}) is False
    # An ``ad`` segment anywhere in the path is enough to reject.
    assert _is_candidate_image("https://x/ads/banner.jpg", {}) is False


def test_is_candidate_image_accepts_real_photos() -> None:
    assert _is_candidate_image("https://x/event-poster.jpg", {}) is True
    assert _is_candidate_image("/img/the-beths-tour.png", {"width": "800"}) is True
    # Words that *contain* a decoration substring but are not bounded by /, _ or -
    # should still be accepted (e.g. "loading" contains "ad" only mid-word).
    assert _is_candidate_image("/img/loading-screen-art.jpg", {}) is True


def test_is_candidate_image_drops_tiny_images() -> None:
    assert _is_candidate_image("https://x/x.png", {"width": "20", "height": "20"}) is False
    assert _is_candidate_image("https://x/x.png", {"width": "200"}) is True


def test_title_keywords_focus_on_act_name_not_venue() -> None:
    """`split_title_parts` should give us just the act, then stopwords drop generics."""
    kw = _title_keywords("The Beths @ The Tivoli, Brisbane")
    # 'beths' should survive; 'brisbane' is a stopword; 'the' too short.
    assert "beths" in kw
    assert "brisbane" not in kw
    assert "tivoli" not in kw  # in the venue half, not the act half


def test_title_keywords_skip_short_words_and_punctuation() -> None:
    kw = _title_keywords("KWN, with Owusu")
    assert "kwn" in kw
    assert "owusu" in kw
    # Punctuation is stripped before splitting.
    for w in kw:
        assert "," not in w


# ── Image-candidate extraction ────────────────────────────────────────────────


def test_extract_img_candidates_returns_abs_src_and_alt() -> None:
    html = """
    <html><body>
      <img src="/p/the-beths.jpg" alt="The Beths poster" width="800">
      <img src="https://cdn.x/logo.png" alt="logo">
      <img src="kwn-tour.jpg" alt="KWN tour"/>
    </body></html>
    """
    cands = _extract_img_candidates(html, base_url="https://venue.com/whats-on")
    srcs = [s for s, _ in cands]
    alts = [a for _, a in cands]
    assert "https://venue.com/p/the-beths.jpg" in srcs
    assert "https://venue.com/whats-on/kwn-tour.jpg" in srcs
    # The logo entry should be filtered out by the decoration heuristic.
    assert all("logo" not in s for s in srcs)
    assert "The Beths poster" in alts


def test_extract_img_candidates_dedupes_by_src() -> None:
    html = """
    <html><body>
      <img src="poster.jpg" alt="A">
      <img src="poster.jpg" alt="A again">
    </body></html>
    """
    cands = _extract_img_candidates(html, base_url="https://venue.com/x")
    assert len(cands) == 1


# ── Best-match scoring ────────────────────────────────────────────────────────


def test_best_img_picks_overlapping_alt_text() -> None:
    candidates = [
        ("https://x/banner.jpg", "Generic events banner"),
        ("https://x/beths.jpg", "The Beths album launch"),
        ("https://x/kwn.jpg", "KWN live in Brisbane"),
    ]
    pick = _best_img_for_title(
        "The Beths @ The Tivoli, Brisbane",
        candidates,
        exclude=set(),
    )
    assert pick == "https://x/beths.jpg"


def test_best_img_returns_none_when_no_meaningful_overlap() -> None:
    """Empty alt **and** generic filenames → ``None`` (Task 13 follow-up).

    The previous behaviour was to return the first non-excluded candidate,
    which produced systematic off-by-N misassignments on pages whose DOM
    starts with a header logo before the actual event posters. Returning
    ``None`` instead lets the og:image fallback kick in, which is honest
    about the lack of signal.
    """
    candidates = [
        ("https://x/a.jpg", ""),
        ("https://x/b.jpg", ""),
    ]
    pick = _best_img_for_title("Random Act @ Venue", candidates, exclude=set())
    assert pick is None


def test_best_img_uses_filename_when_alt_is_empty() -> None:
    """Filename keywords matter when ``alt`` is missing (Task 13 follow-up).

    Many WordPress sites name uploaded posters after the act but leave the
    ``alt`` attribute blank. Mining the filename means we still pick the
    right poster instead of falling back to first-unused order.
    """
    candidates = [
        ("https://x/uploads/2026/05/banner-strip.png", ""),
        ("https://x/uploads/2026/05/Boy-Bear-with-The-Dreggs-May-8.jpg", ""),
        ("https://x/uploads/2026/05/Ned-Bennett-May-8.jpg", ""),
    ]
    pick = _best_img_for_title(
        "Boy & Bear With The Dreggs @ Riverstage, Brisbane",
        candidates,
        exclude=set(),
    )
    assert pick == "https://x/uploads/2026/05/Boy-Bear-with-The-Dreggs-May-8.jpg"


def test_filename_keywords_strips_extension_and_punctuation() -> None:
    kw = _filename_keywords("https://x/uploads/Boy-Bear-with-The-Dreggs-May-8-2026.jpg")
    assert "boy" in kw
    assert "bear" in kw
    assert "dreggs" in kw
    # Stopwords like 'with' and 'the' are excluded by the same stop list.
    assert "with" not in kw
    # Extension dropped.
    assert "jpg" not in kw


def test_best_img_returns_none_when_all_excluded() -> None:
    cands = [("https://x/a.jpg", "alt")]
    assert _best_img_for_title("Whatever", cands, exclude={"https://x/a.jpg"}) is None


# ── enrich_thumbnails orchestration ───────────────────────────────────────────


def _r(title: str, url: str, *, thumbnail: str | None = None) -> Resource:
    return Resource(title=title, url=url, thumbnail_url=thumbnail)


def _patch_network(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pages: dict[str, str | None],
    og_images: dict[str, str | None] | None = None,
) -> dict[str, int]:
    """Stub the two HTTP-touching functions and return a call-count dict."""
    counts: dict[str, int] = {"html": 0, "og": 0}

    def fake_html(url: str) -> str | None:
        counts["html"] += 1
        return pages.get(url)

    def fake_og(url: str) -> str | None:
        counts["og"] += 1
        return (og_images or {}).get(url)

    monkeypatch.setattr(enrich, "_fetch_html", fake_html)
    monkeypatch.setattr(enrich, "fetch_og_image", fake_og)
    return counts


def test_shared_url_group_gets_distinct_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Five events on one listing page → five different thumbnails (Task 12)."""
    page_html = """
    <html><body>
      <h2>The Beths</h2><img src="/img/beths.jpg" alt="The Beths Tivoli show">
      <h2>KWN</h2><img src="/img/kwn.jpg" alt="KWN headline">
      <h2>Genesis Owusu</h2><img src="/img/owusu.jpg" alt="Genesis Owusu live">
    </body></html>
    """
    listing = "https://venue.com/whats-on"
    _patch_network(monkeypatch, pages={listing: page_html})

    inputs = [
        _r("The Beths @ The Tivoli, Brisbane", listing),
        _r("KWN @ The Tivoli, Brisbane", listing),
        _r("Genesis Owusu @ The Tivoli, Brisbane", listing),
    ]
    out = enrich_thumbnails(inputs)

    thumbs = [r.thumbnail_url for r in out]
    assert all(thumbs), "every event should have a thumbnail"
    assert len(set(thumbs)) == 3, f"expected 3 distinct thumbnails, got {thumbs}"
    # Each event lands on the image whose alt text best matches its act name.
    by_title = {r.title: r.thumbnail_url for r in out}
    assert "beths" in (by_title["The Beths @ The Tivoli, Brisbane"] or "")
    assert "kwn" in (by_title["KWN @ The Tivoli, Brisbane"] or "")
    assert "owusu" in (by_title["Genesis Owusu @ The Tivoli, Brisbane"] or "")


def test_solo_resource_uses_og_image_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """One resource per URL → og:image path still works (back-compat)."""
    counts = _patch_network(
        monkeypatch,
        pages={},  # no per-event pass for solos
        og_images={"https://venue.com/event/1": "https://venue.com/p/1.jpg"},
    )
    out = enrich_thumbnails([_r("Solo Gig @ Venue", "https://venue.com/event/1")])
    assert out[0].thumbnail_url == "https://venue.com/p/1.jpg"
    assert counts["og"] == 1


def test_existing_distinct_thumbnails_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the LLM already gave distinct thumbnails to a shared-URL group, don't refetch."""
    counts = _patch_network(monkeypatch, pages={})
    listing = "https://venue.com/whats-on"
    inputs = [
        _r("Act A @ V", listing, thumbnail="https://x/a.jpg"),
        _r("Act B @ V", listing, thumbnail="https://x/b.jpg"),
    ]
    out = enrich_thumbnails(inputs)
    assert [r.thumbnail_url for r in out] == [
        "https://x/a.jpg",
        "https://x/b.jpg",
    ]
    assert counts["html"] == 0
    assert counts["og"] == 0


def test_og_cache_avoids_duplicate_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same URL shared by N solo-thumbnail-less resources → og:image fetched once."""
    counts = _patch_network(
        monkeypatch,
        pages={"https://venue.com/listing": ""},  # empty page → no candidates
        og_images={"https://venue.com/listing": "https://venue.com/banner.jpg"},
    )
    listing = "https://venue.com/listing"
    inputs = [_r("A @ V", listing), _r("B @ V", listing), _r("C @ V", listing)]
    out = enrich_thumbnails(inputs)
    # Per-event matching couldn't help (no candidates), so all three fall back
    # to og:image — but only one HTTP call should have happened in Pass 2.
    assert counts["og"] == 1
    assert all(r.thumbnail_url == "https://venue.com/banner.jpg" for r in out)


def test_partial_match_remainder_falls_through_to_og(monkeypatch: pytest.MonkeyPatch) -> None:
    """When candidates < group size, surplus resources get og:image (not blank)."""
    page_html = (
        '<html><body><img src="/img/beths.jpg" alt="The Beths"></body></html>'
    )
    listing = "https://venue.com/x"
    _patch_network(
        monkeypatch,
        pages={listing: page_html},
        og_images={listing: "https://venue.com/banner.jpg"},
    )
    out = enrich_thumbnails(
        [
            _r("The Beths @ Venue", listing),
            _r("KWN @ Venue", listing),
        ]
    )
    thumbs = [r.thumbnail_url for r in out]
    assert "https://venue.com/img/beths.jpg" in thumbs
    assert "https://venue.com/banner.jpg" in thumbs


def test_existing_unique_thumbnails_preserved_when_one_event_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM picks survive when only one sibling lacks a thumbnail (Task 13).

    Previously, if N-1 of N events sharing a URL already had distinct
    thumbnails set by the curator and one was missing, Pass 1 fired and
    overwrote *all* of them with potentially worse choices. The trusted
    LLM picks must now be reserved instead.
    """
    page_html = """
    <html><body>
      <img src="/uploads/header-strip.png" alt="">
      <img src="/uploads/Boy-Bear-with-The-Dreggs-May-8.jpg" alt="">
      <img src="/uploads/Ned-Bennett-May-8.jpg" alt="">
      <img src="/uploads/Thundercat-May-8.jpg" alt="">
    </body></html>
    """
    listing = "https://venue.com/whats-on"
    _patch_network(
        monkeypatch,
        pages={listing: page_html},
        og_images={listing: "https://venue.com/og.jpg"},
    )

    inputs = [
        _r(
            "Boy & Bear @ Riverstage, Brisbane",
            listing,
            thumbnail="https://cdn.x/Boy-Bear-llm-pick.jpg",
        ),
        _r(
            "Ned Bennett @ The Princess Theatre",
            listing,
            thumbnail="https://cdn.x/Ned-Bennett-llm-pick.jpg",
        ),
        _r("Thundercat @ The Fortitude Music Hall", listing),  # missing
    ]
    out = enrich_thumbnails(inputs)

    by_title = {r.title: r.thumbnail_url for r in out}
    assert by_title["Boy & Bear @ Riverstage, Brisbane"] == "https://cdn.x/Boy-Bear-llm-pick.jpg"
    assert by_title["Ned Bennett @ The Princess Theatre"] == "https://cdn.x/Ned-Bennett-llm-pick.jpg"
    # Thundercat had no thumbnail; Pass 1 should match by filename keyword.
    assert by_title["Thundercat @ The Fortitude Music Hall"] == (
        "https://venue.com/uploads/Thundercat-May-8.jpg"
    )


# ── poster_quality_score (Task 13 follow-up — spreadsheet self-heal) ─────────


def test_poster_quality_score_empty_is_lowest() -> None:
    """Missing posters score below everything else so any URL upgrades them."""
    assert poster_quality_score(None, "The Beths") == -1
    assert poster_quality_score("", "The Beths") == -1
    assert poster_quality_score("   ", "The Beths") == -1


def test_poster_quality_score_decoration_is_zero() -> None:
    """Logos / ads / banners score 0 — only an empty cell is worse."""
    assert poster_quality_score(
        "https://x/uploads/toowong-logo-600px.jpg", "Boy & Bear"
    ) == 0
    assert poster_quality_score(
        "https://x/images/ad-set-1-laptop-tower-ad.png", "Boy & Bear"
    ) == 0
    assert poster_quality_score(
        "https://x/uploads/2026/03/Whats-on-Brisbane-Gigs-banner.webp", "Boy & Bear"
    ) == 0


def test_poster_quality_score_generic_image_is_one() -> None:
    """Any non-decoration URL with no act overlap gets the generic tier."""
    assert poster_quality_score("https://venue.com/og-image.jpg", "Boy & Bear") == 1


def test_poster_quality_score_filename_match_outranks_generic() -> None:
    """A poster whose filename mentions the act beats both decoration and generic."""
    score = poster_quality_score(
        "https://x/uploads/2026/05/Boy-Bear-with-The-Dreggs-Bears-Den.jpg",
        "Boy & Bear",
    )
    # Two overlapping keywords ("boy", "bear") → 2 + 2 = 4.
    assert score >= 3
    assert score > poster_quality_score("https://x/og.jpg", "Boy & Bear")
    assert score > poster_quality_score("https://x/ad-banner.jpg", "Boy & Bear")


def test_poster_quality_score_unknown_act_falls_back_to_decoration_tier() -> None:
    """When the act yields no keywords, we still rank empty < decoration < other."""
    assert (
        poster_quality_score(None, "")
        < poster_quality_score("https://x/logo.png", "")
        < poster_quality_score("https://x/poster.jpg", "")
    )


def test_decorative_first_images_do_not_steal_event_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header logos and ads must not consume the first per-event slots (Task 13).

    Real toowongnews-style listing pages start with logos, ads and banners
    before the actual event posters. With the strengthened decoration
    filter, those decorations are excluded entirely — events match against
    only the real posters by filename and alt text.
    """
    page_html = """
    <html><body>
      <img src="/uploads/toowong-logo-600px.jpg" alt="">
      <img src="/images/ad-set-1-laptop-tower-ad.png" alt="">
      <img src="/uploads/2026/03/Whats-on-Brisbane-Gigs-FI.webp" alt="">
      <img src="/uploads/2026/05/Eskimo-Joe-Black-Fingernails.jpg" alt="">
      <img src="/uploads/2026/05/Tommy-Little-Namaste.jpg" alt="">
    </body></html>
    """
    listing = "https://toowongnews.com.au/whats-on/"
    _patch_network(monkeypatch, pages={listing: page_html})

    inputs = [
        _r("Eskimo Joe @ The Triffid, Newstead", listing),
        _r("Tommy Little @ The Tivoli, Fortitude Valley", listing),
    ]
    out = enrich_thumbnails(inputs)
    by_title = {r.title: r.thumbnail_url for r in out}
    assert "Eskimo-Joe" in (by_title["Eskimo Joe @ The Triffid, Newstead"] or "")
    assert "Tommy-Little" in (by_title["Tommy Little @ The Tivoli, Fortitude Valley"] or "")
