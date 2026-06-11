# Research Agent — Architecture Analysis

**Generated:** Sunday 24 May 2026  
**Repo:** `Research Agent` (LangGraph Python pipeline + Angular UI + MongoDB)

> Copy synced from Obsidian vault. Primary location:  
> `Obsidian Vault/Reasearch Agent/Analysis.md`

---

## Summary

This app is an **agentic research pipeline**: a LangGraph workflow that plans web searches, gathers noisy text from the open web, uses an LLM to **curate** structured event records, then **deterministically** merges, deduplicates, filters, and persists them in **MongoDB**. An Angular front end reads the same database through a Python REST API.

The “agent” part is not magic autonomy — it is a **fixed graph of steps** where the LLM is invoked at specific decision points (plan queries, parse listings, tag events, optional dedupe/exclusions). Everything else is ordinary Python (HTTP, parsing, hashing, database I/O).

---

## How the parts wire together

```mermaid
flowchart TB
  subgraph config [Topic config - YAML + topics.json]
    TJ[topics/topics.json]
    SM[subject_matter.yaml]
    PG[prompt_guides.yaml]
    EX[exclusions.yaml]
  end

  subgraph cli [CLI / scheduler]
    RUN[run-once / serve]
    API[agent api]
    MIG[migrate-mongodb]
  end

  subgraph graph [LangGraph workflow]
    PLAN[plan - LLM]
    SEARCH[search - DuckDuckGo]
    CRAWL[crawl - httpx + BS4]
    NORM[normalize - LLM]
    ENRICH[enrich - og:image]
    FP[fingerprint]
    OUT[output - MongoDB]
  end

  subgraph mongo [MongoDB per topic db]
    EV[events]
    IM[images]
    VN[venues]
    RP[reports]
    US[users]
  end

  subgraph web [Angular web/]
    UI[List / Admin / Home]
  end

  TJ --> RUN
  SM --> PLAN
  SM --> NORM
  PG --> PLAN
  PG --> NORM
  EX --> OUT

  RUN --> graph
  graph --> mongo
  API --> mongo
  UI --> API
```

### Runtime processes (local dev)

| Process | Command | Role |
|---------|---------|------|
| Research agent | `python -m agent run-once` or `serve` | Runs LangGraph; writes MongoDB |
| REST API | `python -m agent api --port 8765` | Serves events, images, admin data to Angular |
| Angular dev server | `cd web && npm start` | UI; proxies `/api/*` to port 8765 |

Production deploy (Docker) runs the agent scheduler + API + nginx in one container (`deploy/start.sh`).

---

## LangGraph and “agentic AI”

**LangGraph** (`src/agent/workflow.py`) compiles a `StateGraph` over `AgentState`. Each node is a pure function in `graph_nodes.py`; edges are fixed:

```
START → plan → search → crawl → normalize → enrich → fingerprint → output → END
```

This makes the app **agentic** in a practical sense:

1. **Goal-directed loop (single pass):** The planner chooses *what to search* based on topic prompts and recent history — not hard-coded queries.
2. **Tool use (deterministic):** Search and crawl nodes call DuckDuckGo and httpx/BeautifulSoup to gather evidence from the web.
3. **Structured reasoning:** The curator LLM turns unstructured HTML/snippets into typed `Resource` objects (Pydantic).
4. **Stateful merge:** Output is not “replace all data” — new runs merge into existing MongoDB rows with dedupe rules.
5. **Observability:** Each run saves a report document (queries, crawled URLs, merge stats, diagnostics).

What LangGraph does **not** do here: multi-step ReAct loops, human-in-the-loop, or dynamic re-planning mid-run. The graph topology is static; intelligence is injected at named nodes.

---

## Pipeline steps (what each node does)

| Node | LLM? | Input | Output |
|------|-------|-------|--------|
| **plan** | Yes | Topic planner prompts + date window + variation block | `queries[]` |
| **search** | No | Queries | `raw_search_text` (DuckDuckGo snippets) |
| **crawl** | No | Search text (seed URLs) | Appends same-site HTML; `crawled_urls[]` |
| **normalize** | Yes | Raw text + curator prompts | `resources[]` (JSON dicts) |
| **enrich** | No | Resources | Fills missing `thumbnail_url` via og:image + inline `[IMG]` scoring |
| **fingerprint** | No | Resources | Compares hash to `data/<topic>/snapshot.json` |
| **output** | No* | Resources + stats | MongoDB merge, report, snapshot update |

\*Post-merge steps inside `write_output()` may call the LLM again (exclusions, tagging, semantic dedupe).

---

## Data storage (MongoDB)

Connection: `MONGODB_URI` in `.env` (local `mongodb://localhost:27017/` or Atlas).

Each topic has a **`db` name** in `topics/topics.json` (e.g. `bgc`, `galway-music`). That string is the MongoDB **database name**.

| Collection | Written by | Read by |
|------------|------------|---------|
| **events** | `local_output.merge_and_write` / `event_store` | API `/api/{db}/events`, Angular list |
| **images** | `image_cache.cache_thumbnails` / `image_store` | API `/api/{db}/images/{id}` |
| **venues** | Venue migration + admin API | Admin venues UI, event venue links |
| **reports** | `report_store.save_run_report` | Admin reports UI |
| **users** | Email subscribe API | Admin users UI |
| **schema_migrations** | `migrations_runner` | Startup only |

**On disk (gitignored):** only `data/<topic_id>/snapshot.json` — fingerprint of the *current run’s* curated list for change detection. Legacy `agent_research.xlsx`, `events.json`, and `images/` were migrated via `python -m agent migrate-mongodb`.

### Merge behaviour (`local_output.py`)

After each run, new `Resource` rows are merged into **events** with deterministic rules:

- Drop past events (local timezone via `local_today()`).
- Skip re-ingest when same URL **and** same act+date.
- Semantic duplicate: same act+date → append URL to **Sources** (different domain only).
- Partial act-name duplicate: substring match + same venue+date → keep longer name.
- Optional **LLM semantic dedupe** clusters same-day near-duplicates.
- **Exclusions:** YAML `drop_terms` (regex) + optional LLM phrase rules.
- **Event tagging:** LLM assigns up to 3 tags per untagged row.
- **Posters:** download to **images**; events store `image_id`; API serves bytes.

---

## LLM use

These are the **only** places an LLM is invoked. Everything else could run without AI (but would produce empty or useless curated data).

| Step | Module | Model call | Structured output schema | Hard without LLM? |
|------|--------|------------|--------------------------|-------------------|
| **Query planner** | `graph_nodes.node_plan` | `PlanQueries` | List of search strings | Yes — needs domain knowledge + variety |
| **Curator / normalizer** | `graph_nodes.node_normalize` | `ResourceListPayload` | List of `Resource` | Yes — parsing messy HTML/listings into rows |
| **Exclusion phrases** | `exclusion_prune._llm_excluded_event_ids` | `ExclusionPruneResult` | Event IDs to drop | Hard — fuzzy rules like “drag-themed brunch” |
| **Event tagging** | `event_tagging.apply_event_tags` | `EventTaggingResult` | Tags per event ID | Moderate — genre/theme labels |
| **Semantic dedupe** | `semantic_dedupe.find_same_event_clusters` | `SemanticDedupeClusters` | Groups of duplicate IDs | Hard — “same gig, different wording” |

**Not LLM:** DuckDuckGo search, site crawl, og:image fetch, poster download, spreadsheet-style merge logic, fingerprinting, MongoDB I/O, JSON API, Angular UI.

### LLM backends (`llm_factory.py`)

Exactly one backend enabled in `.env`:

- **OpenAI** — `ChatOpenAI`, cloud API key.
- **Ollama local** — OpenAI-compatible `http://127.0.0.1:11434/v1`.
- **Ollama Cloud** — remote OpenAI-compat endpoint; uses prompt-based JSON fallback (`structured_output.py`) because cloud lacks native `response_format`.

Planner uses **higher temperature** (`PLANNER_TEMPERATURE`, default 0.85) for query diversity. Curator and post-merge steps use **temperature 0**.

---

## LLM prompts — separation from engine code

Prompts live in **YAML under `topics/<id>/`**, not in Python strings (except small fixed system prompts for exclusions/tags/dedupe). Python **loads, validates, and injects** them at runtime via `config.SUBJECT` and `config.PROMPT_GUIDES`.

### File map

| File | Loaded by | Controls |
|------|-----------|----------|
| **`topics/<id>/subject_matter.yaml`** | `subject_config.load_subject_config` → `config.SUBJECT` | Topic label; **planner_system_prompt**, **planner_user_message**, **curator_system_prompt** |
| **`topics/<id>/prompt_guides.yaml`** | `prompt_guides.load_prompt_guides` → `config.PROMPT_GUIDES` | Resource labels; **planner_query_angles**; **planner_date_suffix** / **curator_date_suffix** (geo priority) |
| **`topics/<id>/exclusions.yaml`** | `exclusion_config` → reloaded each merge | **drop_terms** (no LLM); **exclusions** phrases (LLM) |
| **`event_tagging.py`** | `_SYSTEM` constant in Python | Tagging rules (could move to YAML later) |
| **`semantic_dedupe.py`** | `_SYSTEM` constant in Python | Duplicate-cluster rules |
| **`exclusion_prune.py`** | `_SYSTEM` constant in Python | Exclusion interpretation rules |

### How prompts feed the graph

**Planner (`node_plan`):**

```
SystemMessage(config.SUBJECT.planner_system_prompt)
HumanMessage(
  config.SUBJECT.planner_user_message
  + planner_date_instruction(PROMPT_GUIDES)      # engine: today + 30-day window
  + "Produce up to N queries"
  + build_planner_variation_block(PROMPT_GUIDES) # engine: recent queries + random angles
)
→ invoke_structured(..., PlanQueries)
```

**Curator (`node_normalize`):**

```
SystemMessage(config.SUBJECT.curator_system_prompt)
HumanMessage(
  curator_date_instruction(PROMPT_GUIDES)        # engine: date window + portal hints
  + "Extract curated resource list"
  + truncated raw_search_text                    # DuckDuckGo + crawl block
)
→ invoke_structured(..., ResourceListPayload)
→ filter_events_in_upcoming_window (Python)
```

**Engine-injected fragments** (`event_window.py`, `query_planner.py`) stay topic-agnostic: ISO dates, horizon days, “do not repeat recent searches”, shuffled query angles. Topic-specific *wording* stays in YAML.

### Switching topics

Edit `topics/topics.json` (`active` id) or set `ACTIVE_TOPIC` in `.env`. Each topic folder carries its own prompts, exclusions, and MongoDB `db` name — **no Python changes required**.

---

## Angular front end

- **`TopicService`** loads `topics/topics.json` → active topic’s `db`, title, background.
- **`ListComponent`** GET `/api/{db}/events` — table of gigs, tag/venue filters, poster images via `/api/{db}/images/...`.
- **Admin** (`/admin/*`) — reports, venues CRUD, email subscribers; gated by `ADMIN_PASSWORD` via `POST /api/admin/verify-password`.

The UI never reads `events.json` from disk; it always goes through the API → MongoDB.

---

## Key source files

| Area | Path |
|------|------|
| Graph wiring | `src/agent/workflow.py` |
| Nodes | `src/agent/graph_nodes.py` |
| LLM + structured output | `src/agent/llm_factory.py`, `structured_output.py` |
| Topic prompts | `topics/<id>/subject_matter.yaml`, `prompt_guides.yaml` |
| Merge + dedupe | `src/agent/local_output.py` |
| MongoDB events/images | `src/agent/event_store.py`, `image_store.py`, `mongodb.py` |
| REST API | `src/agent/api.py` |
| Run reports | `src/agent/report_store.py` |
| Angular app | `web/src/app/` |

---

## Mental model (one paragraph)

You configure **what** to research in YAML; LangGraph runs a **fixed pipeline** that uses an LLM to plan searches and extract gigs from messy web text; Python **merges** those gigs into MongoDB with strict dedupe and cleanup rules; optional LLM passes **tag**, **exclude**, or **dedupe** further; the Angular site **displays** the database through a small Starlette API. The LLM is the flexible “reader and planner”; the database and merge logic are the reliable “memory and librarian.”
