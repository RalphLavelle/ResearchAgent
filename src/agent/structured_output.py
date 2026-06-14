"""Structured-output helper that works with any OpenAI-compatible backend.

Cloud OpenAI and local Ollama support ``response_format`` / tool-calling
natively, so ``with_structured_output()`` works out of the box.  Ollama Cloud
does **not** support those features (as of mid-2026), so this module provides
a prompt-based fallback: it shows a concrete JSON example, asks the model to
reply with **only** valid JSON, then parses + validates the response with
Pydantic.

Usage in graph nodes / semantic dedupe::

    result = invoke_structured(llm, messages, PlanQueries)

The function picks the right strategy automatically based on
``config.is_ollama_cloud()``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel

from agent import config

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Numbered-list lines the planner sometimes returns instead of JSON.
_NUMBERED_QUERY_LINE = re.compile(r"^\s*\d+[\.)]\s*(.+)$", re.MULTILINE)

# Concrete examples — clearer for cloud models than a raw JSON Schema blob.
_FALLBACK_EXAMPLES: dict[str, dict[str, Any]] = {
    "PlanQueries": {
        "queries": [
            "site:facebook.com/events Gold Coast live music June 2026",
            "who is playing at breweries in Burleigh Heads this winter?",
        ]
    },
    "ResourceListPayload": {
        "resources": [
            {
                "title": "The Beths @ The Tivoli, Brisbane",
                "url": "https://example.com/gig",
                "date": "2026-07-15",
                "summary": "Indie rock headline show.",
                "thumbnail_url": None,
            }
        ]
    },
    "ExclusionPruneResult": {"excluded_event_ids": ["uuid-one", "uuid-two"]},
    "EventTaggingResult": {
        "assignments": [{"event_id": "uuid-one", "tags": ["jazz", "free"]}]
    },
    "SemanticDedupeClusters": {
        "duplicate_groups": [{"event_ids": ["uuid-a", "uuid-b"]}]
    },
}


def invoke_structured(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    output_model: type[T],
) -> T:
    """Invoke *llm* and return a validated *output_model* instance.

    * **Native path** (OpenAI / local Ollama): uses ``with_structured_output``
      which sends ``response_format`` or tool-call constraints to the API.
    * **Fallback path** (Ollama Cloud): injects a JSON example into the
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


def _fallback_example(output_model: type[BaseModel]) -> dict[str, Any]:
    """Return a small example payload for the cloud JSON prompt."""
    return dict(_FALLBACK_EXAMPLES.get(output_model.__name__, {}))


def _fallback_format_instruction(output_model: type[BaseModel]) -> str:
    """Prompt fragment asking for JSON matching *output_model*."""
    example = _fallback_example(output_model)
    if not example:
        schema_json = json.dumps(
            output_model.model_json_schema(), indent=2, ensure_ascii=False
        )
        body = f"```json\n{schema_json}\n```"
    else:
        body = (
            "Return a single JSON object with the same top-level keys as this example. "
            "Put your real values in those keys — do **not** echo JSON Schema metadata "
            "such as ``description``, ``properties``, ``type``, or ``items``.\n\n"
            f"```json\n{json.dumps(example, indent=2, ensure_ascii=False)}\n```"
        )
    return (
        "\n\n--- RESPONSE FORMAT ---\n"
        "You MUST respond with ONLY valid JSON. "
        "Do NOT include any explanation, markdown fences outside the JSON, or numbered lists.\n\n"
        f"{body}\n"
        "--- END RESPONSE FORMAT ---"
    )


def _invoke_with_prompt_fallback(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    output_model: type[T],
) -> T:
    """Embed an example in the prompt, get raw text, extract JSON, validate."""
    schema_instruction = _fallback_format_instruction(output_model)
    patched = _append_to_system_message(messages, schema_instruction)

    response = llm.invoke(patched)
    raw_text = response.content or ""

    logger.debug("Cloud model raw response (first 500 chars): %.500s", raw_text)

    parsed_json = _extract_json(raw_text, output_model=output_model)
    coerced = _coerce_schema_echo(parsed_json, output_model)
    result = output_model.model_validate(coerced)

    if _looks_empty(result, output_model):
        recovered = _recover_from_plain_text(raw_text, output_model)
        if recovered is not None:
            logger.warning(
                "Cloud model JSON parsed empty %s — recovered from plain-text fallback.",
                output_model.__name__,
            )
            return recovered

    return result


def _looks_empty(result: BaseModel, output_model: type[BaseModel]) -> bool:
    """True when validation succeeded but list fields are unexpectedly empty."""
    if output_model.__name__ == "PlanQueries":
        return not (getattr(result, "queries", None) or [])
    if output_model.__name__ == "ResourceListPayload":
        return not (getattr(result, "resources", None) or [])
    return False


def _recover_from_plain_text(
    text: str,
    output_model: type[T],
) -> T | None:
    """Last-resort parsers when cloud models ignore the JSON instruction."""
    if output_model.__name__ == "PlanQueries":
        queries = _parse_numbered_queries(text)
        if queries:
            return output_model.model_validate({"queries": queries})
    return None


def _coerce_schema_echo(
    parsed: dict | list,
    output_model: type[BaseModel],
) -> dict | list:
    """Fix cloud models that nest data under ``properties`` like a JSON Schema."""
    if not isinstance(parsed, dict):
        return parsed

    props = parsed.get("properties")
    if not isinstance(props, dict):
        return parsed

    coerced = dict(parsed)
    for field_name in output_model.model_fields:
        if field_name in coerced and coerced[field_name] not in (None, [], {}):
            continue
        candidate = props.get(field_name)
        if candidate is None:
            continue
        # Model echoed schema metadata instead of data.
        if isinstance(candidate, dict) and candidate.get("type") in {
            "array",
            "object",
            "string",
        }:
            continue
        coerced[field_name] = candidate

    return coerced


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

_CODE_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)

_BARE_JSON_RE = re.compile(
    r"(\{[\s\S]*\}|\[[\s\S]*\])",
)


def _parse_numbered_queries(text: str) -> list[str]:
    """Extract query strings from ``1. ...`` / ``2) ...`` planner output."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _NUMBERED_QUERY_LINE.finditer(text or ""):
        query = match.group(1).strip()
        if len(query) >= 2 and query[0] == query[-1] and query[0] in "\"'":
            query = query[1:-1].strip()
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        out.append(query)
    return out


def _try_load_json(candidate: str) -> dict | list | None:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _score_parsed_candidate(
    parsed: dict | list,
    output_model: type[BaseModel] | None,
) -> int:
    """Prefer payloads that look like real data, not schema echoes."""
    if not isinstance(parsed, dict) or output_model is None:
        return 1
    score = 0
    for field_name in output_model.model_fields:
        value = parsed.get(field_name)
        if isinstance(value, list) and value:
            score += 10 + len(value)
        props = parsed.get("properties")
        if isinstance(props, dict):
            nested = props.get(field_name)
            if isinstance(nested, list) and nested and isinstance(nested[0], str):
                score += 5 + len(nested)
    if parsed.get("description") and parsed.get("properties"):
        score -= 5
    return score


def _extract_json(
    text: str,
    *,
    output_model: type[BaseModel] | None = None,
) -> dict | list:
    """Pull a JSON object or array from *text*, trying several strategies."""
    candidates: list[tuple[int, dict | list]] = []

    for match in _CODE_FENCE_RE.finditer(text or ""):
        loaded = _try_load_json(match.group(1).strip())
        if loaded is not None:
            candidates.append(
                (_score_parsed_candidate(loaded, output_model), loaded)
            )

    bare_match = _BARE_JSON_RE.search(text or "")
    if bare_match:
        loaded = _try_load_json(bare_match.group(1).strip())
        if loaded is not None:
            candidates.append(
                (_score_parsed_candidate(loaded, output_model), loaded)
            )

    loaded = _try_load_json((text or "").strip())
    if loaded is not None:
        candidates.append((_score_parsed_candidate(loaded, output_model), loaded))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    raise ValueError(
        f"Could not extract valid JSON from the model response. "
        f"First 300 chars: {text[:300]!r}"
    )
