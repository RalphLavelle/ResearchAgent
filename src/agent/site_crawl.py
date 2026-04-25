"""Bounded same-origin crawl to deepen research after DuckDuckGo (Task 6).

Picks a few seed URLs from the search blob, fetches HTML with httpx, strips
boilerplate with BeautifulSoup, and follows same-host links up to strict depth
and page limits. Output is plain text appended for the curator LLM — not a
general-purpose indexer (no sitemap, no robots.txt parsing yet).
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from agent import config
from agent.enrich import USER_AGENT

logger = logging.getLogger(__name__)

_LINK_LINE = re.compile(r"^link:\s*(https?://\S+)", re.I | re.MULTILINE)
_BINARYISH = re.compile(
    r"\.(pdf|zip|jpg|jpeg|png|gif|webp|svg|mp4|mp3|wav|m4a|doc|docx)(\?|$)",
    re.I,
)
_MAX_HTML_BYTES = 600_000
_MAX_TEXT_PER_PAGE = 7000


def _strip_url_trailing_junk(url: str) -> str:
    return url.rstrip(").,]}\"'").rstrip(">")


def extract_seed_urls_from_ddg_blob(text: str, max_seeds: int) -> list[str]:
    """Pull candidate http(s) URLs from ``run_searches`` output (``link:`` lines)."""
    found: list[str] = []
    seen: set[str] = set()
    for m in _LINK_LINE.finditer(text or ""):
        u = _strip_url_trailing_junk(m.group(1))
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        found.append(u)

    def score(u: str) -> int:
        low = u.lower()
        s = 0
        for kw in (
            "event",
            "ticket",
            "whats-on",
            "whatson",
            "gig",
            "show",
            "calendar",
            "concert",
            "live-music",
            "bookings",
        ):
            if kw in low:
                s += 2
        return s

    ranked = sorted(found, key=lambda u: (-score(u), len(u)))
    seeds: list[str] = []
    hosts: set[str] = set()
    for u in ranked:
        try:
            host = urlparse(u).netloc.lower()
        except ValueError:
            continue
        if not host or host in hosts:
            continue
        hosts.add(host)
        seeds.append(u)
        if len(seeds) >= max_seeds:
            break

    if not seeds:
        seeds = found[:max_seeds]
    return seeds[:max_seeds]


def _same_host(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
    except ValueError:
        return False


def _html_to_text(html: str, page_url: str) -> str:
    soup = BeautifulSoup(html[:_MAX_HTML_BYTES], "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    body = "\n".join(lines)
    header = f"### Fetched: {page_url}\n\n"
    return header + body[:_MAX_TEXT_PER_PAGE]


def _extract_internal_links(page_url: str, html: str, host: str) -> list[str]:
    soup = BeautifulSoup(html[:_MAX_HTML_BYTES], "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        abs_u = urljoin(page_url, href)
        p = urlparse(abs_u)
        if p.scheme not in ("http", "https") or p.netloc.lower() != host:
            continue
        clean = abs_u.split("#", 1)[0]
        if _BINARYISH.search(clean) or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= 40:
            break
    return out


def deep_search_supplement(ddg_blob: str) -> str:
    """Return extra markdown-style text for the curator, or empty string."""
    seeds = extract_seed_urls_from_ddg_blob(ddg_blob, config.MAX_CRAWL_SEEDS)
    if not seeds:
        return ""

    timeout = httpx.Timeout(18.0, connect=6.0)
    chunks: list[str] = []
    pages_done = 0
    max_total = max(1, config.MAX_CRAWL_PAGES_TOTAL)
    max_depth = max(0, config.MAX_CRAWL_DEPTH)
    per_seed = max(1, config.MAX_CRAWL_PAGES_PER_SEED)

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}

    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        for seed in seeds:
            if pages_done >= max_total:
                break
            try:
                host = urlparse(seed).netloc.lower()
            except ValueError:
                continue
            q: deque[tuple[str, int]] = deque([(seed, 0)])
            visited: set[str] = set()
            seed_pages = 0
            while q and pages_done < max_total and seed_pages < per_seed:
                url, depth = q.popleft()
                if url in visited:
                    continue
                visited.add(url)
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    blob = resp.text
                    ctype = (resp.headers.get("content-type") or "").lower()
                    head = blob.lower()[:8000]
                    if (
                        "html" not in ctype
                        and "<html" not in head
                        and "<!doctype html" not in head
                    ):
                        continue
                    text_body = _html_to_text(blob, url)
                    chunks.append(text_body)
                    pages_done += 1
                    seed_pages += 1
                except Exception as exc:
                    logger.debug("crawl skip %s: %s", url, exc)
                    continue
                time.sleep(max(0.0, config.CRAWL_DELAY_SEC))

                if depth >= max_depth:
                    continue
                try:
                    for link in _extract_internal_links(url, blob, host):
                        if link not in visited:
                            q.append((link, depth + 1))
                except Exception:
                    continue

    if not chunks:
        return ""
    return (
        "## Same-site crawl (bounded)\n\n"
        "The following text was fetched by following links on the same host as a "
        "few promising search hits. Use it to find additional individual gigs.\n\n"
        + "\n\n---\n\n".join(chunks)
    )
