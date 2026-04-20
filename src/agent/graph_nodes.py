"""LangGraph node functions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agent import config
from agent.enrich import enrich_thumbnails
from agent.models import (
    AgentState,
    PlanQueries,
    Resource,
    ResourceListPayload,
    resource_from_dict,
)
from agent.search_tools import run_searches
from agent.snapshot import fingerprint_changed, save_snapshot

logger = logging.getLogger(__name__)

SYSTEM_PLANNER = """You plan web search queries to find HIGH-QUALITY learning resources about
how to build AI agents: physical books, ebooks, online courses, and websites.
Include a mix of query styles (books, courses, LangGraph-specific, general agents).
Output 4 to 8 distinct queries. Each query should be short and specific."""

SYSTEM_CURATOR = """You turn noisy search results into a curated list of resources.

Rules:
- Keep only genuinely useful, reputable-looking sources for learning about AI agents.
- Classify each as book, ebook, course, or website.
- Set langgraph_specific True only when the material is clearly about the LangGraph framework.
  If a result is tied to a different framework only (AutoGen, CrewAI, etc.), omit it unless
  the snippet is broadly about building agents in general.
- price: use a short string like Free, $49, £12.99, or Unknown if not inferable.
- summary: one or two sentences on why this is worth listing.
- Every resource must have a valid http(s) URL from the provided text."""

DEFAULT_QUERIES = [
    "LangGraph AI agents tutorial official documentation",
    "best books building AI agents 2024 2025",
    "online course AI agents LangChain LangGraph",
    "how to build AI agents from scratch guide",
]


def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=config.OPENAI_MODEL,
        temperature=0,
        api_key=config.OPENAI_API_KEY or None,
    )


def node_plan(state: AgentState) -> AgentState:
    """Produce search queries (LLM or defaults if no API key)."""
    if not config.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY missing; using default query list.")
        return {"queries": DEFAULT_QUERIES[: config.MAX_SEARCH_QUERIES]}

    llm = _llm().with_structured_output(PlanQueries)
    msg = HumanMessage(
        content=(
            "Plan search queries to research: how to build AI agents, learning materials, "
            "including LangGraph where relevant. Prefer high-quality sources."
        )
    )
    try:
        plan: PlanQueries = llm.invoke(
            [SystemMessage(content=SYSTEM_PLANNER), msg]
        )
        qs = (plan.queries or [])[: config.MAX_SEARCH_QUERIES]
        if len(qs) < 3:
            qs = DEFAULT_QUERIES[: config.MAX_SEARCH_QUERIES]
        return {"queries": qs}
    except Exception as exc:
        logger.exception("Plan step failed: %s", exc)
        return {"queries": DEFAULT_QUERIES[: config.MAX_SEARCH_QUERIES]}


def node_search(state: AgentState) -> AgentState:
    queries = state.get("queries") or DEFAULT_QUERIES
    text = run_searches(queries)
    return {"raw_search_text": text}


def node_normalize(state: AgentState) -> AgentState:
    raw = state.get("raw_search_text") or ""
    if not config.OPENAI_API_KEY:
        return {
            "resources": [],
            "run_log_message": "OPENAI_API_KEY missing; cannot normalize results.",
        }

    llm = _llm().with_structured_output(ResourceListPayload)
    msg = HumanMessage(
        content=(
            "Here are DuckDuckGo search results. Extract the curated resource list.\n\n"
            f"{raw[:120_000]}"
        )
    )
    try:
        out: ResourceListPayload = llm.invoke(
            [SystemMessage(content=SYSTEM_CURATOR), msg]
        )
        # Dedupe by URL
        seen: set[str] = set()
        unique: list[Resource] = []
        for r in out.resources:
            u = (r.url or "").strip().lower()
            if not u.startswith("http") or u in seen:
                continue
            seen.add(u)
            unique.append(r)
        return {
            "resources": [r.model_dump() for r in unique],
            "run_log_message": "",
        }
    except Exception as exc:
        logger.exception("Normalize failed: %s", exc)
        return {
            "resources": [],
            "run_log_message": f"Normalize error: {exc}",
        }


def node_enrich(state: AgentState) -> AgentState:
    raw_res = state.get("resources") or []
    resources = [resource_from_dict(d) for d in raw_res]
    enriched = enrich_thumbnails(resources)
    return {"resources": [r.model_dump() for r in enriched]}


def node_fingerprint(state: AgentState) -> AgentState:
    resources = [resource_from_dict(d) for d in (state.get("resources") or [])]
    fp, unchanged = fingerprint_changed(resources, config.SNAPSHOT_PATH)
    return {
        "fingerprint": fp,
        "fingerprint_unchanged": unchanged,
        "skip_doc_rewrite": unchanged,
    }


def build_run_log_message(state: AgentState) -> str:
    """Human-readable line for run log + stdout."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    unchanged = state.get("fingerprint_unchanged")
    n = len(state.get("resources") or [])
    extra = state.get("run_log_message") or ""
    dry = state.get("dry_run", False)
    if extra:
        return f"{ts} - {extra}"
    if dry:
        return (
            f"{ts} - dry-run: {n} resources curated; local files and snapshot writes skipped."
        )
    if unchanged:
        return f"{ts} - searched; no content changes since last snapshot ({n} resources tracked)."
    return f"{ts} - searched; updated AgentAI Markdown files with new or changed resources ({n} resources)."


def node_local_output(state: AgentState) -> AgentState:
    """Rewrite agent_research.md when data changed; append run_log.md only when fingerprint unchanged."""
    from agent.local_output import write_output

    msg = build_run_log_message(state)
    dry = state.get("dry_run", False)
    if dry:
        return {"run_log_message": msg}

    skip = state.get("skip_doc_rewrite") or state.get("fingerprint_unchanged")
    resources = [resource_from_dict(d) for d in (state.get("resources") or [])]
    fp = state.get("fingerprint") or ""

    try:
        if skip:
            write_output(resources, append_log_only=True, log_line=msg)
        else:
            write_output(resources, append_log_only=False, log_line=msg)
            save_snapshot(config.SNAPSHOT_PATH, fp, resources)
    except Exception as exc:
        logger.exception("Local output failed: %s", exc)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        mode = "append-only log" if skip else "full Markdown write"
        return {
            "run_log_message": f"{ts} - save failed during {mode} ({len(resources)} resources): {exc}",
        }

    return {"run_log_message": msg}
