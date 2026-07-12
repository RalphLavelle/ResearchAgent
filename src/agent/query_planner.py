"""Helpers that push the planner toward diverse, non-repeating search queries."""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

from agent.prompt_guides import PromptGuides
from agent.report_store import recent_search_queries


def pick_query_angles(
    angles: Sequence[str],
    *,
    count: int = 5,
    rng: random.Random | None = None,
) -> list[str]:
    """Return *count* shuffled angles from the topic's planner guide list."""
    pool = [a.strip() for a in angles if str(a).strip()]
    if not pool:
        return []
    r = rng or random.Random()
    if len(pool) <= count:
        chosen = list(pool)
        r.shuffle(chosen)
        return chosen
    return r.sample(pool, count)


def build_planner_variation_block(
    guides: PromptGuides,
    *,
    recent_queries: Sequence[str] | None = None,
    rng: random.Random | None = None,
) -> str:
    """Text appended to the planner user message to reduce samey queries."""
    parts: list[str] = []

    recent = [q.strip() for q in (recent_queries or []) if str(q).strip()]
    if recent:
        lines = "\n".join(f"- {q}" for q in recent[:40])
        parts.append(
            "## Do not repeat recent searches\n"
            "These queries were used on recent pipeline runs. Do **not** reuse them "
            "or produce near-duplicates (same venue + same month + same wording). "
            "Reach for new suburbs, genres, sources, and phrasing:\n"
            f"{lines}\n"
        )

    angles = pick_query_angles(
        guides.planner_query_angles,
        count=min(6, max(3, guides.planner_angle_pick_count)),
        rng=rng,
    )
    if angles:
        angle_lines = "\n".join(f"- {a}" for a in angles)
        parts.append(
            "## Prioritise these fresh angles THIS run\n"
            "Cover several of the angles below. At least half your queries should "
            "follow an angle **not** represented in the recent-search list above:\n"
            f"{angle_lines}\n"
        )

    parts.append(
        "## Query format variety (required)\n"
        "Mix structures — **not** every query should look like "
        '"Venue Name City upcoming gigs Month Year". Include a blend of:\n'
        "- Natural questions (e.g. \"who is playing in West End Brisbane this weekend?\")\n"
        "- Short keyword clusters (3–7 words, no filler)\n"
        "- Main, well-known venues in the main entertainment areas of Brisbane and Gold Coast\n"
        "- Niche listings: open mic, jam, blues, folk, classical, world, DJ, tribute\n"
        "- Lesser-covered suburbs and corridors (Logan, Ipswich, Redlands, Moreton Bay)\n"
        "- Community halls, RSLs, bowls clubs, breweries, markets, record stores\n"
    )

    return "\n".join(parts)


def load_recent_planner_queries(db_name: str, *, limit: int = 30) -> list[str]:
    """Recent search strings from MongoDB run reports (newest runs first)."""
    return recent_search_queries(db_name, limit=limit)


def _venue_weight_key(venue: Mapping[str, Any]) -> str:
    """Stable lookup key for optional venue selection weights."""
    venue_id = str(venue.get("_id") or venue.get("venue_id") or "").strip()
    if venue_id:
        return venue_id
    return str(venue.get("name") or "").strip().lower()


def _weighted_sample_without_replacement(
    items: Sequence[Mapping[str, Any]],
    weights: Sequence[float],
    count: int,
    rng: random.Random,
) -> list[Mapping[str, Any]]:
    """Pick *count* distinct items, favouring higher weights."""
    pool = list(items)
    pool_weights = [max(0.0, float(w)) for w in weights]
    chosen: list[Mapping[str, Any]] = []
    picks = min(max(0, count), len(pool))
    for _ in range(picks):
        total = sum(pool_weights)
        if total <= 0:
            idx = rng.randrange(len(pool))
        else:
            idx = rng.choices(range(len(pool)), weights=pool_weights, k=1)[0]
        chosen.append(pool.pop(idx))
        pool_weights.pop(idx)
    return chosen


def _render_venue_query(template: str, *, venue: str, location: str) -> str:
    """Fill a venue-query template, tidying gaps left by a blank location.

    ``"What's on in {venue} in {location}, Australia"`` with an empty location
    becomes ``"What's on in The Triffid, Australia"`` instead of leaving a
    dangling ``" in ,"``.
    """
    try:
        rendered = template.format(venue=venue, location=location)
    except (KeyError, IndexError, ValueError):
        # Bad template placeholders — fall back to a sensible plain query.
        rendered = f"What's on at {venue}".strip()
    # Tidy the common "blank location" artefacts, then collapse double spaces.
    rendered = rendered.replace(" in ,", ",").replace(" in .", ".")
    return " ".join(rendered.split()).strip()


def build_targeted_venue_queries(
    venues: Sequence[Mapping[str, Any]],
    guides: PromptGuides,
    *,
    rng: random.Random | None = None,
    weights: Mapping[str, float] | None = None,
) -> list[str]:
    """Build a random handful of "What's on in <venue>" search queries.

    Picks between ``guides.venue_query_min`` and ``guides.venue_query_max``
    venues using optional *weights* (higher-yield or stale venues rank higher).
    When *weights* is omitted, selection is uniform random. The ``{location}``
    slot uses the venue's own stored ``location`` when present, otherwise a
    random fallback from ``guides.venue_query_locations``.
    """
    template = (guides.venue_query_template or "").strip()
    if not template:
        return []

    named = [v for v in venues if str((v or {}).get("name") or "").strip()]
    if not named:
        return []

    r = rng or random.Random()
    lo = max(0, int(guides.venue_query_min))
    hi = max(lo, int(guides.venue_query_max))
    want = min(r.randint(lo, hi), len(named))
    if want <= 0:
        return []

    if weights:
        row_weights = [float(weights.get(_venue_weight_key(v), 1.0)) for v in named]
        chosen = _weighted_sample_without_replacement(named, row_weights, want, r)
    else:
        chosen = r.sample(named, want)
    locations = [
        str(loc).strip()
        for loc in (guides.venue_query_locations or [])
        if str(loc).strip()
    ]

    queries: list[str] = []
    seen: set[str] = set()
    for venue in chosen:
        name = str(venue.get("name") or "").strip()
        location = str(venue.get("location") or "").strip()
        if not location and locations:
            location = r.choice(locations)
        query = _render_venue_query(template, venue=name, location=location)
        key = query.lower()
        if query and key not in seen:
            seen.add(key)
            queries.append(query)
    return queries


def load_targeted_venue_queries(
    db_name: str,
    guides: PromptGuides,
    *,
    rng: random.Random | None = None,
) -> list[str]:
    """Load venues from MongoDB and build targeted "What's on" queries."""
    from agent.strategy_scores import build_venue_query_weights
    from agent.venue_store import list_venues

    try:
        venues = list_venues(db_name)
        weights = build_venue_query_weights(db_name, venues)
    except Exception:
        # Venue store unavailable (e.g. no Mongo) — skip targeted queries.
        return []
    return build_targeted_venue_queries(venues, guides, rng=rng, weights=weights)


def merge_queries(
    targeted: Sequence[str],
    planned: Sequence[str],
    *,
    limit: int,
) -> list[str]:
    """Combine targeted venue queries (priority) with planner queries.

    Targeted queries come first so they always survive the ``limit`` cap; the
    remaining slots are filled with planner queries, dropping case-insensitive
    duplicates. This is what lets each run "discard some" planner queries in
    favour of the targeted venue ones.
    """
    combined: list[str] = []
    seen: set[str] = set()
    for query in list(targeted) + list(planned):
        text = (query or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        combined.append(text)
    if limit > 0:
        return combined[:limit]
    return combined
