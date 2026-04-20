"""Fetch Open Graph image URLs for resource pages."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

from agent.models import Resource

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; AIAgentResearch/0.1; +https://example.local) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_HTML_BYTES = 900_000

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)


def _absolute_url(base: str, maybe: str) -> str:
    return urljoin(base + "/", maybe)


def fetch_og_image(url: str) -> str | None:
    """Return best-effort og:image URL or None."""
    if not url or not urlparse(url).scheme.startswith("http"):
        return None
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            body = resp.content[:MAX_HTML_BYTES]
            text = body.decode("utf-8", errors="ignore")
    except Exception as exc:
        logger.debug("og:image fetch failed for %s: %s", url, exc)
        return None

    m = _OG_IMAGE_RE.search(text) or _OG_IMAGE_RE2.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw:
        return None
    return _absolute_url(url, raw)


def enrich_thumbnails(resources: list[Resource]) -> list[Resource]:
    """Set thumbnail_url from og:image where missing."""
    out: list[Resource] = []
    for r in resources:
        if r.thumbnail_url:
            out.append(r)
            continue
        thumb = fetch_og_image(r.url)
        out.append(r.model_copy(update={"thumbnail_url": thumb}))
    return out
