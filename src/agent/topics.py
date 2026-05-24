"""Load the topics registry and resolve per-topic paths.

``topics/topics.json`` is read at startup. The active topic id selects which
folder under ``topics/<id>/`` holds subject-matter YAML, exclusions, schedule,
and UI assets. Curated events live in MongoDB under the topic's ``db`` name;
run reports and snapshots still land in ``data/<topic_id>/``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TopicEntry(BaseModel):
    """One research topic — config folder + MongoDB db + web chrome."""

    model_config = ConfigDict(extra="ignore")

    name: str
    db: str = Field(
        description="MongoDB database name for events and images collections.",
    )
    background_image: str = Field(
        default="/topics/default/assets/bg.jpg",
        description="URL path served by the Angular app for the page background.",
    )
    site_title: str = "Research events"
    site_emoji: str = "🎵"


class TopicsRegistry(BaseModel):
    """Root object in topics/topics.json."""

    active: str
    topics: dict[str, TopicEntry]


def slugify_topic_id(name: str) -> str:
    """Turn a human title into a stable folder id (kebab-case)."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "topic"


def load_topics(path: Path) -> TopicsRegistry:
    """Read and validate topics.json."""
    if not path.exists():
        raise FileNotFoundError(
            f"Topics registry not found: {path}\n"
            "Create topics/topics.json or set TOPICS_CONFIG in .env."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        registry = TopicsRegistry(**raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Invalid topics registry {path}: {exc}") from exc

    if registry.active not in registry.topics:
        raise ValueError(
            f"Active topic {registry.active!r} is missing from topics in {path}"
        )
    return registry


def topic_config_dir(repo_root: Path, topic_id: str) -> Path:
    """Directory holding subject_matter.yaml, exclusions.yaml, schedule.yaml."""
    return repo_root / "topics" / topic_id


def topic_data_dir(data_base: Path, topic_id: str) -> Path:
    """Per-topic folder for run reports and snapshots under the data/ root."""
    return data_base / topic_id


def resolve_output_dir(
    *,
    data_base: Path,
    topic_dir: Path,
    env_override: str | None,
) -> Path:
    """Return the pipeline output folder for run reports (topic subfolder).

    - Unset ``OUTPUT_DIR`` → ``topic_dir`` (``data/<topic_id>/``).
    - ``OUTPUT_DIR`` equals ``data_base`` → ``topic_dir`` (legacy flat ``data/`` in .env).
    - ``OUTPUT_DIR`` already equals ``topic_dir`` → unchanged.
    - Any other explicit path → honoured verbatim (custom location).
    """
    raw = (env_override or "").strip().strip("'\"")
    if not raw:
        return topic_dir

    override = Path(raw).expanduser().resolve()
    base = data_base.resolve()
    topic = topic_dir.resolve()

    if override == base:
        return topic
    if override == topic:
        return override
    return override
