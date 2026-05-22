"""Thumbnail enrichment for curated resources (Tasks 5, 12).

Two passes run after the curator LLM, in this order, so that **events sharing
a listing URL still end up with distinct images** (Task 12):

1. **Per-event matching for shared-URL groups.**
   When ``N`` resources curated by the LLM all point at the same listing
   page (e.g. five gigs on `https://venue.com/whats-on`) and they don't
   already have ``N`` distinct thumbnails, fetch that page **once** and
   match each resource to a different ``<img>`` by comparing its act name
   to each candidate image's alt text.

2. **og:image fallback for solo / unmatched resources.**
   For resources that still have no thumbnail (single-event pages or pages
   where Pass 1 ran out of candidates), fetch the page's ``og:image`` meta
   tag — the original behaviour from Task 5. Per-URL caching means we
   never re-fetch the same listing page twice in one run.

The pure helpers (``_extract_img_candidates``, ``_best_img_for_title``) are
exported so they can be unit-tested without touching the network.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from agent.event_window import split_title_parts
from agent.models import Resource

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; AIAgentResearch/0.1; +https://example.local) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_HTML_BYTES = 900_000

# These small or decorative images appear on most CMS pages and should never be
# offered as event posters — keep them out of the candidate pool entirely.
#
# We match these tokens *anywhere in the URL path* (not just at a path segment
# boundary) because real-world CMS filenames embed them mid-name as well —
# e.g. ``toowong-logo-600px.jpg`` or ``ad-set-1-laptop-tower-ad.png``. The
# previous ``(?:^|/)`` anchor missed those and let header logos and sidebar
# ads dominate per-event matching (Task 13 follow-up).
_IMG_DECORATION_TOKENS = (
    "logo", "favicon", "sprite", "icon", "spinner", "placeholder",
    "tracking", "pixel", "advert", "banner", "header-", "footer-",
    "sidebar", "promo", "social", "share-", "arrow", "navbar",
)
_IMG_DECORATION_RE = re.compile(
    r"(?:^|[/_\-])(?:" + "|".join(_IMG_DECORATION_TOKENS) + r")",
    re.IGNORECASE,
)
# Standalone ``ad`` and ``ads`` are too generic to safely substring-match
# (would reject paths like ``/loading/``), so check them as their own segment
# or ``ad-`` / ``-ad-`` style chunks separately.
_IMG_AD_RE = re.compile(
    r"(?:^|[/_\-])ads?(?:[/_\-.]|$)",
    re.IGNORECASE,
)

# Words too generic to count as a real title↔alt match (otherwise every event
# in Brisbane "matches" every Brisbane image).
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "this", "that", "your",
    "live", "music", "show", "shows", "tour", "tickets", "ticket", "event",
    "events", "gig", "gigs", "concert", "concerts", "presents", "presented",
    "night", "presents", "feat", "featuring", "support", "supports",
    "brisbane", "queensland", "australia", "gold", "coast", "city",
})

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)


def _absolute_url(base: str, maybe: str) -> str:
    """Resolve a possibly-relative URL against the page URL."""
    return urljoin(base + "/", maybe)


def _fetch_html(url: str) -> str | None:
    """Single GET; returns body text (truncated) or ``None`` on any failure."""
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
            return body.decode("utf-8", errors="ignore")
    except Exception as exc:
        logger.debug("HTML fetch failed for %s: %s", url, exc)
        return None


def fetch_og_image(url: str) -> str | None:
    """Return the page's ``og:image`` URL or ``None`` (back-compat helper)."""
    text = _fetch_html(url)
    if not text:
        return None
    m = _OG_IMAGE_RE.search(text) or _OG_IMAGE_RE2.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw:
        return None
    return _absolute_url(url, raw)


def _is_candidate_image(src: str, attrs: dict) -> bool:
    """Filter ``<img>`` tags down to plausible event posters.

    Matches the same heuristic as the crawler's inline marker filter so the
    LLM and the deterministic fallback agree on what a "real" image is.
    """
    if not src or src.startswith("data:"):
        return False
    if _IMG_DECORATION_RE.search(src) or _IMG_AD_RE.search(src):
        return False
    for attr in ("width", "height"):
        val = attrs.get(attr)
        try:
            if val is not None and int(str(val)) < 80:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _extract_img_candidates(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return a list of ``(absolute_src, alt_text)`` for every plausible image.

    Order is preserved so the first hit wins ties — usually the first image
    on the page is the most prominent event poster, which is the right
    fallback when alt text is missing or generic.
    """
    soup = BeautifulSoup(html[:MAX_HTML_BYTES], "html.parser")
    out: list[tuple[str, str]] = []
    seen_src: set[str] = set()
    for img in soup.find_all("img"):
        src = (
            (img.get("src") or img.get("data-src") or img.get("data-lazy-src") or "").strip()
        )
        if not _is_candidate_image(src, img.attrs or {}):
            continue
        try:
            abs_src = _absolute_url(base_url, src)
        except ValueError:
            continue
        if abs_src in seen_src:
            continue
        seen_src.add(abs_src)
        alt = (img.get("alt") or "").strip()
        out.append((abs_src, alt))
    return out


def _title_keywords(title: str) -> set[str]:
    """Extract the distinctive words from an event title.

    We focus on the *act name* (the part before ``@``) because act names are
    far more distinctive than venue/location words, then drop short words
    and a curated stop-list of music-event boilerplate.
    """
    act, _venue, _loc = split_title_parts(title)
    primary = (act or title or "").lower()
    primary = re.sub(r"[^a-z0-9' ]+", " ", primary)
    return {w for w in primary.split() if len(w) >= 3 and w not in _STOPWORDS}


def _filename_keywords(src: str) -> set[str]:
    """Extract distinctive words from the URL's last path segment.

    Many CMSes (WordPress especially) name uploaded posters after the act,
    e.g. ``Boy-Bear-with-The-Dreggs-Bears-Den-UK-Rageflower-May-8-2026.jpg``.
    Even when the ``alt`` attribute is empty, the filename carries enough
    signal to pick the right poster — so we mine it for the same kind of
    keyword set we extract from titles.
    """
    if not src:
        return set()
    try:
        path = urlparse(src).path
    except ValueError:
        path = src
    last = path.rsplit("/", 1)[-1] if path else src
    last = re.sub(r"\.[a-z0-9]{1,5}$", "", last, flags=re.IGNORECASE)  # drop extension
    last = re.sub(r"[^a-z0-9' ]+", " ", last.lower())
    return {w for w in last.split() if len(w) >= 3 and w not in _STOPWORDS}


def poster_quality_score(poster_url: str | None, act: str) -> int:
    """Heuristic score for ranking event poster URLs (Task 13 follow-up).

    Used by the spreadsheet dedupe (``local_output.merge_and_write``) to
    decide whether to **upgrade** an existing row's Poster URL when a
    duplicate event is ingested with a fresher thumbnail. Higher = better.

    Tiers:

    - ``-1`` empty / missing
    - ``0``  contains a decoration token (logo / ad / banner / sidebar / …)
    - ``1``  generic image with no detectable signal
    - ``2 + overlap`` filename keywords overlap the act name (best tier)

    Examples:
        >>> poster_quality_score(None, "The Beths")
        -1
        >>> poster_quality_score("https://x/uploads/toowong-logo-600px.jpg", "Boy & Bear")
        0
        >>> poster_quality_score("https://x/og.jpg", "The Beths")
        1
        >>> poster_quality_score(
        ...     "https://x/2026/05/Boy-Bear-with-The-Dreggs.jpg",
        ...     "Boy & Bear",
        ... ) >= 3
        True
    """
    p = (poster_url or "").strip()
    if not p:
        return -1
    if _IMG_DECORATION_RE.search(p) or _IMG_AD_RE.search(p):
        return 0
    overlap = len(_title_keywords(act) & _filename_keywords(p))
    return (2 + overlap) if overlap > 0 else 1


def _best_img_for_title(
    title: str,
    candidates: list[tuple[str, str]],
    *,
    exclude: set[str],
) -> str | None:
    """Pick the candidate image whose alt text or filename best matches *title*.

    Scoring (Task 13 follow-up): count distinctive words shared between the
    title's act name and **both** the image's alt text **and** its filename
    (so pages with empty ``alt`` attributes but descriptive upload names
    still match correctly). Ties are broken by original order in the list.

    Returns ``None`` when no candidate scores **above zero**, so callers can
    leave the slot for the ``og:image`` fallback rather than handing out
    arbitrary first-non-excluded images. Previously this returned the first
    unused candidate, which produced systematic off-by-N misassignments on
    pages whose DOM begins with a logo, an ad, and a banner before the real
    event posters.
    """
    title_words = _title_keywords(title)
    if not title_words:
        return None
    best_score = 0
    best_src: str | None = None
    for src, alt in candidates:
        if src in exclude:
            continue
        alt_words = {
            w for w in re.sub(r"[^a-z0-9' ]+", " ", alt.lower()).split()
            if len(w) >= 3 and w not in _STOPWORDS
        }
        context = alt_words | _filename_keywords(src)
        score = len(title_words & context)
        if score > best_score:
            best_score = score
            best_src = src
    return best_src  # None when no candidate had a meaningful overlap


def _assign_per_event_images(resources: list[Resource]) -> list[Resource]:
    """Pass 1 (Task 12 + Task 13 follow-up): give each shared-URL resource its own image.

    Strategy (revised):

    1. Group resources by URL.  Singletons need no per-event matching.
    2. **Preserve** any LLM-assigned thumbnail that is unique within the
       group — the curator already saw inline ``[IMG ...]`` markers and
       made a deliberate per-event pick, which is generally better than
       anything we can reconstruct from the bare HTML.  Reserve those URLs
       so they cannot be reassigned to siblings.
    3. Only fetch the listing page when at least one resource in the group
       still needs a thumbnail (or carries a duplicate one).
    4. Score remaining resources against the ``<img>`` candidates using
       both ``alt`` text and filename keywords.  ``_best_img_for_title``
       returns ``None`` when no candidate has any meaningful overlap, so
       the resource falls through to the ``og:image`` fallback rather
       than being handed an arbitrary logo or banner.
    """
    by_url: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(resources):
        url = (r.url or "").strip()
        if url:
            by_url[url].append(i)

    out: list[Resource] = list(resources)
    for url, indices in by_url.items():
        if len(indices) < 2:
            continue
        thumbs = [(out[i].thumbnail_url or "").strip() for i in indices]

        # Identify which of the LLM's existing thumbnails are unique in this
        # group — those are trusted picks we'll keep no matter what Pass 1
        # discovers.  Anything blank or shared between two resources is
        # eligible for re-assignment.
        thumb_counts: dict[str, int] = {}
        for t in thumbs:
            if t:
                thumb_counts[t] = thumb_counts.get(t, 0) + 1
        kept_already: set[str] = {t for t, c in thumb_counts.items() if c == 1}

        # Already perfectly distinct? Trust the LLM completely.
        if all(thumbs) and len(set(thumbs)) == len(indices):
            continue

        # Indices that still need a thumbnail (blank or duplicate).
        needs_indices = [
            idx for idx, t in zip(indices, thumbs)
            if not t or thumb_counts.get(t, 0) > 1
        ]
        if not needs_indices:
            continue

        html = _fetch_html(url)
        if not html:
            continue
        candidates = _extract_img_candidates(html, base_url=url)
        if not candidates:
            continue

        # ``used`` starts pre-populated with the LLM's trusted picks so we
        # never reassign one event's good poster to another event in the
        # same group.
        used: set[str] = set(kept_already)
        for idx in needs_indices:
            r = out[idx]
            best = _best_img_for_title(r.title or "", candidates, exclude=used)
            if not best:
                # No meaningful match — leave for the og:image fallback.
                # If the existing thumbnail was a duplicate, clearing it
                # lets Pass 2 substitute a (still-shared) og:image rather
                # than keeping a misleading copy.
                if thumb_counts.get((r.thumbnail_url or "").strip(), 0) > 1:
                    out[idx] = r.model_copy(update={"thumbnail_url": None})
                continue
            used.add(best)
            out[idx] = r.model_copy(update={"thumbnail_url": best})
    return out


def enrich_thumbnails(resources: list[Resource]) -> list[Resource]:
    """Two-pass thumbnail resolution.

    1. Per-event matching for groups of resources sharing a URL — fetch the
       page once and assign each event a distinct ``<img>`` by alt text.
    2. ``og:image`` fallback for any resource still without a thumbnail.

    Per-URL HTTP responses are cached for the duration of the call so a
    listing page is fetched at most twice across both passes (once for image
    candidates, once via ``fetch_og_image``).
    """
    enriched = _assign_per_event_images(resources)

    og_cache: dict[str, str | None] = {}
    final: list[Resource] = []
    for r in enriched:
        if r.thumbnail_url:
            final.append(r)
            continue
        url = (r.url or "").strip()
        if url not in og_cache:
            og_cache[url] = fetch_og_image(url)
        final.append(r.model_copy(update={"thumbnail_url": og_cache[url]}))
    return final
