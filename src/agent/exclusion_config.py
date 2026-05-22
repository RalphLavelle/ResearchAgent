"""YAML configuration for event exclusions (Task 19).

Rules live in ``config/exclusions.yaml`` by default (override with
``EVENT_EXCLUSIONS_CONFIG`` in ``.env``).

- ``drop_terms``: deterministic word-boundary substring drops (no LLM).
- ``exclusions``: phrases passed to the LLM for fuzzier matching when a backend is enabled.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EventExclusionsConfig(BaseModel):
    """Human-authored exclusion configuration."""

    exclusions: list[str] = Field(
        default_factory=list,
        description="Phrases for the LLM to interpret against each row.",
    )
    drop_terms: list[str] = Field(
        default_factory=list,
        description=(
            "Case-insensitive whole-word terms; if any appear in name/venue/"
            "location/summary/url, the row is dropped without an LLM call."
        ),
    )


def load_event_exclusions(path: Path) -> EventExclusionsConfig:
    """Load exclusions from YAML; missing file yields an empty config (no prune)."""
    if not path.exists():
        logger.info(
            "Event exclusions file not found (%s) — LLM exclusion pruning disabled.",
            path,
        )
        return EventExclusionsConfig()

    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        return EventExclusionsConfig()
    if not isinstance(data, dict):
        logger.warning("Ignoring malformed event exclusions YAML (expected mapping).")
        return EventExclusionsConfig()
    try:
        return EventExclusionsConfig(**data)
    except Exception as exc:
        logger.warning("Could not parse event exclusions YAML: %s", exc)
        return EventExclusionsConfig()
