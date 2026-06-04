"""Unit tests for graph node diagnostics (no network)."""

import pytest

from agent.graph_nodes import _merge_diagnostic, node_plan, node_search


def test_merge_diagnostic_sets_and_clears_step_note() -> None:
    state = {"pipeline_diagnostics": {"planner": "old"}}
    updated = _merge_diagnostic(state, "search", "Search failed")
    assert updated["pipeline_diagnostics"]["search"] == "Search failed"
    assert "planner" in updated["pipeline_diagnostics"]

    cleared = _merge_diagnostic({**state, **updated}, "search", None)
    assert "search" not in cleared["pipeline_diagnostics"]


def test_node_plan_records_llm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.graph_nodes.config.llm_inference_enabled", lambda: False)

    out = node_plan({})

    assert out["queries"] == []
    assert "planner" in out["pipeline_diagnostics"]
    assert "LLM backend" in out["pipeline_diagnostics"]["planner"]


def test_node_search_skips_without_queries() -> None:
    out = node_search(
        {
            "queries": [],
            "pipeline_diagnostics": {"planner": "Planner failed (TimeoutError): timed out"},
        }
    )

    assert out["raw_search_text"] == ""
    assert "search" not in (out.get("pipeline_diagnostics") or {})
