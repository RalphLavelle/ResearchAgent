"""Topic-specific prompt fragments injected by the engine.

Generic date-window boilerplate stays in ``event_window.py``; geography,
priority rules, and resource labels live in ``topics/<id>/prompt_guides.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class PromptGuides(BaseModel):
    """Optional text the engine appends to planner/curator date instructions."""

    model_config = ConfigDict(extra="ignore")

    # Woven into engine-built sentences (defaults suit any dated-event topic).
    resource_label_plural: str = Field(
        default="events",
        description="Noun phrase for planner window text, e.g. 'gigs and concerts'.",
    )
    curator_resource_label_plural: str = Field(
        default="events",
        description="Noun phrase in curator date preamble, e.g. 'gigs or concerts'.",
    )
    portal_avoid_hint: str = Field(
        default=(
            "Avoid generic portal homepages; aim for pages that list specific dated events."
        ),
    )

    # Topic-specific blocks appended after the generic date-window text.
    planner_date_suffix: str = ""
    curator_date_suffix: str = ""


def load_prompt_guides(path: Path) -> PromptGuides:
    """Load ``prompt_guides.yaml`` for the active topic, or return defaults."""
    if not path.exists():
        return PromptGuides()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return PromptGuides(**raw)
