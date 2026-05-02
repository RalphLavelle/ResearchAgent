"""LLM-assisted same-calendar-day semantic deduplication.

Runs after deterministic spreadsheet merge. Sends all events with named fields
so the model can group rows that describe the same real-world occurrence.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent import config

logger = logging.getLogger(__name__)

_SYSTEM = """You identify duplicate event records that refer to the same real-world occurrence.
Wording may differ: event title, venue name, suburb vs full address, description.
Only group records that share the same calendar date AND clearly describe the same single event.
Do not merge different events on the same day (for example two different gigs at different venues).
Each event id must appear in at most one group. Omit groups of size 1."""


class EventIdCluster(BaseModel):
    event_ids: list[str] = Field(
        min_length=2,
        description="Stable ids of spreadsheet rows that are the same event.",
    )


class SemanticDedupeClusters(BaseModel):
    duplicate_groups: list[EventIdCluster] = Field(default_factory=list)


def _normalize_clusters(raw: list[list[str]], valid_ids: set[str]) -> list[list[str]]:
    """Turn possibly overlapping LLM groups into disjoint clusters of valid ids."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    touched: set[str] = set()
    for group in raw:
        ids = [i.strip() for i in group if i.strip() in valid_ids]
        if len(ids) < 2:
            continue
        touched.update(ids)
        first = ids[0]
        for other in ids[1:]:
            union(first, other)

    buckets: dict[str, list[str]] = {}
    for vid in touched:
        root = find(vid)
        buckets.setdefault(root, []).append(vid)
    return [sorted(set(g)) for g in buckets.values() if len(set(g)) >= 2]


def find_same_event_clusters(events: list[dict]) -> list[list[str]]:
    """Call the LLM; return disjoint clusters of event ids (same day, same event)."""
    if len(events) < 2:
        return []
    if not config.OPENAI_API_KEY:
        return []

    valid_ids = {str(e.get("id") or "").strip() for e in events}
    valid_ids.discard("")

    body = json.dumps(events, ensure_ascii=False, indent=2)
    if len(body) > 180_000:
        body = body[:180_000] + "\n…(truncated)"
        logger.warning("Semantic dedupe prompt truncated for size.")

    llm = ChatOpenAI(
        model=config.OPENAI_MODEL,
        temperature=0,
        api_key=config.OPENAI_API_KEY or None,
    ).with_structured_output(SemanticDedupeClusters)

    try:
        out: SemanticDedupeClusters = llm.invoke(
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=(
                        "Each object is one event; property names explain the fields. "
                        "Find groups of ids that are duplicates (same date, same real-world event).\n\n"
                        f"{body}"
                    )
                ),
            ]
        )
    except Exception as exc:
        logger.warning("Semantic dedupe LLM call failed: %s", exc)
        return []

    raw_groups = [g.event_ids for g in (out.duplicate_groups or [])]
    clusters = _normalize_clusters(raw_groups, valid_ids)
    if clusters:
        logger.info("Semantic dedupe: %d duplicate cluster(s) from LLM.", len(clusters))
    return clusters
