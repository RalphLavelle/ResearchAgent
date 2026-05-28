---
name: aiagent-research-pipeline
description: Guides changes to the LangGraph music-events research agent, MongoDB source-of-truth, deduplication, REST API / Notion outputs, and subject YAML. Use when editing this repo, docs/tasks, graph_nodes, local_output, notion_output, subject_matter.yaml, or when the user mentions AgentAI, MongoDB, dedup, or the research pipeline.
---

# AIAgent research pipeline

## Architecture (one sentence)

LangGraph runs `plan → search → crawl → normalize → enrich → fingerprint → output`; the LLM only plans queries and curates structured `Resource` rows from noisy text; everything else is deterministic code.

## Source of truth

- **MongoDB** (database name = topic's ``db`` property in ``topics.json``) is the database. Collections: ``events``, ``venues``, ``images``, ``reports``. The Angular app reads via ``GET /api/<db>/events``, ``GET /api/<db>/reports``, and ``GET /api/<db>/images/<id>``.
- **Run reports** are stored in MongoDB (``reports`` collection) at the end of each pipeline run: ``datetime`` (UTC), ``searches``, ``urls`` (crawled pages grouped by host), and ``changes`` (merge stats).
- **`data/snapshot.json`** fingerprints the **current run’s** resources for log messaging; Notion sync uses a fingerprint of the **full event store** (`canonical_fingerprint(all_resources)`).

## Topic vs engine

- **Topic-specific text** lives in `topics/<id>/`:
  - `subject_matter.yaml` — planner/curator system prompts and labels (`config.SUBJECT`)
  - `prompt_guides.yaml` — date-window suffixes and resource labels injected by `event_window.py` (`config.PROMPT_GUIDES`)
  - `exclusions.yaml`, `assets/`
- Use the **topic-creator** skill to scaffold new topics.
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
- **Planner**: `node_plan` → structured `PlanQueries`. Uses `build_planner_llm()` (higher `PLANNER_TEMPERATURE`, default 0.85) plus `query_planner.build_planner_variation_block()` — recent report searches to avoid and rotated angles from `prompt_guides.yaml` `planner_query_angles`. No default queries if key missing; errors logged.
- **Normalize**: `node_normalize` → structured `ResourceListPayload`; input capped (env `CURATOR_INPUT_MAX_CHARS`); crawler tail preserved when clipping; curator dedupe is per `(url, date, title)` so many gigs may share one listing URL; then date window filter, sort soonest-first.
- **Per-event images (Tasks 12, 13)**: `site_crawl._html_to_text` keeps `[IMG alt="…" src=…]` markers inline (decorations like logos/ads/banners filtered, mid-name tokens too) so the curator prompt can pick a distinct image per event. After the LLM, `enrich.enrich_thumbnails` runs Pass 1 — for groups of resources sharing a URL it **preserves any LLM-chosen thumbnail that is unique within the group** and only re-assigns blank or duplicate slots, scoring candidates against both `alt` text **and** the image filename's keywords (`_best_img_for_title` returns `None` when no candidate has any title-word overlap, so the slot falls through to og:image rather than the first-non-excluded poster). Pass 2 then fills any remaining nulls with the page's og:image (cached per-URL).
- **Local poster cache (Task 14, MongoDB Task 4)**: `image_cache.cache_thumbnails` runs inside `local_output.write_output`, between exclusion handling and semantic dedupe. Posters are stored **once per upstream URL** in the topic's MongoDB ``images`` collection — many events can share one blob. For every row with an `http(s)` poster URL, it downloads only when that URL is not already cached, then sets `thumbnail_url` to `/api/<db>/images/<hash>.<ext>`. Failed downloads degrade to `thumbnail_url = None`. `image_cache.garbage_collect` deletes blobs no longer referenced by any active event id. Event documents keep upstream **poster_url** for quality scoring / self-heal.

## Outputs

- **Angular API**: `api.py` serves `GET /api/<db>/events`, `GET /api/<db>/reports`, and `GET /api/<db>/images/<id>`.
- **Notion**: native table blocks; no inline images in cells (poster glyph pattern in `notion_output.py`).

## Config/env (do not overwrite `.env` without asking)

- Schedule: `SCHEDULE_INTERVAL_HOURS` in `.env` (hours only; default 1). Used by `agent serve`.
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
| Planner query diversity | `src/agent/query_planner.py` |
| Spreadsheet + dedup | `src/agent/local_output.py` |
| MongoDB persistence | `src/agent/event_store.py`, `src/agent/venue_store.py`, `src/agent/image_store.py`, `src/agent/report_store.py`, `src/agent/mongodb.py` |
| REST API for Angular | `src/agent/api.py` |
| Legacy file migration | `src/agent/migrate_mongodb.py`, `src/agent/migrate_venues.py` |
| Per-run reports (MongoDB) | `src/agent/report_store.py` |
| Events JSON for Angular | `src/agent/json_output.py` |
| Notion API | `src/agent/notion_output.py` |
| Crawl + image markers | `src/agent/site_crawl.py` |
| Per-event image enrichment | `src/agent/enrich.py` |
| Local poster cache + GC | `src/agent/image_cache.py` |
| Topics registry + paths | `src/agent/topics.py`, `topics/topics.json` |
| Models + `resource_from_dict` | `src/agent/models.py` |
| New topic scaffolding | `.cursor/skills/topic-creator/SKILL.md` |
| Task specs | `docs/tasks/*.md` |

## `resource_from_dict`

Rebuilds **`Resource`** from graph-state dicts. Unknown legacy keys (`resource_type`, `price`, `participatory`, etc.) from older snapshots are ignored.
