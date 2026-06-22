"""Venue-first mining (Task 1).

When a known venue (from the ``venues`` collection) is recognised in the
DuckDuckGo results, this module finds the venue's own "What's On" page, stores
the link on the venue document, and returns those links as **priority crawl
seeds**. On later runs the stored link is reused directly, so big venues (e.g.
The Triffid) are mined exhaustively instead of being hit-and-miss.

The actual page fetching + pagination is handled by ``site_crawl`` — this
module only does the lightweight "find the What's On URL" discovery step.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from agent import config, venue_store
from agent.enrich import USER_AGENT

logger = logging.getLogger(__name__)

# One DDG result block from ``search_tools.run_searches`` output.
_RESULT_BLOCK = re.compile(
    r"title:\s*(?P<title>.*?)\n"
    r"snippet:\s*(?P<snippet>.*?)\n"
    r"link:\s*(?P<link>https?://\S+)",
    re.IGNORECASE | re.DOTALL,
)

# Hosts that are aggregators / socials — never treated as a venue's own site.
_AGGREGATOR_HOSTS = (
    "facebook.", "instagram.", "twitter.", "x.com", "youtube.", "youtu.be",
    "spotify.", "tiktok.", "eventbrite.", "ticketek.", "ticketmaster.",
    "moshtix.", "oztix.", "bandsintown.", "songkick.", "eventfinda.",
    "allevents.", "meetup.", "tripadvisor.", "wikipedia.", "google.",
    "bing.", "reddit.", "linktr.ee", "linktree.", "yelp.", "timeout.",
)

# Anchor text / href fragments that point at a venue's gig listing page.
_WHATS_ON_HINTS = (
    "whats-on", "whatson", "what-s-on", "/gigs", "/gig-guide", "gig-guide",
    "/events", "/event", "/shows", "/show", "/calendar", "/programme",
    "/program", "/lineup", "/line-up", "/tour", "/tickets", "/live-music",
    "upcoming",
)
_WHATS_ON_TEXT = (
    "what's on", "whats on", "gig guide", "gigs", "events", "shows",
    "calendar", "programme", "program", "line up", "lineup", "upcoming",
    "tickets", "live music",
)

_STRIP_TRAILING = ").,]}\"'>"


def _clean_url(url: str) -> str:
    return (url or "").strip().rstrip(_STRIP_TRAILING)


def _is_aggregator_host(host: str) -> bool:
    low = (host or "").lower()
    return any(frag in low for frag in _AGGREGATOR_HOSTS)


def parse_ddg_results(blob: str) -> list[dict[str, str]]:
    """Return ``[{title, snippet, link}]`` parsed from the search blob."""
    out: list[dict[str, str]] = []
    for m in _RESULT_BLOCK.finditer(blob or ""):
        out.append(
            {
                "title": m.group("title").strip(),
                "snippet": m.group("snippet").strip(),
                "link": _clean_url(m.group("link")),
            }
        )
    return out


def _root_url(link: str) -> str:
    """Scheme + host root for a result link (e.g. ``https://www.thetriffid.com.au``)."""
    p = urlparse(link)
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


def _whats_on_score(href: str, text: str) -> int:
    """Rank a candidate link; higher is a better "What's On" match.

    Returns 0 when neither the href nor the link text carries a listing hint,
    so unrelated links (``/about``, ``/contact``) are never chosen.
    """
    low_href = href.lower()
    low_text = (text or "").lower().strip()
    score = 0
    if any(frag in low_href for frag in _WHATS_ON_HINTS):
        score += 6
    for phrase in _WHATS_ON_TEXT:
        if phrase == low_text:
            score += 8
            break
        if phrase in low_text:
            score += 3
            break
    if score == 0:
        return 0
    # Prefer shorter, listing-style paths over deep single-event URLs.
    if low_href.count("/") <= 4:
        score += 1
    return score


def find_whats_on_link(root_url: str, html: str) -> str | None:
    """Find the best same-host "What's On" link on a venue homepage."""
    host = urlparse(root_url).netloc.lower()
    soup = BeautifulSoup(html[:600_000], "html.parser")
    best: tuple[int, str] | None = None
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        abs_u = urljoin(root_url, href).split("#", 1)[0]
        p = urlparse(abs_u)
        if p.scheme not in ("http", "https") or p.netloc.lower() != host:
            continue
        score = _whats_on_score(abs_u, a.get_text())
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, abs_u)
    return best[1] if best else None


def _events_link_is_fresh(doc: dict) -> bool:
    """True when a stored events_link was verified within the TTL window."""
    ttl = config.VENUE_EVENTS_LINK_TTL_DAYS
    if ttl <= 0:
        return True
    checked = str(doc.get("events_link_checked") or "").strip()
    if not checked:
        return False
    try:
        when = datetime.fromisoformat(checked)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when < timedelta(days=ttl)


def _discover_for_venue(client: httpx.Client, doc: dict, root_url: str) -> str | None:
    """Fetch a venue homepage and return its discovered What's On link."""
    try:
        resp = client.get(root_url)
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("Venue root fetch failed (%s): %s", root_url, exc)
        return None
    return find_whats_on_link(root_url, resp.text)


def gather_venue_seed_urls(db_name: str, ddg_blob: str) -> list[str]:
    """Return priority "What's On" seed URLs for the crawler.

    Two independent tiers, each with its own cap, so they never starve each
    other (the old code returned early once memory was full, which meant new
    venues were never discovered and the same few venues were mined forever):

    1. **Memory** — reuse up to ``MAX_VENUE_SEEDS`` venues that already have a
       fresh stored ``events_link``, picked **least-recently-mined first** so
       coverage rotates instead of repeating the same venues every run.
    2. **Discovery** — *always* try to find up to
       ``MAX_VENUE_DISCOVERIES_PER_RUN`` brand-new venues' What's On pages in
       this run's search results, so the pool of linked venues keeps growing.
    """
    if not config.VENUE_MINING_ENABLED:
        return []

    try:
        known = venue_store.list_venues(db_name)
    except Exception as exc:
        logger.warning("Venue mining skipped — could not load venues: %s", exc)
        return []
    if not known:
        return []

    seeds: list[str] = []
    seen: set[str] = set()
    resolved_ids: set[str] = set()
    seed_venue_ids: list[str] = []
    iso_now = datetime.now(timezone.utc).isoformat()

    def _finish() -> list[str]:
        """Record which venues were mined (for rotation) and return the seeds."""
        try:
            venue_store.mark_venues_mined(db_name, seed_venue_ids, iso_now)
        except Exception as exc:
            logger.warning("Could not record last_mined for venues: %s", exc)
        return seeds

    # 1) Memory: venues we already found a link for. Sort least-recently-mined
    #    first (missing last_mined = never mined = highest priority); a random
    #    shuffle first breaks ties so never-mined venues rotate from run one.
    fresh = [
        doc
        for doc in known
        if str(doc.get("events_link") or "").strip() and _events_link_is_fresh(doc)
    ]
    random.shuffle(fresh)
    fresh.sort(key=lambda d: str(d.get("last_mined") or ""))
    for doc in fresh[: max(0, config.MAX_VENUE_SEEDS)]:
        link = str(doc.get("events_link") or "").strip()
        key = link.lower()
        if key in seen:
            continue
        seen.add(key)
        seeds.append(link)
        resolved_ids.add(str(doc["_id"]))
        seed_venue_ids.append(str(doc["_id"]))

    # 2) Discovery: ALWAYS recognise NEW venues in this run's search results,
    #    even when the memory tier above is full, so the linked-venue pool grows.
    results = parse_ddg_results(ddg_blob)
    discovery_cap = max(0, config.MAX_VENUE_DISCOVERIES_PER_RUN)
    if not results or discovery_cap <= 0:
        return _finish()

    timeout = httpx.Timeout(15.0, connect=6.0)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"}
    attempts = 0
    discovered = 0
    max_attempts = max(1, discovery_cap * 3)

    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        for res in results:
            if discovered >= discovery_cap or attempts >= max_attempts:
                break
            host = urlparse(res["link"]).netloc.lower()
            if not host or _is_aggregator_host(host):
                continue
            match = None
            for doc in known:
                if str(doc["_id"]) in resolved_ids:
                    continue
                if venue_store.host_matches_venue(host, doc) or (
                    venue_store.text_mentions_venue(res["title"], doc)
                    and venue_store.host_matches_venue(host, doc)
                ):
                    match = doc
                    break
            if match is None:
                continue
            root = _root_url(res["link"])
            if not root:
                continue
            attempts += 1
            link = _discover_for_venue(client, match, root)
            resolved_ids.add(str(match["_id"]))
            if not link:
                continue
            try:
                venue_store.set_venue_web_fields(
                    db_name,
                    str(match["_id"]),
                    website=root,
                    events_link=link,
                    checked_iso=iso_now,
                )
            except Exception as exc:
                logger.warning("Could not save events_link for venue %s: %s", match.get("name"), exc)
            logger.info(
                "Venue mining: %s → What's On %s", match.get("name") or "?", link
            )
            if link.lower() not in seen:
                seen.add(link.lower())
                seeds.append(link)
                seed_venue_ids.append(str(match["_id"]))
                discovered += 1

    return _finish()
