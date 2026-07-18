"""robots.txt and sitemap.xml for the public site (SEO).

Served by the API (nginx proxies ``/robots.txt`` and ``/sitemap.xml`` here)
so URLs are generated from the live events window and the request's own
host — no hard-coded production domain anywhere in the repo, and the
sitemap stays current as tag/venue filter pages come and go.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable
from xml.sax.saxutils import escape

# Paths that always exist regardless of event data.
STATIC_PATHS = ["/", "/about"]


def slugify(value: str) -> str:
    """Mirror of the Angular ``slugify`` (web/src/app/list/event-filter-slug.ts).

    Both sides must agree so sitemap venue URLs match the SPA's routes.
    """
    s = (value or "").strip().lower()
    s = re.sub(r"[\u2018\u2019']", "", s)  # apostrophes vanish ("What's" -> "whats")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def build_robots_txt(base_url: str) -> str:
    """Allow everything public, keep crawlers out of admin, point at the sitemap."""
    return (
        "User-agent: *\n"
        "Disallow: /admin\n"
        "\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )


def sitemap_paths(events: Iterable[dict[str, Any]]) -> list[str]:
    """Static pages plus one URL per distinct tag and venue in the display window."""
    tags: set[str] = set()
    venues: set[str] = set()
    for ev in events:
        for tag in ev.get("tags") or []:
            label = str(tag).strip().lower()
            if label:
                tags.add(label)
        venue_slug = slugify(str(ev.get("venue") or ""))
        if venue_slug:
            venues.add(venue_slug)

    paths = list(STATIC_PATHS)
    paths.extend(f"/tags/{tag}" for tag in sorted(tags))
    paths.extend(f"/venues/{slug}" for slug in sorted(venues))
    return paths


def build_sitemap_xml(base_url: str, events: Iterable[dict[str, Any]]) -> str:
    """Render the sitemap; ``lastmod`` is today because listings change daily."""
    lastmod = date.today().isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in sitemap_paths(events):
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(base_url + path)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"
