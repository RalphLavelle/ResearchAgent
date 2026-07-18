"""Shared entry for a full research pipeline pass (CLI and HTTP admin)."""

from __future__ import annotations

import logging

from agent import config
from agent.image_cache import dedupe_images_for_all_topics
from agent.llm_factory import verify_llm_at_startup
from agent.models import AgentState
from agent.workflow import run_once

logger = logging.getLogger(__name__)


class LLMNotReadyError(RuntimeError):
    """Raised when the configured LLM backend is missing or unreachable."""


class LLMInvocationError(RuntimeError):
    """Raised when the first (or any) LLM call fails mid-run — abort the pipeline.

    Further steps (search/crawl/curator) need the same model, so continuing after
    a planner 404 / auth / timeout would only waste time and repeat the failure.
    """


def prepare_run_environment() -> None:
    """Housekeeping shared by ``run-once`` and ``serve`` before a pipeline pass."""
    try:
        removed = dedupe_images_for_all_topics(data_base=config.DATA_BASE_DIR)
        if removed:
            logger.info(
                "Removed %s duplicate poster file(s) across topic image caches.",
                removed,
            )
    except OSError as exc:
        logger.warning("Image dedupe migration skipped: %s", exc)


def _unwrap_llm_invocation_error(exc: BaseException) -> LLMInvocationError | None:
    """Walk cause/context chain for an ``LLMInvocationError`` (LangGraph may wrap it)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, LLMInvocationError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def execute_run_once(
    *,
    dry_run: bool = False,
    targeted_query: str | None = None,
) -> AgentState:
    """
    Run one research + save pass — same behaviour as ``python -m agent run-once``.

    When *targeted_query* is provided, only that DuckDuckGo phrase is searched
    (admin targeted search); the rest of the pipeline is unchanged.

    Raises ``LLMNotReadyError`` when the LLM backend is not configured or reachable.
    Raises ``LLMInvocationError`` when the first LLM call fails (e.g. model not found).
    """
    prepare_run_environment()
    if not verify_llm_at_startup():
        raise LLMNotReadyError(
            "LLM backend is not reachable or misconfigured — fix .env then retry."
        )
    try:
        return run_once(dry_run=dry_run, targeted_query=targeted_query)
    except Exception as exc:
        # Already logged in the graph node that hit the bad model / API response.
        llm_exc = _unwrap_llm_invocation_error(exc)
        if llm_exc is not None:
            raise llm_exc from exc
        raise
