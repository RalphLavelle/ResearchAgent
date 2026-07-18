"""Tests for robots.txt / sitemap.xml generation (SEO)."""

from starlette.testclient import TestClient

from agent.api import create_app
from agent.seo import build_robots_txt, build_sitemap_xml, sitemap_paths, slugify


def test_slugify_mirrors_angular_helper() -> None:
    # Must match web/src/app/list/event-filter-slug.ts so sitemap venue
    # URLs resolve in the SPA.
    assert slugify("The Triffid") == "the-triffid"
    assert slugify("What's On Stage") == "whats-on-stage"
    assert slugify("  Miami Marketta!  ") == "miami-marketta"
    assert slugify("") == ""


def test_robots_txt_blocks_admin_and_links_sitemap() -> None:
    body = build_robots_txt("https://example.org")
    assert "Disallow: /admin" in body
    assert "Sitemap: https://example.org/sitemap.xml" in body


def test_sitemap_paths_include_static_tag_and_venue_pages() -> None:
    events = [
        {"venue": "The Triffid", "tags": ["rock", "Indie"]},
        {"venue": "The Triffid", "tags": ["rock"]},  # duplicates collapse
        {"venue": "Miami Marketta", "tags": []},
        {"venue": "", "tags": [""]},  # blanks are skipped
    ]
    paths = sitemap_paths(events)
    assert "/" in paths
    assert "/about" in paths
    assert "/tags/rock" in paths
    assert "/tags/indie" in paths
    assert "/venues/the-triffid" in paths
    assert "/venues/miami-marketta" in paths
    assert paths.count("/venues/the-triffid") == 1


def test_sitemap_xml_uses_base_url() -> None:
    xml = build_sitemap_xml("https://example.org", [{"venue": "The Zoo", "tags": ["jazz"]}])
    assert xml.startswith('<?xml version="1.0"')
    assert "<loc>https://example.org/</loc>" in xml
    assert "<loc>https://example.org/venues/the-zoo</loc>" in xml
    assert "<loc>https://example.org/tags/jazz</loc>" in xml


def test_get_robots_txt_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /admin" in response.text
    # Base URL comes from the request host — no hard-coded domain.
    assert "Sitemap: http://testserver/sitemap.xml" in response.text


def test_get_sitemap_xml_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<loc>http://testserver/</loc>" in response.text
    assert "<loc>http://testserver/about</loc>" in response.text


def test_get_sitemap_xml_respects_forwarded_proto() -> None:
    # Behind the DigitalOcean edge, TLS terminates upstream; the API must
    # honour X-Forwarded-Proto so sitemap URLs are https.
    client = TestClient(create_app())
    response = client.get("/sitemap.xml", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert "<loc>https://testserver/</loc>" in response.text
