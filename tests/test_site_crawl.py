"""Tests for URL extraction and HTML→text conversion (no network)."""

from agent.site_crawl import (
    _html_to_text,
    _link_event_priority,
    _merge_crawl_seeds,
    extract_seed_urls_from_ddg_blob,
)


def test_pagination_links_rank_above_plain_pages() -> None:
    """Paged venue listings (Task 1) must be followed during the crawl."""
    plain = _link_event_priority("https://venue.example/about")
    page_query = _link_event_priority("https://venue.example/whats-on?page=2")
    page_path = _link_event_priority("https://venue.example/whats-on/page/3")
    assert page_query > plain
    assert page_path > plain


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
