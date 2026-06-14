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
# Default excerpt length overridden by ``config.CRAWL_MAX_TEXT_PER_PAGE``.


def _link_event_priority(url: str) -> int:
    """Rough score — event-detail URLs are enqueued sooner under crawl page caps."""
    low = url.lower()
    priority = 0
    if "/facebook.com/events/" in low or "facebook.com/events/" in low:
        priority += 12
    for frag in (
        "/e/",
        "/events/",
        "/event/",
        "/gig/",
        "/gigs/",
        "/shows/",
        "/show/",
        "eventbrite",
        "bandsintown",
        "songkick",
        "/tickets",
        "-tickets",
        "ticketmaster",
        "moshtix",
        "oztix",
    ):
        if frag in low:
            priority += 10
            break
    for frag in ("allevents.", "meetup.", "bandsintown", "whats-on", "whatson", "calendar", "concerts"):
        if frag in low:
            priority += 2
            break
    # Long query-string listing URLs slightly lower priority than short /e/... paths
    if low.count("?") > 1:
        priority -= 1
    return priority


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


# Mirrors the upgraded filter in ``enrich._is_candidate_image`` so the LLM
# sees the same poster set that Pass 1 will pick from later (Task 13).
_IMG_LIKELY_DECORATION = re.compile(
    r"(?:^|[/_\-])(?:"
    r"logo|favicon|sprite|icon|spinner|placeholder|tracking|pixel|"
    r"advert|banner|header-|footer-|sidebar|promo|social|share-|arrow|navbar"
    r")",
    re.IGNORECASE,
)
_IMG_LIKELY_AD = re.compile(
    r"(?:^|[/_\-])ads?(?:[/_\-.]|$)",
    re.IGNORECASE,
)


def _is_likely_event_image(src: str, width: str | int | None, height: str | int | None) -> bool:
    """Heuristic filter to drop obvious logos / tracking pixels (Task 12, refined Task 13).

    Keeps the inline image markers focused on photos that could realistically
    illustrate an event. The token list and matching logic mirrors
    ``agent.enrich._is_candidate_image`` so the LLM-facing markers and the
    deterministic per-event matcher reject the same junk.
    """
    if not src or src.startswith("data:"):
        return False
    if _IMG_LIKELY_DECORATION.search(src) or _IMG_LIKELY_AD.search(src):
        return False
    for dim in (width, height):
        try:
            if dim is not None and int(str(dim)) < 80:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _html_to_text(html: str, page_url: str) -> str:
    """Convert one fetched page to curator-ready text (Task 12 image markers).

    Images are not stripped any more: each ``<img>`` is replaced **in place**
    with a ``[IMG alt="…" src=…]`` marker so the curator LLM can see image
    URLs alongside their alt text and the surrounding event copy. Choosing
    a distinct thumbnail per event then becomes a normal LLM extraction
    task instead of relying on the page-level og:image.
    """
    soup = BeautifulSoup(html[:_MAX_HTML_BYTES], "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()

    for img in list(soup.find_all("img")):
        src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src") or "").strip()
        if not _is_likely_event_image(src, img.get("width"), img.get("height")):
            img.decompose()
            continue
        try:
            abs_src = urljoin(page_url, src)
        except ValueError:
            img.decompose()
            continue
        # Keep alt text short so a page with many images doesn't blow the prompt budget.
        alt = (img.get("alt") or "").strip().replace("\n", " ").replace('"', "'")
        if len(alt) > 140:
            alt = alt[:137] + "…"
        img.replace_with(f"[IMG alt=\"{alt}\" src={abs_src}]")

    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    body = "\n".join(lines)
    lim = max(4000, config.CRAWL_MAX_TEXT_PER_PAGE)
    header = f"### Fetched: {page_url}\n\n"
    return header + body[:lim]


def _extract_internal_links(page_url: str, html: str, host: str) -> list[str]:
    """Collect same-host links; prefer event-detail paths so BFS hits them first."""
    soup = BeautifulSoup(html[:_MAX_HTML_BYTES], "html.parser")
    found: list[str] = []
    seen: set[str] = set()
    _max_discover = 220
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
        found.append(clean)
        if len(found) >= _max_discover:
            break
    found.sort(key=lambda u: (-_link_event_priority(u), len(u)))
    return found[:96]


def _merge_crawl_seeds(
    ddg_blob: str,
    extra_seeds: list[str] | None,
) -> list[str]:
    """Combine guaranteed memory seeds with DuckDuckGo-derived seeds."""
    extra: list[str] = []
    seen_urls: set[str] = set()
    extra_hosts: set[str] = set()
    for raw in extra_seeds or []:
        u = _strip_url_trailing_junk((raw or "").strip())
        if not u.startswith("http"):
            continue
        key = u.lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)
        extra.append(u)
        try:
            extra_hosts.add(urlparse(u).netloc.lower())
        except ValueError:
            continue

    ddg_seeds = extract_seed_urls_from_ddg_blob(ddg_blob, config.MAX_CRAWL_SEEDS)
    seeds = list(extra)
    for u in ddg_seeds:
        try:
            host = urlparse(u).netloc.lower()
        except ValueError:
            continue
        if host in extra_hosts:
            continue
        if u.lower() in seen_urls:
            continue
        seen_urls.add(u.lower())
        seeds.append(u)
    return seeds


def deep_search_supplement(
    ddg_blob: str,
    extra_seeds: list[str] | None = None,
) -> tuple[str, list[str], str | None]:
    """Crawl seeds and return ``(curator_text, fetched_urls, crawl_note)``.

    The first element is the markdown-ish text appended for the curator LLM
    (empty string when nothing useful is harvested). The second element is the
    ordered list of URLs that were *successfully* fetched and turned into a
    text excerpt — used by the per-run report (Task 11) so we can see exactly
    which pages contributed to the curator input. The third element explains
    why no pages were crawled when ``fetched_urls`` is empty.
    """
    blob = ddg_blob or ""
    seeds = _merge_crawl_seeds(blob, extra_seeds)
    if not seeds:
        if not blob.strip():
            return (
                "",
                [],
                "Same-site crawl skipped — search step produced no text and no remembered URLs were available.",
            )
        logger.info("Same-site crawl: no seed URLs extracted from DuckDuckGo blob.")
        return (
            "",
            [],
            "Same-site crawl skipped — no http(s) links found in DuckDuckGo results to use as crawl seeds.",
        )

    max_total = max(1, config.MAX_CRAWL_PAGES_TOTAL)
    max_depth = max(0, config.MAX_CRAWL_DEPTH)
    per_seed = max(1, config.MAX_CRAWL_PAGES_PER_SEED)
    logger.info(
        "Same-site crawl starting: %d seed host(s); limits total_pages=%s depth=%s per_seed=%s delay_sec=%s",
        len(seeds),
        max_total,
        max_depth,
        per_seed,
        config.CRAWL_DELAY_SEC,
    )

    timeout = httpx.Timeout(18.0, connect=6.0)
    chunks: list[str] = []
    fetched_urls: list[str] = []
    pages_done = 0
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
                    fetched_urls.append(url)
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
        logger.info("Same-site crawl finished: zero HTML pages harvested (timeouts or skips).")
        note = (
            f"Same-site crawl ran on {len(seeds)} seed URL(s) but every fetch failed "
            "or returned non-HTML (timeouts, blocks, or empty pages)."
        )
        return "", fetched_urls, note
    block = (
        "## Same-site crawl (bounded)\n\n"
        "The following text was fetched by following links on the same host as a "
        "few promising search hits. Use it to find additional individual gigs.\n\n"
        + "\n\n---\n\n".join(chunks)
    )
    logger.info(
        "Same-site crawl finished: harvested %s page excerpts (~%s chars appended for curator).",
        len(chunks),
        f"{len(block):,}",
    )
    return block, fetched_urls, None
