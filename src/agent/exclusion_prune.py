"""Event exclusions after spreadsheet merge (Task 19).

Exactly **one** pass runs every ``write_output``: after ``merge_and_write`` loads the
full workbook, drops rows by optional **deterministic** ``drop_terms`` (whole-word
matches), then optionally unions **LLM** picks from ``exclusions`` phrases when an
inference backend is enabled.

Rules YAML is re-read each call via ``EVENT_EXCLUSIONS_CONFIG_PATH``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent import config
from agent.exclusion_config import EventExclusionsConfig, load_event_exclusions
from agent.llm_factory import build_chat_llm
from agent.local_output import (
    RESEARCH_FILENAME,
    _IDX_EVENT,
    _IDX_LOCATION,
    _IDX_SUMMARY,
    _IDX_URL,
    _IDX_VENUE,
    _load_existing_rows,
    _row_date,
    _row_event_id,
    _write_workbook,
    output_directory,
)
from agent.structured_output import invoke_structured

logger = logging.getLogger(__name__)

_SYSTEM = """You apply developer-authored exclusion rules: which events must NOT stay in the database.

Input:
1. Numbered exclusion rules — treat them as the user's intent for what to remove.
2. JSON events with stable ids (name, venue, location, summary, url, date).

Task: return ``excluded_event_ids`` for every event that matches **at least one** rule when you read name, venue, location, summary, and URL together.

How to match:
- When a rule names a theme (e.g. drag, drag-themed, bingo, comedy), exclude listings where that theme is **plainly present** in the text—even if DJs, bands, or “music” are also mentioned. Drag brunch, drag show, drag night, drag bingo, etc. count as drag-themed unless the word is clearly unrelated noise.
- Keep ordinary concerts/club gigs **only when** no exclusion rule clearly applies.
- If and only if a row is genuinely ambiguous after reading all fields, err on the side of **keeping** it."""


class ExclusionPruneResult(BaseModel):
    excluded_event_ids: list[str] = Field(
        default_factory=list,
        description="Event IDs (uuid strings) to exclude.",
    )


def _active_exclusions_config() -> EventExclusionsConfig:
    """Fresh YAML load each call so edits apply without process restart."""
    return load_event_exclusions(config.EVENT_EXCLUSIONS_CONFIG_PATH)


def _events_from_workbook(path: Path) -> tuple[dict[str, list], list[dict]]:
    """Return ``(existing_rows_dict, event_payloads_for_llm)``."""
    existing = _load_existing_rows(path)
    events: list[dict] = []
    for _uk, row in existing.items():
        d = _row_date(row)
        events.append(
            {
                "id": _row_event_id(row),
                "name": str(row[_IDX_EVENT] or ""),
                "venue": str(row[_IDX_VENUE] or ""),
                "location": str(row[_IDX_LOCATION] or ""),
                "date": d.isoformat() if d else "",
                "url": str(row[_IDX_URL] or ""),
                "summary": str(row[_IDX_SUMMARY] or ""),
            }
        )
    return existing, events


def _deterministic_drop_ids(events: list[dict], terms: list[str]) -> set[str]:
    """Whole-word, case-insensitive match across displayed fields."""
    cleaned = [t.strip().lower() for t in terms if str(t).strip()]
    if not cleaned:
        return set()

    drop: set[str] = set()
    for e in events:
        eid = str(e.get("id") or "").strip()
        if not eid:
            continue
        blob = " ".join(
            str(e.get(k) or "") for k in ("name", "venue", "location", "summary", "url")
        ).lower()
        for term in cleaned:
            pat = rf"\b{re.escape(term)}\b"
            if re.search(pat, blob):
                drop.add(eid)
                break
    if drop:
        logger.info(
            "Exclusion drop_terms removed %d row(s) by literal word match (no LLM).",
            len(drop),
        )
    return drop


def _llm_excluded_event_ids(events: list[dict], rules: list[str]) -> set[str]:
    """Call the model; return ids to exclude (subset of known ids)."""
    valid_ids = {str(e.get("id") or "").strip() for e in events}
    valid_ids.discard("")
    if not valid_ids or not rules:
        return set()

    numbered_rules = "\n".join(f"{i + 1}. {rule}" for i, rule in enumerate(rules))
    body = json.dumps(events, ensure_ascii=False, indent=2)
    if len(body) > 180_000:
        body = body[:180_000] + "\n…(truncated)"
        logger.warning("Exclusion prompt truncated for size.")

    llm = build_chat_llm()
    try:
        out: ExclusionPruneResult = invoke_structured(
            llm,
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=(
                        "Exclusion rules:\n"
                        f"{numbered_rules}\n\n"
                        "Events:\n"
                        f"{body}"
                    )
                ),
            ],
            ExclusionPruneResult,
        )
    except Exception as exc:
        logger.warning("Exclusion LLM call failed: %s", exc)
        return set()

    raw_drop = {i.strip() for i in (out.excluded_event_ids or []) if str(i).strip()}
    to_remove = {i for i in raw_drop if i in valid_ids}
    bogus = raw_drop - valid_ids
    if bogus:
        logger.debug(
            "Exclusion filter ignored unknown ids from LLM: %s",
            sorted(bogus)[:12],
        )
    if to_remove:
        logger.info("Exclusion LLM marked %d row(s) to remove.", len(to_remove))
    else:
        logger.debug(
            "Exclusion LLM returned no ids (%d events, %d phrase rule(s)).",
            len(events),
            len(rules),
        )
    return to_remove


def apply_event_exclusions(path: Path | None = None) -> int:
    """After merge: drop rows matching ``drop_terms`` and/or LLM ``exclusions``.

    Runs once per ``write_output`` on the **full** workbook so new and old rows
    are evaluated together.

    Returns how many rows were removed.
    """
    cfg = _active_exclusions_config()
    terms = [t.strip() for t in cfg.drop_terms if str(t).strip()]
    rules = [r.strip() for r in cfg.exclusions if str(r).strip()]

    if not terms and not rules:
        return 0

    if path is None:
        path = output_directory() / RESEARCH_FILENAME
    if not path.exists():
        return 0

    existing, events = _events_from_workbook(path)
    if not existing:
        return 0

    logger.info(
        "Event exclusions: %d spreadsheet row(s), %d drop_term(s), %d LLM phrase rule(s).",
        len(events),
        len(terms),
        len(rules),
    )

    to_remove = _deterministic_drop_ids(events, terms)

    if rules and config.llm_inference_enabled():
        to_remove |= _llm_excluded_event_ids(events, rules)
    elif rules and not config.llm_inference_enabled():
        logger.warning(
            "Exclusion YAML has %d phrase rule(s) but no LLM backend — "
            "only drop_terms apply; enable OpenAI or Ollama for phrase matching.",
            len(rules),
        )

    if not to_remove:
        return 0

    for uk, row in list(existing.items()):
        if _row_event_id(row) in to_remove:
            del existing[uk]

    _write_workbook(path, existing)
    logger.info(
        "Event exclusions removed %d spreadsheet row(s) total.",
        len(to_remove),
    )
    return len(to_remove)
