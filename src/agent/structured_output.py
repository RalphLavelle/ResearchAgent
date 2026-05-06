"""Structured-output helper that works with any OpenAI-compatible backend.

Cloud OpenAI and local Ollama support ``response_format`` / tool-calling
natively, so ``with_structured_output()`` works out of the box.  Ollama Cloud
does **not** support those features (as of mid-2026), so this module provides
a prompt-based fallback: it embeds the JSON schema in the prompt, asks the
model to reply with **only** valid JSON, and then parses + validates the
response with Pydantic.

Usage in graph nodes / semantic dedupe::

    result = invoke_structured(llm, messages, PlanQueries)

The function picks the right strategy automatically based on
``config.is_ollama_cloud()``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel

from agent import config

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def invoke_structured(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    output_model: type[T],
) -> T:
    """Invoke *llm* and return a validated *output_model* instance.

    * **Native path** (OpenAI / local Ollama): uses ``with_structured_output``
      which sends ``response_format`` or tool-call constraints to the API.
    * **Fallback path** (Ollama Cloud): injects the JSON schema into the
      system prompt, calls the model normally, then extracts and parses JSON
      from the plain-text response.
    """
    if _should_use_fallback():
        return _invoke_with_prompt_fallback(llm, messages, output_model)
    return _invoke_native(llm, messages, output_model)


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

def _should_use_fallback() -> bool:
    """True when the backend does not support native structured output."""
    return config.OLLAMA_ENABLED and config.is_ollama_cloud()


def _invoke_native(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    output_model: type[T],
) -> T:
    """Standard LangChain path — relies on API-level schema enforcement."""
    structured_llm = llm.with_structured_output(output_model)
    return structured_llm.invoke(messages)


def _invoke_with_prompt_fallback(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    output_model: type[T],
) -> T:
    """Embed the schema in the prompt, get raw text, extract JSON, validate."""
    schema_json = json.dumps(
        output_model.model_json_schema(), indent=2, ensure_ascii=False
    )
    schema_instruction = (
        "\n\n--- RESPONSE FORMAT ---\n"
        "You MUST respond with ONLY valid JSON that conforms to this schema. "
        "Do NOT include any explanation, markdown, or text outside the JSON object.\n\n"
        f"```json\n{schema_json}\n```\n"
        "--- END RESPONSE FORMAT ---"
    )

    # Append the schema instruction to the first SystemMessage, or create one.
    patched = _append_to_system_message(messages, schema_instruction)

    response = llm.invoke(patched)
    raw_text = response.content or ""

    logger.debug("Cloud model raw response (first 500 chars): %.500s", raw_text)

    parsed_json = _extract_json(raw_text)
    return output_model.model_validate(parsed_json)


# ---------------------------------------------------------------------------
# Message patching
# ---------------------------------------------------------------------------

def _append_to_system_message(
    messages: list[BaseMessage],
    extra: str,
) -> list[BaseMessage]:
    """Return a copy of *messages* with *extra* appended to the system prompt."""
    patched: list[BaseMessage] = []
    found_system = False
    for msg in messages:
        if isinstance(msg, SystemMessage) and not found_system:
            patched.append(SystemMessage(content=(msg.content or "") + extra))
            found_system = True
        else:
            patched.append(msg)
    if not found_system:
        patched.insert(0, SystemMessage(content=extra))
    return patched


# ---------------------------------------------------------------------------
# JSON extraction from free-form text
# ---------------------------------------------------------------------------

# Matches ```json ... ``` or ``` ... ``` code fences
_CODE_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)

# Matches the outermost { ... } or [ ... ]
_BARE_JSON_RE = re.compile(
    r"(\{[\s\S]*\}|\[[\s\S]*\])",
)


def _extract_json(text: str) -> dict | list:
    """Pull a JSON object or array from *text*, trying several strategies.

    Order of attempts:
    1. Text inside a markdown code fence (```json ... ``` or ``` ... ```).
    2. The outermost braces/brackets in the raw text.
    3. The entire text as-is (maybe it IS pure JSON).
    """
    # Strategy 1: code fence
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Strategy 2: outermost braces / brackets
    bare_match = _BARE_JSON_RE.search(text)
    if bare_match:
        candidate = bare_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Strategy 3: entire text
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    raise ValueError(
        f"Could not extract valid JSON from the model response. "
        f"First 300 chars: {text[:300]!r}"
    )
