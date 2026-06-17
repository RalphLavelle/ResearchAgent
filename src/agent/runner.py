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


def execute_run_once(*, dry_run: bool = False) -> AgentState:
    """
    Run one research + save pass — same behaviour as ``python -m agent run-once``.

    Raises ``LLMNotReadyError`` when the LLM backend is not configured or reachable.
    """
    prepare_run_environment()
    if not verify_llm_at_startup():
        raise LLMNotReadyError(
            "LLM backend is not reachable or misconfigured — fix .env then retry."
        )
    return run_once(dry_run=dry_run)
