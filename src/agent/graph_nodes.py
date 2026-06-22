"""LangGraph node functions.

Each function here is one *step* in the research pipeline. The steps are
deliberately kept topic-agnostic: they know nothing about AI agents, music
events, or any other subject. All the topic-specific text (prompts, queries,
titles) comes from config.SUBJECT, which is loaded from the subject_matter.yaml
file at startup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import monotonic

from langchain_core.messages import HumanMessage, SystemMessage

from agent import config
from agent.enrich import enrich_thumbnails
from agent.event_window import (
    curator_date_instruction,
    filter_future_events,
    parse_event_sort_date,
    planner_date_instruction,
    sort_resources_by_event_date_asc,
)
from agent.llm_factory import build_chat_llm, build_planner_llm
from agent.pipeline_diagnostics import format_step_error
from agent.query_planner import (
    build_planner_variation_block,
    load_recent_planner_queries,
    load_targeted_venue_queries,
    merge_queries,
)
from agent.site_crawl import deep_search_supplement
from agent.models import (
    AgentState,
    PlanQueries,
    Resource,
    ResourceListPayload,
    resource_from_dict,
)
from agent.structured_output import invoke_structured
from agent.search_tools import run_searches
from agent.snapshot import fingerprint_changed, save_snapshot

logger = logging.getLogger(__name__)

CRAWL_SECTION_MARKER = "## Same-site crawl (bounded)"


def _merge_diagnostic(state: AgentState, step: str, message: str | None) -> dict[str, dict[str, str]]:
    """Update one pipeline diagnostic entry, clearing it when *message* is None."""
    current = dict(state.get("pipeline_diagnostics") or {})
    if message:
        current[step] = message
    else:
        current.pop(step, None)
    return {"pipeline_diagnostics": current}


def _truncate_preserving_same_site_crawl(blob: str, max_chars: int) -> str:
    """Prefer keeping the crawl block when clipping — it often holds full listing-page text."""
    if len(blob) <= max_chars or CRAWL_SECTION_MARKER not in blob:
        return blob[:max_chars]
    idx = blob.find(CRAWL_SECTION_MARKER)
    tail = blob[idx:]  # from marker onward (typically appended after DuckDuckGo text)
    if len(tail) >= max_chars:
        return tail[:max_chars]
    head_room = max_chars - len(tail)
    return blob[:head_room] + tail


def _dedupe_curator_resources(resources: list[Resource]) -> list[Resource]:
    """Drop repeats while allowing many gigs that share one calendar/listing URL."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[Resource] = []
    for r in resources:
        u = (r.url or "").strip().lower()
        if not u.startswith("http"):
            continue
        dp = parse_event_sort_date(r.date)
        dk = dp.isoformat() if dp else (r.date or "").strip().lower()[:32]
        title = " ".join((r.title or "").strip().lower().split())
        key = (u, dk, title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def _llm():
    """Return a zero-temperature chat client — OpenAI cloud or Ollama (OpenAI-compat)."""
    return build_chat_llm()


def node_plan(state: AgentState) -> AgentState:
    """Ask the LLM to produce search queries for the active topic.

    If the API key is missing or the LLM call fails, an error is logged and
    the pipeline continues with an empty query list (which produces no results
    and a log line). There are no fallback defaults (Task 8).
    """
    if not config.llm_inference_enabled():
        reason = (
            "Planner skipped — no LLM backend configured. "
            "Enable OPENAI_ENABLED with OPENAI_API_KEY, or OLLAMA_ENABLED with a reachable Ollama endpoint in .env."
        )
        logger.error("LLM unavailable for planner — check .env.")
        return {
            "queries": [],
            "run_log_message": "Planner skipped: no LLM backend configured (see .env).",
            **_merge_diagnostic(state, "planner", reason),
        }

    # Targeted venue searches: each run actively re-checks a random handful of
    # known venues ("What's on in <venue> in <location>, Australia"). These take
    # priority over planner queries so some generated queries are discarded in
    # their favour (see merge_queries).
    targeted = load_targeted_venue_queries(config.ACTIVE_TOPIC.db, config.PROMPT_GUIDES)
    if targeted:
        logger.info(
            "Planner: injecting %d targeted venue search(es): %s",
            len(targeted),
            "; ".join(targeted),
        )

    llm = build_planner_llm()
    recent = load_recent_planner_queries(
        config.ACTIVE_TOPIC.db,
        limit=config.PROMPT_GUIDES.planner_recent_queries_limit,
    )
    variation = build_planner_variation_block(config.PROMPT_GUIDES, recent_queries=recent)
    msg = HumanMessage(
        content=(
            config.SUBJECT.planner_user_message
            + planner_date_instruction(config.PROMPT_GUIDES)
            + f"\n\nProduce up to {config.MAX_SEARCH_QUERIES} distinct search queries."
            + "\n\n"
            + variation
        )
    )

    try:
        plan: PlanQueries = invoke_structured(
            llm,
            [SystemMessage(content=config.SUBJECT.planner_system_prompt), msg],
            PlanQueries,
        )
        planned = list(plan.queries or [])
        qs = merge_queries(targeted, planned, limit=config.MAX_SEARCH_QUERIES)
        if not qs:
            reason = (
                "Planner returned zero search queries — the LLM response was empty or "
                "did not include usable query strings."
            )
            logger.error("Planner returned no queries — check the LLM response.")
            return {
                "queries": [],
                **_merge_diagnostic(state, "planner", reason),
            }
        return {"queries": qs, **_merge_diagnostic(state, "planner", None)}
    except Exception as exc:
        # Planner LLM failed, but targeted venue queries can still drive the run.
        if targeted:
            qs = merge_queries(targeted, [], limit=config.MAX_SEARCH_QUERIES)
            logger.warning(
                "Plan step failed (%s) — continuing with %d targeted venue query(ies).",
                exc,
                len(qs),
            )
            return {"queries": qs, **_merge_diagnostic(state, "planner", None)}
        reason = format_step_error("Planner", exc)
        logger.exception("Plan step failed: %s", exc)
        return {
            "queries": [],
            "run_log_message": f"Plan step failed: {exc}",
            **_merge_diagnostic(state, "planner", reason),
        }


def node_search(state: AgentState) -> AgentState:
    """Run each planned query through DuckDuckGo and collect raw text."""
    queries = state.get("queries") or []
    if not queries:
        logger.warning("No queries available — search step skipped.")
        return {
            "raw_search_text": "",
            **_merge_diagnostic(state, "search", None),
        }
    logger.info(
        "Search step: querying DuckDuckGo with %s planned queries.", len(queries)
    )
    t0 = monotonic()
    text, search_note = run_searches(queries)
    elapsed = monotonic() - t0
    logger.info(
        "Search step finished in %.1f s (~%s chars combined snippets).",
        elapsed,
        f"{len(text):,}",
    )
    return {
        "raw_search_text": text,
        **_merge_diagnostic(state, "search", search_note),
    }


def node_crawl(state: AgentState) -> AgentState:
    """Append same-site HTML text after search so the curator can mine more gigs.

    Also stores the list of URLs that were actually fetched on
    ``state["crawled_urls"]`` so the per-run report (Task 11) can group them
    by host.
    """
    raw = state.get("raw_search_text") or ""
    if not config.CRAWL_ENABLED:
        reason = "Same-site crawl disabled — set CRAWL_ENABLED=true in .env to fetch listing pages after search."
        logger.info("Crawl step skipped (CRAWL_ENABLED=false).")
        return {"crawled_urls": [], **_merge_diagnostic(state, "crawl", reason)}
    try:
        from agent.source_store import pick_weighted_seed_url

        # Venue-first mining (Task 1): recognise known venues in the search
        # results, find their "What's On" pages, and mine them as the highest
        # priority seeds so big venues are exploited exhaustively.
        venue_seeds: list[str] = []
        try:
            from agent.venue_crawl import gather_venue_seed_urls

            venue_seeds = gather_venue_seed_urls(config.ACTIVE_TOPIC.db, raw)
            if venue_seeds:
                logger.info(
                    "Crawl step: %d venue 'What's On' page(s) prioritised: %s",
                    len(venue_seeds),
                    ", ".join(venue_seeds),
                )
        except Exception as venue_exc:
            logger.warning("Venue mining skipped (continuing): %s", venue_exc)
            venue_seeds = []

        memory_seed = pick_weighted_seed_url(config.ACTIVE_TOPIC.db)
        # Venue seeds first (highest priority), then the weighted memory seed.
        extra_seed_list = list(venue_seeds)
        if memory_seed:
            extra_seed_list.append(memory_seed)
            logger.info("Crawl step: using remembered URL as extra seed: %s", memory_seed)
        logger.info(
            "Crawl step starting (runs after DuckDuckGo text is collected; downloads can take several minutes)."
        )
        t0 = monotonic()
        extra, fetched_urls, crawl_note = deep_search_supplement(
            raw,
            extra_seeds=extra_seed_list,
        )
        elapsed = monotonic() - t0
        logger.info(
            "Crawl step finished in %.1f s (~%s extra chars for curator).",
            elapsed,
            f"{len(extra):,}",
        )
        out: AgentState = {
            "crawled_urls": list(fetched_urls),
            "memory_seed": memory_seed or "",
            **_merge_diagnostic(state, "crawl", crawl_note if not fetched_urls else None),
        }
        if extra.strip():
            out["raw_search_text"] = raw + "\n\n" + extra
        return out
    except Exception as exc:
        reason = format_step_error("Crawl", exc)
        logger.warning("Site crawl step failed (continuing with search only): %s", exc)
        return {
            "crawled_urls": [],
            **_merge_diagnostic(state, "crawl", reason),
        }


def node_normalize(state: AgentState) -> AgentState:
    """Ask the LLM to parse raw search text into structured Resource records."""
    raw = state.get("raw_search_text") or ""

    if not config.llm_inference_enabled():
        reason = (
            "Curator skipped — no LLM backend configured. "
            "Enable OPENAI_ENABLED with OPENAI_API_KEY, or OLLAMA_ENABLED with a reachable Ollama endpoint in .env."
        )
        logger.error("LLM unavailable for curator — check .env.")
        return {
            "resources": [],
            "run_log_message": "Normalize skipped: no LLM backend configured (see .env).",
            **_merge_diagnostic(state, "normalize", reason),
        }

    llm = _llm()
    msg = HumanMessage(
        content=(
            f"{curator_date_instruction(config.PROMPT_GUIDES)}\n\n"
            "Here are web search results plus any same-site crawl excerpts below. "
            "Extract the curated resource list.\n\n"
            f"{_truncate_preserving_same_site_crawl(raw, config.CURATOR_INPUT_MAX_CHARS)}"
        )
    )
    try:
        body_len = len(msg.content or "")
        logger.info(
            "Curator (normalize) step: invoking model %s (%s chars after crawl + truncate). "
            "Large prompts on local Ollama can take many minutes — no HTTP spam until this returns.",
            config.active_llm_model_label(),
            f"{body_len:,}",
        )
        t0 = monotonic()
        out: ResourceListPayload = invoke_structured(
            llm,
            [SystemMessage(content=config.SUBJECT.curator_system_prompt), msg],
            ResourceListPayload,
        )
        curator_s = monotonic() - t0
        unique = _dedupe_curator_resources(list(out.resources or []))
        # Keep every dated future event (no upper bound); sort soonest first.
        future = filter_future_events(unique)
        ordered = sort_resources_by_event_date_asc(future)
        logger.info(
            "Curator finished in %.1f s producing %s future-dated resources.",
            curator_s,
            len(ordered),
        )
        return {
            "resources": [r.model_dump() for r in ordered],
            "run_log_message": "",
            **_merge_diagnostic(state, "normalize", None),
        }
    except Exception as exc:
        reason = format_step_error("Curator", exc)
        logger.exception("Normalise step failed: %s", exc)
        return {
            "resources": [],
            "run_log_message": f"Normalise error: {exc}",
            **_merge_diagnostic(state, "normalize", reason),
        }


def node_enrich(state: AgentState) -> AgentState:
    """Fetch Open Graph thumbnail images for each resource where missing."""
    raw_res = state.get("resources") or []
    resources = [resource_from_dict(d) for d in raw_res]
    need = sum(1 for r in resources if not r.thumbnail_url)
    if need:
        logger.info(
            "Enrich step: fetching og:image for %s/%s event pages (one HTTP GET each ~12s timeout).",
            need,
            len(resources),
        )
    t0 = monotonic()
    enriched = enrich_thumbnails(resources)
    if need:
        logger.info(
            "Enrich step finished in %.1f s.", monotonic() - t0
        )
    return {"resources": [r.model_dump() for r in enriched]}


def node_fingerprint(state: AgentState) -> AgentState:
    """Hash the current resource list and compare to the saved snapshot."""
    resources = [resource_from_dict(d) for d in (state.get("resources") or [])]
    fp, unchanged = fingerprint_changed(resources, config.SNAPSHOT_PATH)
    return {
        "fingerprint": fp,
        "fingerprint_unchanged": unchanged,
        "skip_doc_rewrite": unchanged,
    }


def build_run_log_message(state: AgentState) -> str:
    """Build a human-readable one-line summary for the run log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    unchanged = state.get("fingerprint_unchanged")
    n = len(state.get("resources") or [])
    extra = state.get("run_log_message") or ""
    dry = state.get("dry_run", False)
    topic = config.SUBJECT.topic

    if extra:
        return f"{ts} - {extra}"
    if dry:
        return f"{ts} - dry-run: {n} results curated for '{topic}'; no files written."
    if unchanged:
        return f"{ts} - searched '{topic}'; no changes since last snapshot ({n} results tracked)."
    return f"{ts} - searched '{topic}'; {n} events merged into MongoDB."


def node_local_output(state: AgentState) -> AgentState:
    """Write the per-run report and sync events to MongoDB.

    Steps (Task 11):
    1. Build resources + queries + crawled URL list from state.
    2. Skip all file writes on dry-run.
    3. Merge new resources into MongoDB (events + images collections).
    4. Save the snapshot fingerprint.
    5. Save a structured run report to MongoDB (``reports`` collection).
    """
    from agent.local_output import active_db_name, write_output
    from agent.report_store import save_run_report

    msg = build_run_log_message(state)
    dry = state.get("dry_run", False)
    if dry:
        return {"run_log_message": msg}

    resources = [resource_from_dict(d) for d in (state.get("resources") or [])]
    queries = list(state.get("queries") or [])
    crawled_urls = list(state.get("crawled_urls") or [])
    fp = state.get("fingerprint") or ""

    try:
        logger.info(
            "Output step: merging %s curated resources into MongoDB and writing run report.",
            len(resources),
        )
        merge_stats = write_output(resources)
        save_snapshot(config.SNAPSHOT_PATH, fp, resources)

        try:
            from agent.source_store import record_url_outcomes

            record_url_outcomes(
                active_db_name(),
                merge_stats.url_outcomes,
                distinct_counts=merge_stats.url_distinct_event_counts,
            )
        except Exception as source_exc:
            logger.warning("Fruitful URL memory update failed (continuing): %s", source_exc)

        try:
            memory_seed = str(state.get("memory_seed") or "").strip() or None
            report_id = save_run_report(
                active_db_name(),
                queries=queries,
                crawled_urls=crawled_urls,
                merge_stats=merge_stats,
                diagnostics=dict(state.get("pipeline_diagnostics") or {}),
                memory_seed=memory_seed,
            )
            logger.info("Run report saved (id=%s)", report_id)
        except Exception as report_exc:
            logger.warning("Run report save failed (continuing): %s", report_exc)

    except Exception as exc:
        logger.exception("Local output failed: %s", exc)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "run_log_message": (
                f"{ts} - save failed during event store write ({len(resources)} results): {exc}"
            ),
        }

    return {"run_log_message": msg}
