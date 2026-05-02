"""Semantic LLM merge wiring tests (no OpenAI calls)."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from agent.local_output import load_spreadsheet_resources, merge_and_write, run_llm_semantic_dedupe
from agent.models import Resource


def test_semantic_merge_collapses_two_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cluster of two ids merges into one row; richer text and poster preserved."""
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "fake-key-for-branch")

    d = (date.today() + timedelta(days=10)).isoformat()
    r1 = Resource(
        id="keep-me",
        title="Dead of Winter Band Comp @ Mo's, Burleigh",
        url="https://a.example/event",
        date=d,
        summary="Short.",
        thumbnail_url="https://a.example/p.jpg",
    )
    r2 = Resource(
        id="drop-me",
        title="Dead of Winter Festival Band Comp @ Burleigh, QLD",
        url="https://b.example/event",
        date=d,
        summary="Longer summary text for the band competition.",
        thumbnail_url=None,
    )
    merge_and_write([r1])
    merge_and_write([r2])

    def fake_clusters(_events: list) -> list[list[str]]:
        return [["keep-me", "drop-me"]]

    monkeypatch.setattr("agent.semantic_dedupe.find_same_event_clusters", fake_clusters)

    removed = run_llm_semantic_dedupe()
    assert removed == 1

    loaded = load_spreadsheet_resources()
    assert len(loaded) == 1
    assert loaded[0].id == "keep-me"
    assert loaded[0].thumbnail_url == "https://a.example/p.jpg"
    assert "Longer summary" in loaded[0].summary
    assert "Dead of Winter Festival Band Comp" in loaded[0].title
