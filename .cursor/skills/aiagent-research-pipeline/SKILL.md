---
name: aiagent-research-pipeline
description: Guides changes to the LangGraph music-events research agent, spreadsheet source-of-truth, deduplication, events JSON / Notion outputs, and subject YAML. Use when editing this repo, docs/tasks, graph_nodes, local_output, notion_output, subject_matter.yaml, or when the user mentions AgentAI, agent_research.xlsx, dedup, or the research pipeline.
---

# AIAgent research pipeline

## Architecture (one sentence)

LangGraph runs `plan → search → crawl → normalize → enrich → fingerprint → output`; the LLM only plans queries and curates structured `Resource` rows from noisy text; everything else is deterministic code.

## Source of truth

- **`agent_research.xlsx`** (under `OUTPUT_DIR`, default repo `data/`) is the database. `events.json` and Notion are generated from **`load_spreadsheet_resources()`** after each merge, not from the current run's `resources` alone.
- **`Run_<AEST>.md`** (Task 11) is written once per real run by `run_report.write_run_report` from `node_local_output`. Three sections: *Searches* (planner queries), *Search and crawl* (`crawled_urls` from `node_crawl`, grouped by host), *Normalize* (curated `Resource` Pydantic models serialised as JSON, each with its source URL). The single append-only `run_log.md` is removed.
- **`data/snapshot.json`** fingerprints the **current run’s** resources for log messaging; Notion sync uses a fingerprint of the **full spreadsheet** (`canonical_fingerprint(all_resources)`).

## Topic vs engine

- **Topic-specific text** lives in `config/subject_matter.yaml` (planner/curator prompts, labels). Loaded as `config.SUBJECT`. Change YAML or `SUBJECT_MATTER_CONFIG` in `.env` to switch topics without editing Python.
- **Engine code** in `src/agent/` must stay topic-agnostic (no hard-coded “Gold Coast” etc. in logic—only in YAML).

## Deduplication (`local_output.merge_and_write`)

Apply changes here when tasks mention duplicates or Sources:

1. **Identical ingest** — skip only when URL **and** the same semantic `(act, date)` already exist (`Event ID`-keyed storage allows many rows to share one listing URL).
2. **Same normalized act + same date** (venue ignored for this match) — treat as duplicate; append new URL to **Sources** only if **different domain** than primary URL.
3. **Partial act names** — one act string contains the other (min length 4), **and** same **venue** + same **date** — duplicate; keep **longer** act name as canonical Event cell; add URL to Sources per domain rule.
4. **Past events** — removed on each merge (`local_today()` — uses display timezone, not UTC).
5. **Poster URL self-heal (Task 13)** — every dedupe branch (URL re-ingest, exact match, partial match) calls `_maybe_upgrade_poster`, which uses `enrich.poster_quality_score` to replace stale/decorative existing Poster URLs with fresher event-specific ones from the new ingest. Never downgrades. Tiers: empty (-1) < decoration logo/ad/banner (0) < generic (1) < filename keywords overlap the act name (2+).

Spreadsheet columns: `Event, Venue, Location, Date, URL, Sources, Poster URL, Summary, Added, Event ID`. Loader tolerates old files missing **Sources** only.

## LLM integration

- **Backends**: `OPENAI_ENABLED=true` uses cloud OpenAI; `OLLAMA_ENABLED=true` uses OpenAI-compat **Ollama** — local or cloud (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, optional `OLLAMA_EXTRA_BODY_JSON`). Exactly one must be enabled. Wired through `agent/llm_factory.py` (`build_chat_llm`); CLI calls `verify_llm_at_startup()` before `run-once`/`serve`.
- **Structured output**: All LLM calls go through `agent/structured_output.py` → `invoke_structured(llm, messages, PydanticModel)`. For backends that support native structured output (OpenAI, local Ollama), it uses `with_structured_output()`. For Ollama Cloud (which lacks `response_format` support), it falls back to embedding the JSON schema in the prompt and extracting/parsing JSON from the plain-text response.
- **Planner**: `node_plan` → structured `PlanQueries`. No default queries if key missing; errors logged.
- **Normalize**: `node_normalize` → structured `ResourceListPayload`; input capped (env `CURATOR_INPUT_MAX_CHARS`); crawler tail preserved when clipping; curator dedupe is per `(url, date, title)` so many gigs may share one listing URL; then date window filter, sort soonest-first.
- **Per-event images (Tasks 12, 13)**: `site_crawl._html_to_text` keeps `[IMG alt="…" src=…]` markers inline (decorations like logos/ads/banners filtered, mid-name tokens too) so the curator prompt can pick a distinct image per event. After the LLM, `enrich.enrich_thumbnails` runs Pass 1 — for groups of resources sharing a URL it **preserves any LLM-chosen thumbnail that is unique within the group** and only re-assigns blank or duplicate slots, scoring candidates against both `alt` text **and** the image filename's keywords (`_best_img_for_title` returns `None` when no candidate has any title-word overlap, so the slot falls through to og:image rather than the first-non-excluded poster). Pass 2 then fills any remaining nulls with the page's og:image (cached per-URL).
- **Local poster cache (Task 14)**: `image_cache.cache_thumbnails` runs inside `local_output.write_output`, between `load_spreadsheet_resources()` and `write_events_json()`. For every spreadsheet row with an `http(s)` poster URL, it downloads the bytes once into `data/images/<event-id>.<ext>` and rewrites `events.json`'s `thumbnailUrl` to the relative path `data/images/<id>.<ext>` so the Angular app loads same-origin (no more hotlink-protected broken icons). Failed downloads degrade to `thumbnail_url = None` so the 🎸 placeholder renders. A sidecar `data/images/_index.json` (event_id → source_url) makes re-runs zero-cost; only an URL change triggers a re-fetch. `image_cache.garbage_collect` is called right after to delete files for Event IDs no longer in the spreadsheet (kept lock-step with `merge_and_write`'s past-event pruning). The spreadsheet's **Poster URL** column always stores the **upstream** URL — only the JSON gets the local path.

## Outputs

- **Angular JSON**: `json_output.py` writes `events.json` (under `OUTPUT_DIR`) at local-output time — `{ generated, events[] }` in camelCase; the Angular app loads `/data/events.json`.
- **Notion**: native table blocks; no inline images in cells (poster glyph pattern in `notion_output.py`).

## Config/env (do not overwrite `.env` without asking)

- Schedule: `config/schedule.yaml` — `interval_minutes` wins over `interval_hours` when non-zero.
- Crawl toggles: `CRAWL_ENABLED`, limits in `config.py` / `.env`.

## Tests

From repo root:

```bash
$env:PYTHONPATH="src"; venv\Scripts\python.exe -m pytest
```

(PowerShell.) Prefer `venv\Scripts\python.exe` if system Python lacks pytest.

## Key files

| Area | Path |
|------|------|
| Graph wiring | `src/agent/workflow.py` |
| Nodes + LLM calls | `src/agent/graph_nodes.py` |
| Structured output fallback | `src/agent/structured_output.py` |
| Spreadsheet + dedup | `src/agent/local_output.py` |
| Per-run markdown report | `src/agent/run_report.py` |
| Events JSON for Angular | `src/agent/json_output.py` |
| Notion API | `src/agent/notion_output.py` |
| Crawl + image markers | `src/agent/site_crawl.py` |
| Per-event image enrichment | `src/agent/enrich.py` |
| Local poster cache + GC | `src/agent/image_cache.py` |
| Date/window/title split | `src/agent/event_window.py` |
| Models + `resource_from_dict` | `src/agent/models.py` |
| Task specs | `docs/tasks/*.md` |

## `resource_from_dict`

Rebuilds **`Resource`** from graph-state dicts. Unknown legacy keys (`resource_type`, `price`, `participatory`, etc.) from older snapshots are ignored.
