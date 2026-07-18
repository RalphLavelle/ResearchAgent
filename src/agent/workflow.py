"""Compiled LangGraph workflow."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.models import AgentState
from agent.graph_nodes import (
    node_crawl,
    node_enrich,
    node_fingerprint,
    node_local_output,
    node_normalize,
    node_plan,
    node_search,
)


def build_graph():
    """Build and compile the research → local Markdown output graph."""
    g = StateGraph(AgentState)
    g.add_node("plan", node_plan)
    g.add_node("search", node_search)
    g.add_node("crawl", node_crawl)
    g.add_node("normalize", node_normalize)
    g.add_node("enrich", node_enrich)
    g.add_node("fingerprint", node_fingerprint)
    g.add_node("output", node_local_output)

    g.add_edge(START, "plan")
    g.add_edge("plan", "search")
    g.add_edge("search", "crawl")
    g.add_edge("crawl", "normalize")
    g.add_edge("normalize", "enrich")
    g.add_edge("enrich", "fingerprint")
    g.add_edge("fingerprint", "output")
    g.add_edge("output", END)

    return g.compile()


def run_once(*, dry_run: bool = False, targeted_query: str | None = None) -> AgentState:
    """Execute one full pass.

    When *targeted_query* is set, the planner uses that single search phrase
    instead of LLM-generated queries (admin targeted search, Task 3).
    """
    from agent.migrations_runner import run_pending_migrations

    run_pending_migrations()
    graph = build_graph()
    initial: AgentState = {"dry_run": dry_run}
    cleaned = (targeted_query or "").strip()
    if cleaned:
        initial["targeted_query"] = cleaned
    result = graph.invoke(initial)
    return result
