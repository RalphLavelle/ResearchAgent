---
name: aiagent-research-pipeline
description: Guides changes to the LangGraph music-events research agent, spreadsheet source-of-truth, deduplication, HTML/Notion outputs, and subject YAML. Use when editing this repo, docs/tasks, graph_nodes, local_output, notion_output, html_output, subject_matter.yaml, or when the user mentions AgentAI, agent_research.xlsx, dedup, or the research pipeline.
---

# AIAgent research pipeline

## Architecture (one sentence)

LangGraph runs `plan → search → crawl → normalize → enrich → fingerprint → output`; the LLM only plans queries and curates structured `Resource` rows from noisy text; everything else is deterministic code.

## Source of truth

- **`agent_research.xlsx`** (under `OUTPUT_DIR`, default repo `data/`) is the database. HTML and Notion are generated from **`load_spreadsheet_resources()`** after each merge, not from the current run’s `resources` alone.
- **`run_log.md`** is append-only text.
- **`data/snapshot.json`** fingerprints the **current run’s** resources for log messaging; Notion sync uses a fingerprint of the **full spreadsheet** (`canonical_fingerprint(all_resources)`).

## Topic vs engine

- **Topic-specific text** lives in `config/subject_matter.yaml` (planner/curator prompts, labels). Loaded as `config.SUBJECT`. Change YAML or `SUBJECT_MATTER_CONFIG` in `.env` to switch topics without editing Python.
- **Engine code** in `src/agent/` must stay topic-agnostic (no hard-coded “Gold Coast” etc. in logic—only in YAML).

## Deduplication (`local_output.merge_and_write`)

Apply changes here when tasks mention duplicates or Sources:

1. **Exact URL** — row skipped.
2. **Same normalized act + same date** (venue ignored for this match) — treat as duplicate; append new URL to **Sources** only if **different domain** than primary URL.
3. **Partial act names** — one act string contains the other (min length 4), **and** same **venue** + same **date** — duplicate; keep **longer** act name as canonical Event cell; add URL to Sources per domain rule.
4. **Past events** — removed on each merge (`utc_today()`).

Spreadsheet columns: `Event, Venue, Location, Date, URL, Sources, Poster URL, Summary, Added`. Loader tolerates old files missing **Sources** only.

## LLM integration

- **Planner**: `node_plan` → structured `PlanQueries`. No default queries if key missing; errors logged.
- **Curator**: `node_normalize` → structured `ResourceListPayload`; input capped (~200k chars); then URL dedupe in node, date window filter, sort soonest-first.

## Outputs

- **HTML**: `templates/event_table.html` — row injection between `<!-- ROW_TEMPLATE_START -->` / `END`; do not duplicate `{{ROWS}}`-style placeholders (single splice point in `html_output.py`).
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
| Spreadsheet + dedup | `src/agent/local_output.py` |
| HTML template render | `src/agent/html_output.py` |
| Notion API | `src/agent/notion_output.py` |
| Crawl | `src/agent/site_crawl.py` |
| Date/window/title split | `src/agent/event_window.py` |
| Models + `resource_from_dict` | `src/agent/models.py` |
| Task specs | `docs/tasks/*.md` |

## `resource_from_dict`

Must pass through **`resource_type`** and **`price`** (and all other `Resource` fields) so snapshots and tests round-trip correctly.
