"""Tests for the HTML template renderer."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from agent.html_output import HTML_FILENAME, TEMPLATE_PATH, render_html, write_html
from agent.models import Resource


def _make_resource(
    title: str,
    url: str,
    days_ahead: int = 5,
    thumbnail_url: str | None = None,
) -> Resource:
    d = (date.today() + timedelta(days=days_ahead)).isoformat()
    return Resource(title=title, url=url, date=d, thumbnail_url=thumbnail_url)


def test_template_file_exists() -> None:
    assert TEMPLATE_PATH.exists(), f"Template not found at {TEMPLATE_PATH}"


def test_render_contains_event_name() -> None:
    r = _make_resource("The Beths @ The Tivoli, Brisbane", "https://example.com/beths")
    html = render_html([r])
    assert "The Beths" in html


def test_render_linked_event_name() -> None:
    r = _make_resource("The Beths @ The Tivoli, Brisbane", "https://example.com/beths")
    html = render_html([r])
    assert 'href="https://example.com/beths"' in html
    assert ">The Beths<" in html


def test_render_venue_combined() -> None:
    r = _make_resource("The Beths @ The Tivoli, Brisbane", "https://example.com/beths")
    html = render_html([r])
    assert "The Tivoli, Brisbane" in html


def test_render_summary_shown() -> None:
    r = Resource(
        title="Band A @ Venue X, Gold Coast",
        url="https://example.com/a",
        date=(date.today() + timedelta(days=5)).isoformat(),
        summary="Indie rock with support from The Locals",
    )
    html = render_html([r])
    assert "Indie rock with support from The Locals" in html


def test_render_no_image_when_no_thumbnail() -> None:
    r = _make_resource("Band A", "https://example.com/a", thumbnail_url=None)
    output = render_html([r])
    assert '<img class="poster"' not in output


def test_render_image_when_thumbnail_present() -> None:
    r = _make_resource(
        "Band A",
        "https://example.com/a",
        thumbnail_url="https://example.com/poster.jpg",
    )
    html = render_html([r])
    assert '<img class="poster"' in html
    assert 'src="https://example.com/poster.jpg"' in html


def test_render_empty_resources() -> None:
    html = render_html([])
    assert "No gigs found" in html


def test_write_html_creates_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    r = _make_resource("Band A @ Venue X, Gold Coast", "https://example.com/a")
    path = write_html([r])
    assert path == tmp_path / HTML_FILENAME
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Band A" in content
