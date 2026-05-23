"""Load the topics registry and resolve per-topic paths.

``topics/topics.json`` is read at startup. The active topic id selects which
folder under ``topics/<id>/`` holds subject-matter YAML, exclusions, schedule,
and UI assets. Pipeline output lands under ``data/<data_dir>/`` by default.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TopicEntry(BaseModel):
    """One research topic — config folder + data subfolder + web chrome."""

    model_config = ConfigDict(extra="ignore")

    name: str
    data_dir: str = Field(
        description="Subfolder under the repo data/ root for spreadsheet, JSON, and run reports.",
    )
    background_image: str = Field(
        default="/topics/default/assets/bg.jpg",
        description="URL path served by the Angular app for the page background.",
    )
    site_title: str = "Research events"
    site_emoji: str = "🎵"
    home_heading: str = "Upcoming events"


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


def topic_data_dir(data_base: Path, entry: TopicEntry) -> Path:
    """Per-topic output folder under the data/ root."""
    return data_base / entry.data_dir


# Files that lived at ``data/`` before topic segregation.
_LEGACY_DATA_ROOT_FILES = (
    "agent_research.xlsx",
    "events.json",
    "snapshot.json",
    "notion_sync_state.json",
)


def resolve_output_dir(
    *,
    data_base: Path,
    topic_dir: Path,
    env_override: str | None,
) -> Path:
    """Return the pipeline output folder (topic subfolder when env points at ``data/``).

    - Unset ``OUTPUT_DIR`` → ``topic_dir`` (``data/<data_dir>/``).
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


def migrate_legacy_flat_data(data_base: Path, entry: TopicEntry) -> int:
    """Move flat ``data/*`` pipeline artefacts into ``data/<data_dir>/``.

    Runs when the topic folder has no ``events.json`` yet but legacy root files
    exist. Returns the number of top-level items moved.
    """
    target = topic_data_dir(data_base, entry)
    if (target / "events.json").exists():
        return 0

    legacy_files = [data_base / name for name in _LEGACY_DATA_ROOT_FILES]
    legacy_runs = list(data_base.glob("Run_*.md"))
    legacy_images = data_base / "images"
    candidates = [p for p in legacy_files if p.exists()]
    candidates.extend(legacy_runs)
    if legacy_images.is_dir():
        candidates.append(legacy_images)

    if not candidates:
        return 0

    target.mkdir(parents=True, exist_ok=True)
    moved = 0
    for src in candidates:
        dest = target / src.name
        if dest.exists():
            continue
        src.rename(dest)
        moved += 1
    return moved


def rewrite_events_json_poster_paths(events_path: Path, *, topic_data_dir: str) -> bool:
    """Fix legacy ``data/images/`` thumbnail URLs inside ``events.json``.

    Returns True when the file was updated.
    """
    if not events_path.exists():
        return False
    try:
        data = json.loads(events_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    legacy = "data/images/"
    topic_prefix = f"data/{topic_data_dir}/images/"
    changed = False
    for ev in data.get("events") or []:
        if not isinstance(ev, dict):
            continue
        thumb = str(ev.get("thumbnailUrl") or "").strip()
        if not thumb or thumb.lower().startswith("http"):
            continue
        updated = thumb.lstrip("/")
        if updated.startswith(legacy) and not updated.startswith(topic_prefix):
            updated = topic_prefix + updated[len(legacy) :]
            changed = True
        web = f"/{updated}"
        if web != thumb:
            ev["thumbnailUrl"] = web
            changed = True

    if changed:
        events_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return changed
