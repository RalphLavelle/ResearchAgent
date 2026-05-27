"""Configure LangChain ChatOpenAI for cloud OpenAI, local Ollama, or Ollama Cloud.

All three backends expose an OpenAI-compatible ``/v1/chat/completions``
endpoint, so ``ChatOpenAI`` + ``base_url`` is the LangGraph-compatible path.

Ollama Cloud (https://docs.ollama.com/cloud) lets you run large models without
a local GPU. Cloud models are identified by the ``:cloud`` model-name suffix
or a non-localhost ``OLLAMA_BASE_URL`` (e.g. ``https://ollama.com/v1``)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from langchain_openai import ChatOpenAI

from agent import config

logger = logging.getLogger(__name__)


def build_chat_llm() -> ChatOpenAI:
    """Zero-temperature client for whichever backend ``config`` selects."""
    if config.OPENAI_ENABLED:
        return ChatOpenAI(
            model=config.OPENAI_MODEL,
            temperature=0,
            api_key=config.OPENAI_API_KEY.strip() or None,
        )

    if config.OLLAMA_ENABLED:
        kwargs: dict[str, Any] = {
            "model": config.OLLAMA_MODEL,
            "temperature": 0,
            "api_key": config.OLLAMA_API_KEY,
            "base_url": config.OLLAMA_BASE_URL,
        }
        extra = _ollama_extra_body_from_config()
        if extra:
            kwargs["extra_body"] = extra
        return ChatOpenAI(**kwargs)

    raise RuntimeError(
        "No LLM backend enabled. Set OPENAI_ENABLED=true or OLLAMA_ENABLED=true in .env."
    )


def build_planner_llm() -> ChatOpenAI:
    """Chat client for query planning — higher temperature for search diversity."""
    temperature = config.PLANNER_TEMPERATURE
    if config.OPENAI_ENABLED:
        return ChatOpenAI(
            model=config.OPENAI_MODEL,
            temperature=temperature,
            api_key=config.OPENAI_API_KEY.strip() or None,
        )

    if config.OLLAMA_ENABLED:
        kwargs: dict[str, Any] = {
            "model": config.OLLAMA_MODEL,
            "temperature": temperature,
            "api_key": config.OLLAMA_API_KEY,
            "base_url": config.OLLAMA_BASE_URL,
        }
        extra = _ollama_extra_body_from_config()
        if extra:
            kwargs["extra_body"] = extra
        return ChatOpenAI(**kwargs)

    raise RuntimeError(
        "No LLM backend enabled. Set OPENAI_ENABLED=true or OLLAMA_ENABLED=true in .env."
    )


def _ollama_extra_body_from_config() -> dict[str, Any] | None:
    """Optional JSON merged into the request body.

    The ``enable_thinking`` hint is a local-Ollama template parameter (used
    by Qwen models). It's skipped for cloud models because they don't support
    local template overrides.
    """
    raw = (config.OLLAMA_EXTRA_BODY_JSON or "").strip()
    base: dict[str, Any] = {}
    # Thinking-template params only apply to local Ollama, not cloud.
    if config.OLLAMA_DISABLE_THINKING_TEMPLATE and not config.is_ollama_cloud():
        base = dict(config.OLLAMA_THINKING_OFF_EXTRA_BODY)

    if not raw:
        return base or None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("OLLAMA_EXTRA_BODY_JSON is not valid JSON — ignoring (%s).", exc)
        return base or None
    if not isinstance(parsed, dict):
        logger.warning("OLLAMA_EXTRA_BODY_JSON must decode to an object — ignoring.")
        return base or None
    merged = {**base, **parsed}
    return merged


def verify_llm_at_startup() -> bool:
    """Log configuration problems before the graph runs.

    Returns **False** when the chosen backend clearly cannot serve requests —
    callers should abort with a non-zero exit status.
    """
    if not config.OPENAI_ENABLED and not config.OLLAMA_ENABLED:
        logger.error(
            "No LLM backend enabled. "
            "Set OPENAI_ENABLED=true or OLLAMA_ENABLED=true in .env."
        )
        return False

    if config.OPENAI_ENABLED:
        if not (config.OPENAI_API_KEY or "").strip():
            logger.error(
                "OPENAI_ENABLED is true but OPENAI_API_KEY is empty."
            )
            return False
        return True

    # OLLAMA_ENABLED is true from here.
    if config.is_ollama_cloud():
        return _verify_ollama_cloud()
    return _verify_ollama_local()


def _verify_ollama_cloud() -> bool:
    """Check that the Ollama Cloud backend appears reachable."""
    base = config.OLLAMA_BASE_URL.rstrip("/")
    key = (config.OLLAMA_API_KEY or "").strip()

    if not key or key == "ollama":
        logger.error(
            "Cloud model %r requires an Ollama API key. "
            "Create one at https://ollama.com/settings/keys and "
            "set OLLAMA_API_KEY in .env.",
            config.OLLAMA_MODEL,
        )
        return False

    url = f"{base}/models"
    headers = {"Authorization": f"Bearer {key}"}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Could not verify Ollama endpoint at %s (%s). "
            "Continuing anyway — the model request itself may still succeed.",
            url,
            exc,
        )
        # Don't hard-fail: cloud /v1/models may return a different shape
        # or require a POST. The real model call will surface auth errors.

    logger.info(
        "Using Ollama Cloud (model=%s, base_url=%s).",
        config.OLLAMA_MODEL,
        base,
    )
    return True


def _verify_ollama_local() -> bool:
    """Check that a local Ollama server is running and reachable."""
    base = config.OLLAMA_BASE_URL.rstrip("/")
    url = f"{base}/models"
    headers: dict[str, str] = {}
    key = (config.OLLAMA_API_KEY or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
    except Exception as exc:
        logger.error(
            "Local Ollama server does not appear to be reachable at %s (%s). "
            "Ensure Ollama is running (`ollama serve`), the OpenAI-compat path "
            "ends with `/v1`, and model %r is pulled — see README.",
            url,
            exc,
            config.OLLAMA_MODEL,
        )
        return False

    logger.info(
        "Using Ollama at %s (model=%s).",
        base,
        config.OLLAMA_MODEL,
    )
    return True
