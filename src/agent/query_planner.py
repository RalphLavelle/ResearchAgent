"""Helpers that push the planner toward diverse, non-repeating search queries."""

from __future__ import annotations

import random
from typing import Sequence

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
