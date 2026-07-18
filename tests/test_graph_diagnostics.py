"""Unit tests for graph node diagnostics (no network)."""

import pytest

from agent.graph_nodes import _merge_diagnostic, node_plan, node_search
from agent.runner import LLMInvocationError


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


def test_node_plan_uses_targeted_admin_query_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin targeted search skips planner LLM and venue templates."""
    monkeypatch.setattr("agent.graph_nodes.config.llm_inference_enabled", lambda: False)
    monkeypatch.setattr(
        "agent.graph_nodes.load_targeted_venue_queries",
        lambda *_a, **_k: ["Should not be used"],
    )

    out = node_plan({"targeted_query": "  Powderfinger Brisbane 2026  "})

    assert out["queries"] == ["Powderfinger Brisbane 2026"]
    assert out["pipeline_diagnostics"]["planner"] == (
        "Targeted admin search (single query): Powderfinger Brisbane 2026"
    )


def test_node_plan_aborts_when_llm_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First LLM failure must stop the run even if targeted venue queries exist."""
    monkeypatch.setattr("agent.graph_nodes.config.llm_inference_enabled", lambda: True)
    monkeypatch.setattr(
        "agent.graph_nodes.load_targeted_venue_queries",
        lambda *_a, **_k: ["What's on in The Triffid in Brisbane, Australia"],
    )
    monkeypatch.setattr(
        "agent.graph_nodes.load_recent_planner_queries",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "agent.graph_nodes.build_planner_variation_block",
        lambda *_a, **_k: "vary it",
    )
    monkeypatch.setattr("agent.graph_nodes.build_planner_llm", lambda **_k: object())

    def boom(*_a, **_k):
        raise RuntimeError('model "qwen3" not found')

    monkeypatch.setattr("agent.graph_nodes.invoke_structured", boom)

    with pytest.raises(LLMInvocationError) as caught:
        node_plan({})

    assert "Planner" in str(caught.value)
    assert "qwen3" in str(caught.value)


def test_node_search_skips_without_queries() -> None:
    out = node_search(
        {
            "queries": [],
            "pipeline_diagnostics": {"planner": "Planner failed (TimeoutError): timed out"},
        }
    )

    assert out["raw_search_text"] == ""
    assert "search" not in (out.get("pipeline_diagnostics") or {})
