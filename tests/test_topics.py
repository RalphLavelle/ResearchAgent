"""Tests for the topics registry loader."""

import json
from pathlib import Path

import pytest

from agent.topics import (
    TopicEntry,
    TopicsRegistry,
    load_topics,
    resolve_output_dir,
    slugify_topic_id,
    topic_config_dir,
    topic_data_dir,
)


def test_slugify_topic_id() -> None:
    assert slugify_topic_id("Live music in Brisbane and the Gold Coast") == (
        "live-music-in-brisbane-and-the-gold-coast"
    )


def test_load_topics_from_repo_registry() -> None:
    root = Path(__file__).resolve().parents[1]
    reg = load_topics(root / "topics" / "topics.json")
    assert reg.active == "live-music-brisbane-gold-coast"
    entry = reg.topics["live-music-brisbane-gold-coast"]
    assert entry.db == "bgc"
    assert "bg.jpg" in entry.background_image


def test_load_topics_missing_active_raises(tmp_path: Path) -> None:
    p = tmp_path / "topics.json"
    p.write_text(
        '{"active": "missing", "topics": {"other": {"name": "X", "db": "x"}}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Active topic"):
        load_topics(p)


def test_topic_paths(tmp_path: Path) -> None:
    reg = TopicsRegistry(
        active="demo",
        topics={"demo": {"name": "Demo", "db": "demo-data"}},
    )
    entry = reg.topics["demo"]
    assert topic_config_dir(tmp_path, "demo") == tmp_path / "topics" / "demo"
    assert topic_data_dir(tmp_path / "data", "demo") == tmp_path / "data" / "demo"


def test_resolve_output_dir_uses_topic_when_env_is_data_base(tmp_path: Path) -> None:
    base = tmp_path / "data"
    topic = base / "my-topic"
    assert resolve_output_dir(data_base=base, topic_dir=topic, env_override=str(base)) == topic


def test_resolve_output_dir_unset_uses_topic(tmp_path: Path) -> None:
    base = tmp_path / "data"
    topic = base / "my-topic"
    assert resolve_output_dir(data_base=base, topic_dir=topic, env_override=None) == topic
