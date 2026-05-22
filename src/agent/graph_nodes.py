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

from agent import config, notion_output
from agent.enrich import enrich_thumbnails
from agent.event_window import (
    curator_date_instruction,
    filter_events_in_upcoming_window,
    parse_event_sort_date,
    planner_date_instruction,
    sort_resources_by_event_date_asc,
)
from agent.llm_factory import build_chat_llm
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
        logger.error(
            "LLM unavailable for planner — check .env: "
            "OPENAI_ENABLED + OPENAI_API_KEY, or OLLAMA_ENABLED + Ollama config."
        )
        return {
            "queries": [],
            "run_log_message": "Planner skipped: no LLM backend configured (see .env).",
        }

    llm = _llm()
    msg = HumanMessage(
        content=(
            config.SUBJECT.planner_user_message
            + planner_date_instruction()
            + f"\n\nProduce up to {config.MAX_SEARCH_QUERIES} distinct search queries."
        )
    )

    try:
        plan: PlanQueries = invoke_structured(
            llm,
            [SystemMessage(content=config.SUBJECT.planner_system_prompt), msg],
            PlanQueries,
        )
        qs = (plan.queries or [])[: config.MAX_SEARCH_QUERIES]
        if not qs:
            logger.error("Planner returned no queries — check the LLM response.")
        return {"queries": qs}
    except Exception as exc:
        logger.exception("Plan step failed: %s", exc)
        return {
            "queries": [],
            "run_log_message": f"Plan step failed: {exc}",
        }


def node_search(state: AgentState) -> AgentState:
    """Run each planned query through DuckDuckGo and collect raw text."""
    queries = state.get("queries") or []
    if not queries:
        logger.warning("No queries available — search step skipped.")
        return {"raw_search_text": ""}
    logger.info(
        "Search step: querying DuckDuckGo with %s planned queries.", len(queries)
    )
    t0 = monotonic()
    text = run_searches(queries)
    elapsed = monotonic() - t0
    logger.info(
        "Search step finished in %.1f s (~%s chars combined snippets).",
        elapsed,
        f"{len(text):,}",
    )
    return {"raw_search_text": text}


def node_crawl(state: AgentState) -> AgentState:
    """Append same-site HTML text after search so the curator can mine more gigs.

    Also stores the list of URLs that were actually fetched on
    ``state["crawled_urls"]`` so the per-run report (Task 11) can group them
    by host.
    """
    raw = state.get("raw_search_text") or ""
    if not config.CRAWL_ENABLED:
        logger.info("Crawl step skipped (CRAWL_ENABLED=false).")
        return {"crawled_urls": []}
    try:
        logger.info(
            "Crawl step starting (runs after DuckDuckGo text is collected; downloads can take several minutes)."
        )
        t0 = monotonic()
        extra, fetched_urls = deep_search_supplement(raw)
        elapsed = monotonic() - t0
        logger.info(
            "Crawl step finished in %.1f s (~%s extra chars for curator).",
            elapsed,
            f"{len(extra):,}",
        )
        out: AgentState = {"crawled_urls": list(fetched_urls)}
        if extra.strip():
            out["raw_search_text"] = raw + "\n\n" + extra
        return out
    except Exception as exc:
        logger.warning("Site crawl step failed (continuing with search only): %s", exc)
        return {"crawled_urls": []}


def node_normalize(state: AgentState) -> AgentState:
    """Ask the LLM to parse raw search text into structured Resource records."""
    raw = state.get("raw_search_text") or ""

    if not config.llm_inference_enabled():
        logger.error(
            "LLM unavailable for curator — check .env: "
            "OPENAI_ENABLED + OPENAI_API_KEY, or OLLAMA_ENABLED + Ollama config."
        )
        return {
            "resources": [],
            "run_log_message": "Normalize skipped: no LLM backend configured (see .env).",
        }

    llm = _llm()
    msg = HumanMessage(
        content=(
            f"{curator_date_instruction()}\n\n"
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
        # Keep only dated events in the configured horizon; sort soonest first.
        windowed = filter_events_in_upcoming_window(unique)
        ordered = sort_resources_by_event_date_asc(windowed)
        logger.info(
            "Curator finished in %.1f s producing %s resources after date window.",
            curator_s,
            len(ordered),
        )
        return {
            "resources": [r.model_dump() for r in ordered],
            "run_log_message": "",
        }
    except Exception as exc:
        logger.exception("Normalise step failed: %s", exc)
        return {
            "resources": [],
            "run_log_message": f"Normalise error: {exc}",
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
    return f"{ts} - searched '{topic}'; spreadsheet updated with {n} results."


def node_local_output(state: AgentState) -> AgentState:
    """Write the per-run report and sync the spreadsheet/JSON outputs.

    Steps (Task 11):
    1. Build resources + queries + crawled URL list from state.
    2. Skip all file writes on dry-run.
    3. Merge new resources into the spreadsheet and refresh ``events.json``.
    4. Save the snapshot fingerprint.
    5. Write a fresh ``Run_<AEST timestamp>.md`` markdown report capturing the
       three LLM-driven steps in detail (Searches, Search and crawl, Normalize).
    6. Optionally push the full spreadsheet to Notion.
    """
    from agent.local_output import load_spreadsheet_resources, output_directory, write_output
    from agent.run_report import write_run_report

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
            "Output step: merging %s curated resources into spreadsheet, events.json, run report.",
            len(resources),
        )
        merge_stats = write_output(resources)
        save_snapshot(config.SNAPSHOT_PATH, fp, resources)

        try:
            report_path = write_run_report(
                output_directory(),
                queries=queries,
                crawled_urls=crawled_urls,
                resources=resources,
                merge_stats=merge_stats,
            )
            logger.info("Run report saved to %s", report_path.name)
        except Exception as report_exc:
            logger.warning("Run report write failed (continuing): %s", report_exc)

        all_resources = load_spreadsheet_resources()

        from agent.snapshot import canonical_fingerprint
        spreadsheet_fp = canonical_fingerprint(all_resources)

        if (
            config.notion_sync_configured()
            and notion_output.notion_sync_needed(spreadsheet_fp, config.NOTION_SYNC_STATE_PATH)
        ):
            try:
                notion_output.sync_research_page(
                    token=config.NOTION_INTEGRATION_TOKEN,
                    page_id_raw=config.NOTION_RESEARCH_PAGE_ID,
                    resources=all_resources,
                    api_version=config.NOTION_API_VERSION,
                )
                notion_output.mark_notion_synced(spreadsheet_fp, config.NOTION_SYNC_STATE_PATH)
            except Exception as notion_exc:
                logger.exception("Notion sync failed: %s", notion_exc)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                fail_line = f"{ts} - Notion sync failed: {notion_exc}"
                return {"run_log_message": f"{msg} | {fail_line}"}

    except Exception as exc:
        logger.exception("Local output failed: %s", exc)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "run_log_message": (
                f"{ts} - save failed during spreadsheet write ({len(resources)} results): {exc}"
            ),
        }

    return {"run_log_message": msg}
