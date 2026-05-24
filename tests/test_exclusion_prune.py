"""Tests for post-merge event exclusions (Task 19)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from agent.exclusion_config import load_event_exclusions
from agent.exclusion_prune import ExclusionPruneResult, apply_event_exclusions
from agent.local_output import load_spreadsheet_resources, merge_and_write
from agent.models import Resource


def test_load_event_exclusions_missing_file_returns_empty(tmp_path: Path) -> None:
    cfg = load_event_exclusions(tmp_path / "nope.yaml")
    assert cfg.exclusions == []
    assert cfg.drop_terms == []


def test_load_event_exclusions_parses_drop_terms_and_exclusions(tmp_path: Path) -> None:
    p = tmp_path / "ex.yaml"
    p.write_text(
        "drop_terms:\n  - bingo\nexclusions:\n  - Line one\n",
        encoding="utf-8",
    )
    cfg = load_event_exclusions(p)
    assert cfg.drop_terms == ["bingo"]
    assert cfg.exclusions == ["Line one"]


def test_apply_event_exclusions_drop_terms_remove_drag_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whole-word ``drop_terms`` must work with LLM disabled."""
    ex_yaml = tmp_path / "exclusions.yaml"
    ex_yaml.write_text(
        "drop_terms:\n  - drag\nexclusions: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.config.EVENT_EXCLUSIONS_CONFIG_PATH", ex_yaml)
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)

    d = (date.today() + timedelta(days=5)).isoformat()
    merge_and_write(
        [
            Resource(
                title="Drag Bingo Night @ Club X, Brisbane",
                url="https://example.com/a",
                date=d,
            ),
            Resource(
                title="Indie Band @ Hall Y, Brisbane",
                url="https://example.com/b",
                date=d,
            ),
        ],
    )
    assert len(load_spreadsheet_resources()) == 2

    removed = apply_event_exclusions()
    assert removed == 1
    remaining = load_spreadsheet_resources()
    assert len(remaining) == 1
    assert "Indie Band" in (remaining[0].title or "")


def test_apply_event_exclusions_llm_union_with_drop_terms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM ids union with deterministic drops."""
    ex_yaml = tmp_path / "exclusions.yaml"
    ex_yaml.write_text(
        "drop_terms:\n  - bingo\nexclusions:\n  - school fundraiser nights\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.config.EVENT_EXCLUSIONS_CONFIG_PATH", ex_yaml)
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", True)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)

    d = (date.today() + timedelta(days=5)).isoformat()
    rid_bingo = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    rid_school = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    merge_and_write(
        [
            Resource(
                id=rid_bingo,
                title="Music Bingo @ Pub",
                url="https://example.com/bingo",
                date=d,
            ),
            Resource(
                id=rid_school,
                title="Spring Fair @ State High",
                url="https://example.com/school",
                date=d,
                summary="School fundraiser night ticket sales.",
            ),
        ],
    )

    monkeypatch.setattr("agent.exclusion_prune.build_chat_llm", lambda: object())

    def fake_invoke(_llm, _messages, output_model):  # noqa: ANN001
        assert output_model is ExclusionPruneResult
        return ExclusionPruneResult(excluded_event_ids=[rid_school])

    monkeypatch.setattr("agent.exclusion_prune.invoke_structured", fake_invoke)

    removed = apply_event_exclusions()
    assert removed == 2
    assert load_spreadsheet_resources() == []


def test_apply_event_exclusions_skips_when_empty_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ex_yaml = tmp_path / "exclusions.yaml"
    ex_yaml.write_text("exclusions: []\ndrop_terms: []\n", encoding="utf-8")
    monkeypatch.setattr("agent.config.EVENT_EXCLUSIONS_CONFIG_PATH", ex_yaml)
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)

    d = (date.today() + timedelta(days=5)).isoformat()
    merge_and_write([Resource(title="Band @ Venue", url="https://example.com/x", date=d)])

    def boom(*_a, **_k):
        raise AssertionError("invoke_structured should not run")

    monkeypatch.setattr("agent.exclusion_prune.invoke_structured", boom)

    assert apply_event_exclusions() == 0
