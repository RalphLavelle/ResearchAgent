---
name: aiagent-research-pipeline
description: Guides changes to the LangGraph music-events research agent, MongoDB source-of-truth, deduplication, REST API output, and subject YAML. Use when editing this repo, docs/tasks, graph_nodes, local_output, subject_matter.yaml, or when the user mentions AgentAI, MongoDB, dedup, or the research pipeline.
---

# AIAgent research pipeline

## Architecture (one sentence)

LangGraph runs `plan → search → crawl → normalize → enrich → fingerprint → output`; the LLM only plans queries and curates structured `Resource` rows from noisy text; everything else is deterministic code.

## Source of truth

- **MongoDB** (database name = topic's ``db`` property in ``topics.json``) is the database. Collections: ``events``, ``venues``, ``images``, ``reports``, ``users``. The Angular app reads via ``GET /api/<db>/events``, ``GET /api/<db>/reports``, and ``GET /api/<db>/images/<id>``; weekly email signup uses ``POST /api/<db>/users/subscribe``.
- **Run reports** are stored in MongoDB (``reports`` collection) at the end of each pipeline run: ``datetime`` (UTC), ``llm_model`` (active model label — ``OLLAMA_MODEL`` / ``OPENAI_MODEL``), optional ``planner_temperature`` (randomised planner sample), ``searches``, ``urls`` (crawled pages grouped by host), ``changes`` (merge stats), and optional ``diagnostics`` (why planner/search/crawl/curator steps produced no output).
- **`data/snapshot.json`** fingerprints the **current run’s** resources for log messaging (whether anything changed since last run).

## Topic vs engine

- **Topic-specific text** lives in `topics/<id>/`:
  - `subject_matter.yaml` — planner/curator system prompts and labels (`config.SUBJECT`)
  - `prompt_guides.yaml` — date-window suffixes and resource labels injected by `event_window.py` (`config.PROMPT_GUIDES`)
  - `exclusions.yaml`, `assets/`
- Use the **topic-creator** skill to scaffold new topics.
- **Engine code** in `src/agent/` must stay topic-agnostic (no hard-coded “Gold Coast” etc. in logic—only in YAML).

## Venue-first mining (Task 1)

**Smarter crawl page selection (Task 4):** `site_crawl._extract_internal_links` calls `_is_unlikely_event_page` to **hard-skip** non-event links before they are enqueued (gated by `CRAWL_SKIP_NON_EVENT_PAGES`, default true): whole-segment matches against `_NON_EVENT_SEGMENTS` (cart/checkout/login/legal/`win`) always skip; `_FOOD_DINING_WORDS` (food/menu/dining) skip only when `_has_event_signal` (substrings in `_EVENT_SIGNAL_SUBSTRINGS`) is false, so `/food-and-live-music` is kept but `/street-food-lineup` is dropped. `_link_event_priority` then scores the survivors: event/ticket/gig substrings `+10`, pagination `+8`, `_LOW_VALUE_FRAGMENTS` (about/news/shop) `-4`, food words `-8` (when no event signal) so dedicated gig/ticket pages are crawled first and `/ticketed-events` beats `/street-food-lineup`. Matching is on path segments/words (not raw substrings) so look-alikes like `/winery-sessions` survive the bare `/win` rule.

`venue_crawl.gather_venue_seed_urls` runs at the **start of `node_crawl`**: it recognises known venues (from the `venues` collection) in the DuckDuckGo blob by domain-label/name match (aggregator hosts excluded), fetches each venue homepage, and discovers its **"What's On"** page. The link is stored on the venue doc as `events_link` (+ `website`, `events_link_checked`) and returned as a **priority crawl seed** (ahead of the weighted memory seed), so `site_crawl` mines it — pagination links (`?page=N`, `/page/N`) are boosted in `_link_event_priority` so paged listings are followed. Stored `events_link`s are reused on later runs until older than `VENUE_EVENTS_LINK_TTL_DAYS`. **Two independent tiers with separate caps** (they must never starve each other): **(1) Memory** — reuse up to `MAX_VENUE_SEEDS` venues with a fresh `events_link`, picked **least-recently-mined first** (`fresh` is `random.shuffle`d then stable-sorted by `last_mined`; missing = never mined = highest priority) and stamped via `venue_store.mark_venues_mined`, so coverage rotates instead of repeating. **(2) Discovery** — **always runs** (even when the memory tier is full), capped at `MAX_VENUE_DISCOVERIES_PER_RUN`, recognising NEW venues in the DDG blob and persisting their What's On link so the linked-venue pool keeps growing. ⚠️ The old code did `return` once memory hit `MAX_VENUE_SEEDS`, so discovery never ran and the pool froze at a handful of venues that were then mined every run — keep the two tiers separate. After merge, `venue_store.update_last_event_dates` sets each venue's `last_event_date`. Admin venue edits preserve these via `_MINING_FIELDS` passthrough (now includes `last_mined`). Env: `VENUE_MINING_ENABLED`, `MAX_VENUE_SEEDS`, `MAX_VENUE_DISCOVERIES_PER_RUN`, `VENUE_EVENTS_LINK_TTL_DAYS`. (Read-only diagnostic: `scripts/diag_crawl.py`.)

## Fair crawl budget — round-robin (`site_crawl.deep_search_supplement`)

Seeds are crawled **round-robin**, not sequentially: each seed has its own `_SeedCrawl` BFS context (queue/visited/pages) and the crawler gives every seed **one page per round** until `MAX_CRAWL_PAGES_TOTAL` is hit (per-seed cap still `MAX_CRAWL_PAGES_PER_SEED`). This shares the budget fairly so high-priority venue seeds no longer drain it before the search-result seeds get crawled — the root cause of reports repeatedly showing the same venue hosts regardless of the run's search terms. `_fetch_html_page` isolates the per-URL fetch + content-type check (handy to monkeypatch in tests).

## Smarter crawl page selection (Task 4)

`site_crawl` spends a bounded page budget (`MAX_CRAWL_PAGES_TOTAL`/`_PER_SEED`), so which links get enqueued matters. Two tiers, both in `site_crawl.py`: (1) **hard skip** — `_is_unlikely_event_page` matches whole path *segments* against `_NON_EVENT_SEGMENTS` (cart/checkout/account/login/legal/`win`/competition/etc.) and `_extract_internal_links` drops those before enqueuing (gated by `config.CRAWL_SKIP_NON_EVENT_PAGES`, default true; segment match avoids false hits like `/winery-sessions`); (2) **soft de-prioritise** — `_link_event_priority` subtracts for `_LOW_VALUE_FRAGMENTS` (`/menu`, `/about`, `/gallery`, `/shop`, …) so event/ticket/whats-on pages (broad `event`/`ticket`/`concert`/`gig` keyword match, +10) outrank them; a ticket page under a low-value path (e.g. `/shop/tickets`) still nets positive. Net effect: `/ticketed-events` is crawled ahead of `/street-food-lineup`, and `/cart`/`/win` are never crawled.

## Deduplication (`local_output.merge_and_write`)

Apply changes here when tasks mention duplicates or Sources:

1. **Identical ingest** — skip only when URL **and** the same semantic `(act, date)` already exist (`Event ID`-keyed storage allows many rows to share one listing URL).
2. **Same normalized act + same date** (venue ignored for this match) — treat as duplicate; append new URL to **Sources** only if **different domain** than primary URL.
3. **Partial act names** — one act string contains the other (min length 4), **and** same **venue** + same **date** — duplicate; keep **longer** act name as canonical Event cell; add URL to Sources per domain rule.
4. **Past events** — removed on each merge (`local_today()` — uses display timezone, not UTC). **No future pruning** (Task 7): events any distance in the future are stored. The one-month display window is applied only at read time by the API (`event_window.api_window_iso_bounds`, default `API_EVENT_WINDOW_DAYS=30`) in `event_store.load_events_api_payload`.
5. **Poster URL self-heal (Task 13)** — every dedupe branch (URL re-ingest, exact match, partial match) calls `_maybe_upgrade_poster`, which uses `enrich.poster_quality_score` to replace stale/decorative existing Poster URLs with fresher event-specific ones from the new ingest. Never downgrades. Tiers: empty (-1) < decoration logo/ad/banner (0) < generic (1) < filename keywords overlap the act name (2+).

Spreadsheet columns: `Event, Venue, Location, Date, URL, Sources, Poster URL, Summary, Added, Event ID`. Loader tolerates old files missing **Sources** only.

## LLM integration

- **Backends**: `OPENAI_ENABLED=true` uses cloud OpenAI; `OLLAMA_ENABLED=true` uses OpenAI-compat **Ollama** — local or cloud (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, optional `OLLAMA_EXTRA_BODY_JSON`). Exactly one must be enabled. Wired through `agent/llm_factory.py` (`build_chat_llm`); CLI calls `verify_llm_at_startup()` before `run-once`/`serve`.
- **Structured output**: All LLM calls go through `agent/structured_output.py` → `invoke_structured(llm, messages, PydanticModel)`. For backends that support native structured output (OpenAI, local Ollama), it uses `with_structured_output()`. For Ollama Cloud (which lacks `response_format` support), it falls back to embedding the JSON schema in the prompt and extracting/parsing JSON from the plain-text response.
- **Planner**: `node_plan` → structured `PlanQueries`. Uses `build_planner_llm()` with a **randomised temperature** each run (`sample_planner_temperature`, default range ``PLANNER_TEMPERATURE_MIN``–``PLANNER_TEMPERATURE_MAX`` = ``0.0``–``1.0``; set both equal for a fixed value; legacy ``PLANNER_TEMPERATURE`` still works as a fixed override when min/max are unset) plus `query_planner.build_planner_variation_block()` — recent report searches to avoid and rotated angles from `prompt_guides.yaml` `planner_query_angles`. No default queries if key missing; errors logged. **If the planner LLM call fails** (model not found, auth, timeout, …), the run raises ``LLMInvocationError`` and **stops** — later steps need the same model, so there is no “continue with targeted venue queries only” fallback. The sampled temperature is stored on the run report as ``planner_temperature``.
- **Other LLM calls stay at temperature 0**: curator (`node_normalize`), event tagging, exclusion phrases, and semantic dedupe all use `build_chat_llm()` — they extract/classify structured data, so randomness would hurt accuracy rather than help coverage.
- **Targeted venue searches**: `node_plan` also calls `query_planner.load_targeted_venue_queries` → `build_targeted_venue_queries`, which renders a weighted-random `venue_query_min`–`venue_query_max` (default 3–6) `prompt_guides.yaml` `venue_query_template` ("What's on in {venue} in {location}, Australia") from the `venues` collection, biased by `strategy_scores` venue yield plus freshness signals (`events_link`, `last_mined`, `last_event_date`) with an exploration floor. `{location}` uses the venue's stored `location`, else a random `venue_query_locations` fallback (topic-agnostic — regions live in YAML, not code). `query_planner.merge_queries` puts these **ahead** of planner queries and caps at `MAX_SEARCH_QUERIES`, so some generated queries are discarded.
- **Normalize**: `node_normalize` → structured `ResourceListPayload`; input capped (env `CURATOR_INPUT_MAX_CHARS`); crawler tail preserved when clipping; curator dedupe is per `(url, date, title)` so many gigs may share one listing URL; then `filter_future_events` (keeps **all** dated events from today onward — no upper bound; drops only past/undated), sort soonest-first. The planner/curator prompts (`event_window.planner_date_instruction` / `curator_date_instruction`) ask for **all** future events, not a fixed window.
- **Per-event images (Tasks 12, 13)**: `site_crawl._html_to_text` keeps `[IMG alt="…" src=…]` markers inline (decorations like logos/ads/banners filtered, mid-name tokens too) so the curator prompt can pick a distinct image per event. After the LLM, `enrich.enrich_thumbnails` runs Pass 1 — for groups of resources sharing a URL it **preserves any LLM-chosen thumbnail that is unique within the group** and only re-assigns blank or duplicate slots, scoring candidates against both `alt` text **and** the image filename's keywords (`_best_img_for_title` returns `None` when no candidate has any title-word overlap, so the slot falls through to og:image rather than the first-non-excluded poster). Pass 2 then fills any remaining nulls with the page's og:image (cached per-URL).
- **Local poster cache (Task 14, MongoDB Task 4)**: `image_cache.cache_thumbnails` runs inside `local_output.write_output`, between exclusion handling and semantic dedupe. Posters are stored **once per upstream URL** in the topic's MongoDB ``images`` collection — many events can share one blob. For every row with an `http(s)` poster URL, it downloads only when that URL is not already cached, then sets `thumbnail_url` to `/api/<db>/images/<hash>.<ext>`. Failed downloads degrade to `thumbnail_url = None`. `image_cache.garbage_collect` deletes blobs no longer referenced by any active event id. Event documents keep upstream **poster_url** for quality scoring / self-heal.

## Outputs

- **Angular API**: `api.py` serves `GET /api/<db>/events`, `GET /api/<db>/reports`, and `GET /api/<db>/images/<id>`.

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
| Weekly email subscribers | `src/agent/user_store.py` |
| Legacy file migration | `src/agent/migrate_mongodb.py`, `src/agent/migrate_venues.py` |
| Per-run reports (MongoDB) | `src/agent/report_store.py` |
| Events JSON for Angular | `src/agent/json_output.py` |
| Crawl + image markers | `src/agent/site_crawl.py` |
| Venue-first mining (What's On discovery + priority seeds) | `src/agent/venue_crawl.py` |
| Per-event image enrichment | `src/agent/enrich.py` |
| Local poster cache + GC | `src/agent/image_cache.py` |
| Topics registry + paths | `src/agent/topics.py`, `topics/topics.json` |
| Models + `resource_from_dict` | `src/agent/models.py` |
| New topic scaffolding | `.cursor/skills/topic-creator/SKILL.md` |
| Task specs | `docs/tasks/*.md` |

## `resource_from_dict`

Rebuilds **`Resource`** from graph-state dicts. Unknown legacy keys (`resource_type`, `price`, `participatory`, etc.) from older snapshots are ignored.
