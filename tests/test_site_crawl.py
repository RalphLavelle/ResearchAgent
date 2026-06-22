"""Tests for URL extraction and HTML→text conversion (no network)."""

from urllib.parse import urlparse

import pytest

from agent import site_crawl
from agent.site_crawl import (
    _extract_internal_links,
    _html_to_text,
    _is_unlikely_event_page,
    _link_event_priority,
    _merge_crawl_seeds,
    deep_search_supplement,
    extract_seed_urls_from_ddg_blob,
)


def test_pagination_links_rank_above_plain_pages() -> None:
    """Paged venue listings (Task 1) must be followed during the crawl."""
    plain = _link_event_priority("https://venue.example/about")
    page_query = _link_event_priority("https://venue.example/whats-on?page=2")
    page_path = _link_event_priority("https://venue.example/whats-on/page/3")
    assert page_query > plain
    assert page_path > plain


# ── Smarter page selection (Task 4) ───────────────────────────────────────────


def test_unlikely_event_pages_are_flagged() -> None:
    """Cart / checkout / account / legal / competition pages never list gigs."""
    for url in (
        "https://www.miamimarketta.com/cart",
        "https://www.miamimarketta.com/win",
        "https://venue.example/checkout",
        "https://venue.example/my-account",
        "https://venue.example/privacy-policy",
        "https://venue.example/login",
    ):
        assert _is_unlikely_event_page(url), url


def test_event_pages_are_not_flagged_as_unlikely() -> None:
    """Whole-segment matching keeps real event pages (and look-alikes) crawlable."""
    for url in (
        "https://www.miamimarketta.com/ticketed-events",
        "https://venue.example/whats-on",
        "https://venue.example/events/summer-gig",
        # Look-alikes that share a prefix with a skip word but are different segments.
        "https://venue.example/winery-sessions",
        "https://venue.example/search-live-music",
    ):
        assert not _is_unlikely_event_page(url), url


def test_event_pages_outrank_generic_content_pages() -> None:
    """The /ticketed-events page must score higher than /street-food-lineup etc."""
    event = _link_event_priority("https://www.miamimarketta.com/ticketed-events")
    food = _link_event_priority("https://www.miamimarketta.com/street-food-lineup")
    menu = _link_event_priority("https://venue.example/menu")
    assert event > food
    assert event > menu


def test_ticket_shop_pages_keep_positive_score() -> None:
    """A ticket page under /shop still wins despite the generic-page penalty."""
    assert _link_event_priority("https://venue.example/shop/event-tickets") > 0


def test_extract_internal_links_drops_non_event_pages() -> None:
    """Cart/win/checkout AND food-only pages are never enqueued; gigs survive."""
    html = """
    <html><body>
      <a href="/ticketed-events">Tickets</a>
      <a href="/cart">Cart</a>
      <a href="/win">Win tickets</a>
      <a href="/checkout">Checkout</a>
      <a href="/street-food-lineup">Street food</a>
      <a href="/the-beths">The Beths</a>
    </body></html>
    """
    links = _extract_internal_links(
        "https://www.miamimarketta.com/",
        html,
        host="www.miamimarketta.com",
    )
    assert "https://www.miamimarketta.com/cart" not in links
    assert "https://www.miamimarketta.com/win" not in links
    assert "https://www.miamimarketta.com/checkout" not in links
    # The Miami Marketta example from the task: the food page is dropped, the
    # ticketed-events page is kept and ranks first.
    assert "https://www.miamimarketta.com/street-food-lineup" not in links
    assert "https://www.miamimarketta.com/ticketed-events" in links
    assert links[0] == "https://www.miamimarketta.com/ticketed-events"
    # A neutral event-detail page (band name, no keyword) is still crawlable.
    assert "https://www.miamimarketta.com/the-beths" in links


def test_food_pages_are_flagged_unless_event_signal() -> None:
    """Food/dining pages are skipped — unless the URL also names an event."""
    assert _is_unlikely_event_page("https://www.miamimarketta.com/street-food-lineup")
    assert _is_unlikely_event_page("https://venue.example/menu")
    assert _is_unlikely_event_page("https://venue.example/food-and-drinks")
    # A page that pairs food with a gig signal stays crawlable.
    assert not _is_unlikely_event_page("https://venue.example/food-and-live-music")
    assert not _is_unlikely_event_page("https://venue.example/dinner-show-tickets")


def test_food_pages_rank_below_neutral_event_pages() -> None:
    """Even with skipping off, a food page sinks below a neutral band page."""
    food = _link_event_priority("https://www.miamimarketta.com/street-food-lineup")
    neutral = _link_event_priority("https://www.miamimarketta.com/the-beths")
    assert food < neutral


def test_merge_crawl_seeds_prepends_memory_url() -> None:
    blob = """
link: https://venue.example/events
link: https://other.example/page
"""
    seeds = _merge_crawl_seeds(
        blob,
        ["https://memory.example/whats-on"],
    )
    assert seeds[0] == "https://memory.example/whats-on"
    assert "https://venue.example/events" in seeds
    assert len(seeds) >= 2


def test_merge_crawl_seeds_allows_memory_without_ddg_blob() -> None:
    seeds = _merge_crawl_seeds("", ["https://memory.example/listings"])
    assert seeds == ["https://memory.example/listings"]


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


# ── Image-marker preservation (Task 12) ───────────────────────────────────────


def test_html_to_text_keeps_image_markers_inline() -> None:
    """The curator LLM needs to see <img> as `[IMG alt="..." src=...]`."""
    html = """
    <html><body>
      <h2>The Beths</h2>
      <img src="/p/the-beths.jpg" alt="The Beths album launch">
      <p>9 May at The Tivoli — tickets via Oztix.</p>
    </body></html>
    """
    text = _html_to_text(html, page_url="https://venue.example/whats-on")
    assert '[IMG alt="The Beths album launch" src=https://venue.example/p/the-beths.jpg]' in text
    assert "The Beths" in text
    assert "9 May at The Tivoli" in text


def test_html_to_text_drops_decoration_images() -> None:
    """Tiny logos / favicons must not pollute the candidate pool."""
    html = """
    <html><body>
      <img src="/img/logo.png" alt="Site logo">
      <img src="/icon-spinner.gif" alt="">
      <img src="/p/event.jpg" alt="Event poster">
    </body></html>
    """
    text = _html_to_text(html, page_url="https://venue.example/x")
    assert "logo.png" not in text
    assert "icon-spinner.gif" not in text
    assert "event.jpg" in text


def test_html_to_text_drops_embedded_decoration_keywords() -> None:
    """Mid-name logo/ad/banner tokens are also filtered (Task 13 follow-up).

    The LLM sees the same image set the deterministic Pass 1 will rank
    later, so the two filters must agree — otherwise the curator can
    confidently pick a logo for an event poster.
    """
    html = """
    <html><body>
      <img src="/uploads/toowong-logo-600px.jpg" alt="">
      <img src="/images/ad-set-1-laptop-tower-ad.png" alt="">
      <img src="/uploads/2026/05/header-strip.png" alt="">
      <img src="/uploads/share-twitter.png" alt="">
      <img src="/p/Eskimo-Joe.jpg" alt="">
    </body></html>
    """
    text = _html_to_text(html, page_url="https://venue.example/x")
    assert "toowong-logo-600px.jpg" not in text
    assert "laptop-tower-ad.png" not in text
    assert "header-strip.png" not in text
    assert "share-twitter.png" not in text
    assert "Eskimo-Joe.jpg" in text


def test_html_to_text_resolves_relative_image_urls() -> None:
    html = '<html><body><img src="poster.jpg" alt="A"></body></html>'
    text = _html_to_text(html, page_url="https://venue.example/whats-on/may")
    assert "src=https://venue.example/whats-on/poster.jpg" in text


def test_html_to_text_truncates_long_alt_text() -> None:
    long_alt = "x" * 500
    html = f'<html><body><img src="/p.jpg" alt="{long_alt}"></body></html>'
    text = _html_to_text(html, page_url="https://venue.example/x")
    # Alt should be truncated to ~140 chars before the closing quote.
    assert "…" in text
    assert "x" * 200 not in text


# ── Round-robin seed crawling (fair budget sharing) ───────────────────────────


def test_crawl_shares_budget_across_seeds_round_robin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every seed gets pages even when the first seed could fill the budget.

    Regression guard: previously the crawler drained one seed before moving to
    the next, so the first (venue) seeds consumed the whole page budget and the
    later (search-result) seeds were never crawled — which is why run reports
    kept showing the same handful of hosts every run.
    """

    def fake_fetch(_client: object, url: str) -> str:
        host = urlparse(url).netloc
        # Each page links to several same-host event pages so queues stay full.
        anchors = "".join(
            f'<a href="https://{host}/events/{i}">Gig {i}</a>' for i in range(5)
        )
        return f"<html><body><h1>{url}</h1>{anchors}</body></html>"

    monkeypatch.setattr(site_crawl, "_fetch_html_page", fake_fetch)
    monkeypatch.setattr("agent.config.CRAWL_DELAY_SEC", 0.0)
    monkeypatch.setattr("agent.config.MAX_CRAWL_PAGES_TOTAL", 4)
    monkeypatch.setattr("agent.config.MAX_CRAWL_PAGES_PER_SEED", 10)
    monkeypatch.setattr("agent.config.MAX_CRAWL_DEPTH", 2)

    _text, fetched, note = deep_search_supplement(
        "",
        extra_seeds=[
            "https://venue-a.example/whats-on",
            "https://venue-b.example/whats-on",
        ],
    )

    assert note is None
    assert len(fetched) == 4
    hosts = {urlparse(u).netloc for u in fetched}
    # Both seeds are represented — the budget was shared, not drained by seed A.
    assert hosts == {"venue-a.example", "venue-b.example"}
