"""DuckDuckGo search via LangChain DuckDuckGoSearchRun (structured via api_wrapper)."""

from __future__ import annotations

import logging
import time

from langchain_community.tools import DuckDuckGoSearchRun

from agent import config

logger = logging.getLogger(__name__)


def run_searches(queries: list[str]) -> str:
    """
    Run each query with spacing; return one big text block for the LLM.

    Uses DuckDuckGoSearchRun's api_wrapper.results() so we keep title/link/snippet.
    """
    tool = DuckDuckGoSearchRun()
    wrapper = tool.api_wrapper
    wrapper.max_results = config.MAX_DDG_RESULTS_PER_QUERY

    lines: list[str] = []
    for i, q in enumerate(queries):
        if i > 0:
            time.sleep(config.SEARCH_DELAY_SEC)
        try:
            rows = wrapper.results(
                q, max_results=config.MAX_DDG_RESULTS_PER_QUERY, source="text"
            )
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for %r: %s", q, exc)
            lines.append(f"Query: {q}\nError: {exc}\n---\n")
            continue
        lines.append(f"## Query: {q}\n")
        for r in rows:
            title = r.get("title", "")
            link = r.get("link", "")
            snippet = r.get("snippet", "")
            lines.append(
                f"title: {title}\nsnippet: {snippet}\nlink: {link}\n---\n"
            )
    return "\n".join(lines)
