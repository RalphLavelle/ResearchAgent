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
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from agent import config
from agent.enrich import USER_AGENT

logger = logging.getLogger(__name__)


@dataclass
class _SeedCrawl:
    """Per-seed BFS state so several seeds can be crawled round-robin.

    Each seed keeps its own queue, visited set, and fetched-page count. The
    crawler gives every seed one page per round, sharing the global page budget
    fairly instead of letting the first seed(s) drain it.
    """

    host: str
    queue: deque[tuple[str, int]] = field(default_factory=deque)
    visited: set[str] = field(default_factory=set)
    pages: int = 0

    def next_unvisited(self) -> tuple[str, int] | None:
        """Pop and return this seed's next ``(url, depth)`` not yet fetched."""
        while self.queue:
            url, depth = self.queue.popleft()
            if url in self.visited:
                continue
            return url, depth
        return None

_LINK_LINE = re.compile(r"^link:\s*(https?://\S+)", re.I | re.MULTILINE)
_BINARYISH = re.compile(
    r"\.(pdf|zip|jpg|jpeg|png|gif|webp|svg|mp4|mp3|wav|m4a|doc|docx)(\?|$)",
    re.I,
)
_MAX_HTML_BYTES = 600_000
# Default excerpt length overridden by ``config.CRAWL_MAX_TEXT_PER_PAGE``.

# Task 4 — whole path segments that are essentially never music/event listings.
# These are transactional, account, legal, or utility pages found on venue and
# ticketing sites (e.g. /cart, /checkout, /my-account, /privacy, the "/win" of a
# competition). Crawling them wastes the bounded page budget that should go to
# gig/event/whats-on pages, so they are dropped before they are ever enqueued.
_NON_EVENT_SEGMENTS = frozenset(
    {
        # Cart / checkout / commerce flow
        "cart", "carts", "basket", "baskets", "checkout", "checkouts",
        "order", "orders", "wishlist", "wishlists", "gift-card", "gift-cards",
        "giftcard", "giftcards", "voucher", "vouchers",
        # Account / auth
        "account", "accounts", "my-account", "myaccount", "login", "log-in",
        "signin", "sign-in", "signup", "sign-up", "register", "registration",
        "logout", "log-out", "password", "reset-password", "profile",
        # Legal / policy / utility
        "terms", "terms-and-conditions", "terms-of-service", "tos", "legal",
        "privacy", "privacy-policy", "cookie", "cookies", "disclaimer",
        "refund", "refunds", "returns", "shipping", "delivery", "postage",
        "faq", "faqs", "help", "support", "sitemap", "search", "404",
        # Recruitment / press / fundraising
        "careers", "career", "jobs", "vacancies", "press", "media-kit",
        "donate", "donation", "donations",
        # Promotions that aren't gigs
        "win", "competition", "competitions", "giveaway", "giveaways",
        "sweepstake", "sweepstakes",
    }
)

# Generic content pages that occasionally exist but rarely list gigs; matched as
# path fragments and softly de-prioritised (not hard-skipped) so a ticket page
# living under one of them — e.g. /shop/tickets — keeps its positive event score.
# These pages CAN occasionally mention a gig (a /news post, an /about page), so
# they are kept and crawled last rather than dropped outright.
_LOW_VALUE_FRAGMENTS = (
    "/about", "/contact", "/gallery", "/photos", "/blog", "/news", "/team",
    "/staff", "/history", "/our-story", "/location", "/find-us", "/directions",
    "/parking", "/hire", "/venue-hire", "/function", "/functions", "/wedding",
    "/weddings", "/merch", "/shop", "/store", "/product", "/products",
    "/membership", "/subscribe", "/newsletter", "/accommodation", "/rooms",
    "/stay",
)

# Food / dining "words" (path segments split on - and _). A page that is purely
# about food or drink — e.g. Miami Marketta's ``/street-food-lineup`` or a plain
# ``/menu`` — is about the kitchen, not the stage, so it is treated like the
# transactional pages above (skipped) UNLESS the same URL also carries an event
# signal (so ``/food-and-live-music`` or ``/dinner-show-tickets`` is still kept).
_FOOD_DINING_WORDS = frozenset({
    "food", "foods", "menu", "menus", "dining", "drinks", "drink", "catering",
    "brunch", "degustation", "buffet", "eats",
})

# Substrings that signal a page is about gigs/tickets/shows — used both to score
# links and to protect food/content pages that genuinely host events from being
# skipped. (``lineup`` is deliberately excluded: it appears in food line-ups too,
# and a real music line-up page carries no food word so it is never skipped.)
_EVENT_SIGNAL_SUBSTRINGS = (
    "/e/", "event", "ticket", "gig", "/show", "shows", "concert", "live-music",
    "livemusic", "whats-on", "whatson", "what-s-on", "gig-guide", "gigguide",
    "calendar", "eventbrite", "bandsintown", "songkick", "ticketmaster",
    "moshtix", "oztix", "allevents.", "meetup.",
)


def _has_event_signal(url: str) -> bool:
    """True when the URL looks like a gig / ticket / show / what's-on page."""
    return any(s in url.lower() for s in _EVENT_SIGNAL_SUBSTRINGS)


def _segment_words(path: str) -> set[str]:
    """Path segments plus their hyphen/underscore-split words, lowercased."""
    words: set[str] = set()
    for seg in path.split("/"):
        seg = seg.strip()
        if not seg:
            continue
        words.add(seg)
        for word in re.split(r"[-_]", seg):
            if word:
                words.add(word)
    return words


def _is_unlikely_event_page(url: str) -> bool:
    """True for pages that almost never list gigs, so the crawl can skip them.

    Two groups are dropped:

    1. Transactional / account / legal / utility pages (``/cart``, ``/login``,
       ``/privacy``, the ``/win`` of a competition) — never events.
    2. Food / dining pages (``/street-food-lineup``, ``/menu``) — about the
       kitchen, not the stage — *unless* the URL also carries an event signal.

    Matching is on whole path *segments* (and their hyphen-split words), not raw
    substrings, so look-alikes like ``/winery-sessions`` or ``/search-live-music``
    are not mistaken for the bare ``/win`` or ``/search`` utility pages.
    """
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        return False
    segments = [seg for seg in path.split("/") if seg]
    if any(seg in _NON_EVENT_SEGMENTS for seg in segments):
        return True
    if not _has_event_signal(url):
        if _segment_words(path) & _FOOD_DINING_WORDS:
            return True
    return False


def _link_event_priority(url: str) -> int:
    """Rough score — event-detail URLs are enqueued sooner under crawl page caps."""
    low = url.lower()
    priority = 0
    if "/facebook.com/events/" in low or "facebook.com/events/" in low:
        priority += 12
    for frag in (
        "/e/",
        "event",
        "ticket",
        "/gig",
        "/show",
        "concert",
        "live-music",
        "livemusic",
        "eventbrite",
        "bandsintown",
        "songkick",
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
    # Pagination links keep the crawler moving through long venue listings so a
    # venue's full gig list (which is often paged) gets mined (Task 1).
    if re.search(r"(?:[?&](?:page|paged|pg|p)=\d+|/page/\d+|/p/\d+)", low):
        priority += 8
    # Generic content pages (about, gallery, shop, …) sink below event pages so
    # the bounded crawl budget targets gigs first (Task 4). A page that also
    # looks event-y (e.g. /shop/tickets) keeps a net positive score.
    for frag in _LOW_VALUE_FRAGMENTS:
        if frag in low:
            priority -= 4
            break
    # Food / dining pages (street-food-lineup, menu) sink well below neutral
    # event-detail pages unless they also carry an event signal (Task 4).
    if priority <= 0 and not _has_event_signal(low):
        try:
            path = urlparse(low).path
        except ValueError:
            path = ""
        if _segment_words(path) & _FOOD_DINING_WORDS:
            priority -= 8
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
        # Skip cart/checkout/login/legal/"win" pages so the bounded page budget
        # is spent on event-likely pages instead (Task 4).
        if config.CRAWL_SKIP_NON_EVENT_PAGES and _is_unlikely_event_page(clean):
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


def _fetch_html_page(client: httpx.Client, url: str) -> str | None:
    """Fetch one URL and return its HTML, or ``None`` for failures / non-HTML."""
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("crawl skip %s: %s", url, exc)
        return None
    html = resp.text
    ctype = (resp.headers.get("content-type") or "").lower()
    head = html.lower()[:8000]
    if "html" not in ctype and "<html" not in head and "<!doctype html" not in head:
        return None
    return html


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

    # One independent BFS context per seed. We crawl them **round-robin** (one
    # page per seed per round) instead of draining each seed before the next, so
    # the total page budget is shared fairly. Previously the first venue seeds
    # consumed the whole budget, starving the search-result seeds — which is why
    # reports kept showing the same handful of venue hosts every run.
    contexts: list[_SeedCrawl] = []
    for seed in seeds:
        try:
            host = urlparse(seed).netloc.lower()
        except ValueError:
            continue
        if not host:
            continue
        contexts.append(_SeedCrawl(host=host, queue=deque([(seed, 0)])))

    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        while pages_done < max_total and contexts:
            progressed = False
            for ctx in contexts:
                if pages_done >= max_total:
                    break
                if ctx.pages >= per_seed:
                    continue
                next_page = ctx.next_unvisited()
                if next_page is None:
                    continue
                url, depth = next_page
                ctx.visited.add(url)
                progressed = True
                html = _fetch_html_page(client, url)
                if html is None:
                    continue
                chunks.append(_html_to_text(html, url))
                fetched_urls.append(url)
                pages_done += 1
                ctx.pages += 1
                time.sleep(max(0.0, config.CRAWL_DELAY_SEC))
                if depth >= max_depth:
                    continue
                try:
                    for link in _extract_internal_links(url, html, ctx.host):
                        if link not in ctx.visited:
                            ctx.queue.append((link, depth + 1))
                except Exception:
                    continue
            # Drop seeds that can no longer contribute this run.
            contexts = [c for c in contexts if c.queue and c.pages < per_seed]
            if not progressed:
                break

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
