"""DuckDuckGo search via LangChain DuckDuckGoSearchRun (structured via api_wrapper)."""

from __future__ import annotations

import logging
import time

from langchain_community.tools import DuckDuckGoSearchRun

from agent import config

logger = logging.getLogger(__name__)


def run_searches(queries: list[str]) -> tuple[str, str | None]:
    """
    Run each query with spacing; return combined text plus an optional failure note.

    Uses DuckDuckGoSearchRun's api_wrapper.results() so we keep title/link/snippet.
    """
    if not queries:
        return "", "Search skipped — planner produced no queries."

    tool = DuckDuckGoSearchRun()
    wrapper = tool.api_wrapper
    wrapper.max_results = config.MAX_DDG_RESULTS_PER_QUERY

    lines: list[str] = []
    failed_queries = 0
    empty_queries = 0
    for i, q in enumerate(queries):
        if i > 0:
            time.sleep(config.SEARCH_DELAY_SEC)
        try:
            rows = wrapper.results(
                q, max_results=config.MAX_DDG_RESULTS_PER_QUERY, source="text"
            )
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for %r: %s", q, exc)
            failed_queries += 1
            lines.append(f"Query: {q}\nError: {exc}\n---\n")
            continue
        if not rows:
            empty_queries += 1
            lines.append(f"## Query: {q}\n(no DuckDuckGo results)\n---\n")
            continue
        lines.append(f"## Query: {q}\n")
        for r in rows:
            title = r.get("title", "")
            link = r.get("link", "")
            snippet = r.get("snippet", "")
            lines.append(
                f"title: {title}\nsnippet: {snippet}\nlink: {link}\n---\n"
            )

    text = "\n".join(lines)
    if failed_queries == len(queries):
        return text, (
            f"Search failed — all {len(queries)} DuckDuckGo queries raised errors "
            "(network issue or DuckDuckGo rate limiting)."
        )
    if failed_queries and failed_queries + empty_queries == len(queries):
        return text, (
            f"Search produced no usable snippets — {failed_queries} query error(s) and "
            f"{empty_queries} empty result set(s)."
        )
    if empty_queries == len(queries):
        return text, (
            f"Search returned no DuckDuckGo hits for all {len(queries)} planned queries."
        )
    if failed_queries:
        return text, (
            f"Search partial — {failed_queries} of {len(queries)} DuckDuckGo queries failed; "
            "remaining queries may have returned fewer snippets."
        )
    if empty_queries:
        return text, (
            f"Search partial — {empty_queries} of {len(queries)} DuckDuckGo queries "
            "returned zero results."
        )
    if not text.strip():
        return "", "Search finished but produced no text for the curator."
    return text, None
