# Research Agent

Python app using **LangGraph** that researches **configurable topics**, curates structured event rows, and stores them in **MongoDB**. An **Angular** front end reads events and poster images through a **REST API**.

**Topics registry:** `topics/topics.json` — the `active` id selects prompts, exclusions, UI chrome, and the topic's MongoDB database name (`db`).

Search uses LangChain's **`DuckDuckGoSearchRun`** (via its `api_wrapper`) — no search API key.

## Architecture

Each topic has its own **MongoDB database** (the `db` field in `topics.json`, e.g. `bgc` for Brisbane/Gold Coast). Collections include:

| Collection | Purpose |
|------------|---------|
| `events` | Source of truth — curated gigs (name, venue, date, URLs, tags, poster link, etc.) |
| `images` | Cached poster image bytes (one blob per upstream URL; many events can share one) |
| `venues` | Normalised venue records linked from events |
| `reports` | Pipeline run reports (searches, crawled URLs, merge stats, LLM model, optional memory seed) |
| `sources` | Multi-event listing URLs only — one document per host with a `urls` array (pages that yielded 2+ distinct events in a run); stale entries (`runs_contributed > 3 × events_added`) are pruned; one weighted-random URL is revisited each crawl |
| `strategy_scores` | Deterministic scorecards for source URLs, hosts, venues, and query strings, updated after each successful run report; venue scores now bias targeted venue query selection |
| `users` | Weekly email subscribers |
| `comments` | Visitor comments and suggestions (`name`, `comment`, `date`) |
| `schema_migrations` | Applied one-shot schema migration ids |

**On disk** (under `data/<topic_id>/`, gitignored):

| File | Purpose |
|------|---------|
| `snapshot.json` | Fingerprint of the last run's curated resources (for change detection) |

Legacy **`agent_research.xlsx`**, **`events.json`**, and **`images/`** folders are no longer used. If you still have them from an older version, run **`migrate-mongodb`** once (see below).

## Setup

### 1. Python and install

Requires **Python 3.11+**. Check what you have:

```powershell
python --version
```

Create a virtual environment named `venv/` in the repo root. A venv is an isolated Python sandbox so this project's packages don't clash with anything else on your machine. The `venv/` folder is gitignored.

```powershell
python -m venv venv
```

Install the project (and dev tools like `pytest`) into the venv. You can either call the venv's `pip.exe` directly:

```powershell
.\venv\Scripts\pip.exe install -e ".[dev]"
```

…or **activate** the venv first so `python` and `pip` resolve to the venv automatically:

```powershell
.\venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

> If activation is blocked, run PowerShell once as your user with
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and try again.
> Type `deactivate` to leave the venv. The rest of this README uses the
> direct `.\venv\Scripts\python.exe` form so it works without activation.

### 2. MongoDB

Set **`MONGODB_URI`** in `.env` (copy from `env.example`).

**Local dev** (MongoDB Community or Docker on the default port):

```env
MONGODB_URI=mongodb://localhost:27017/
```

**MongoDB Atlas** (cloud) — Database → Connect → Drivers → copy the connection string. The host must look like `cluster0.xxxxx.mongodb.net` (not `mongodb.com`):

```env
MONGODB_URI=mongodb+srv://myuser:myPass@cluster0.ab12cd.mongodb.net/?retryWrites=true&w=majority
```

**Migrate legacy file-based data** (spreadsheet, `events.json`, `images/` under `data/<topic_id>/`):

```powershell
.\venv\Scripts\python.exe -m agent migrate-mongodb
```

Add `--keep-files` to copy into MongoDB without deleting the old files. After migration, inspect databases in Compass (e.g. `bgc.events`, `bgc.images`).

### 3. LLM backend

Copy `env.example` to `.env` and enable exactly one backend:

**A. OpenAI** — set `OPENAI_ENABLED=true`, provide `OPENAI_API_KEY`, and optionally `OPENAI_MODEL`.

**B. Ollama locally** — set `OLLAMA_ENABLED=true`. [Install Ollama](https://ollama.ai/) and run it (`ollama serve` is usually automatic after install), then `ollama pull` the model you configure (default in `env.example` is `qwen3.5:0.8b`). Point `OLLAMA_BASE_URL` at the OpenAI-compat endpoint (normally `http://127.0.0.1:11434/v1`). Many setups use `OLLAMA_API_KEY=ollama` as a harmless placeholder — local Ollama does not authenticate by default.

**C. Ollama Cloud** — run large models without a local GPU. Set `OLLAMA_ENABLED=true` and pick one of two approaches:

| | Via local Ollama | Direct cloud API |
|---|---|---|
| Requires local Ollama? | Yes (`ollama serve` + `ollama signin`) | No |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434/v1` | `https://ollama.com/v1` |
| `OLLAMA_MODEL` | With `:cloud` suffix, e.g. `kimi-k2.6:cloud` | Without suffix, e.g. `kimi-k2.6` |
| `OLLAMA_API_KEY` | Your [Ollama API key](https://ollama.com/settings/keys) | Your [Ollama API key](https://ollama.com/settings/keys) |
| Extra setup | `ollama pull kimi-k2.6:cloud` | Just set `.env` |

Cloud is auto-detected by the `:cloud` model-name suffix or a non-localhost base URL. Local-only parameters (like `OLLAMA_DISABLE_THINKING_TEMPLATE`) are automatically skipped for cloud. See the [Ollama Cloud docs](https://docs.ollama.com/cloud) for available models.

**Startup check:** the agent probes the configured backend once at CLI startup (`run-once`, `serve`). If neither backend is enabled, or the enabled backend is misconfigured, you'll see an **`ERROR`** in the logs and exit code **`3`**.

### 4. Topics (optional)

The bundled topic **Live music in Brisbane and the Gold Coast** lives under `topics/live-music-brisbane-gold-coast/` (`subject_matter.yaml`, `prompt_guides.yaml`, `exclusions.yaml`, `assets/`).

To add a topic, use the **topic-creator** skill (`.cursor/skills/topic-creator/SKILL.md`) or copy the live-music folder and register a new entry in `topics.json` with a unique `db` name.

Override the active topic without editing JSON:

```env
ACTIVE_TOPIC=live-music-brisbane-gold-coast
```

### 5. Snapshots folder (optional)

Run fingerprints still land under `data/<topic_id>/` (default `data/live-music-brisbane-gold-coast/`). Override with:

```env
OUTPUT_DIR=D:\MyData\AgentAI
```

### 6. Schedule interval (optional)

When using `serve`, set the run interval in `.env` (hours only; default `1`):

```env
SCHEDULE_INTERVAL_HOURS=1
```

Restart `serve` after changing this value.

## Commands

- **One shot:**

  ```powershell
  .\venv\Scripts\python.exe -m agent run-once
  ```

- **Dry run** (no MongoDB writes / snapshot):

  ```powershell
  .\venv\Scripts\python.exe -m agent run-once --dry-run
  ```

- **Scheduled mode:**

  ```powershell
  .\venv\Scripts\python.exe -m agent serve
  ```

- **Strategy diagnostics** (read-only view of recursive self-improvement signals):

  ```powershell
  .\venv\Scripts\python.exe scripts\diag_strategy.py
  ```

  Prints remembered high-yield source URLs, venues with weak future coverage, recently crawled low-yield hosts, and repeated recent search queries for the active topic database.

- **Migrate legacy spreadsheet / JSON / images to MongoDB:**

  ```powershell
  .\venv\Scripts\python.exe -m agent migrate-mongodb
  ```

- **REST API** (required for the Angular UI — serves events and poster images from MongoDB):

  ```powershell
  .\venv\Scripts\python.exe -m agent api --host 127.0.0.1 --port 8765
  ```

  Set **`MONGODB_URI`** in `.env` and run the research pipeline at least once so events exist in the database. In dev, the Angular dev server proxies `/api/*` to this port (`web/proxy.conf.json`).

  | Endpoint | Purpose |
  |----------|---------|
  | `GET /api/{db}/events` | Event list for a topic — **next month only** (events dated today through today+30; the store keeps all future events, this query applies the display window). JSON with `generated` + `events`; `{db}` is the topic's MongoDB database name from `topics.json`, e.g. `bgc`. Responses are served from an **in-process cache** warmed after each pipeline run (see `docs/features/events-cache.md`; disable with `EVENTS_API_CACHE_ENABLED=false`) |
  | `POST /api/{db}/events/search` | Text search over the same display window — body `{ "query": "..." }`; returns `generated`, `events` (matched rows), and `searchQuery`. Searches `event`, `summary`, `tags`, and `venue.name` in MongoDB (no LLM). Used by the home-page search bar (`?search=` in the URL) |
  | `GET /api/{db}/events/spotlight[?limit=4&exclude=id1,id2]` | Up to four random events within the **same display window** as the main list (default 30 days) with an **event-specific** cached poster (`poster_quality` ≥ 2 — scored on read and backfilled for legacy rows) |
  | `GET /api/{db}/images/{image_id}` | Cached poster image bytes for an event (`image_id` from MongoDB) |
  | `GET /api/{db}/reports[?limit=50]` | Pipeline run reports (`datetime`, `llm_model`, `planner_temperature`, `searches`, `urls`, `changes`) — used by `/admin/reports` |
  | `GET /api/{db}/venues[?limit=50&skip=0]` | Venue records (`id`, `name`, `location`, `aliases`, `linkedEventCount`, …) — used by `/admin/venues` (50 per page max). Paginated in MongoDB with a `sort_name` index; linked-event counts are batched in one aggregation per page |
  | `GET /api/{db}/venues?all=true` | All venue records sorted by name for the admin delete reassignment dropdown — omits `linkedEventCount` (not needed for the picker) |
  | `GET /api/{db}/venues/{venue_id}` | Raw venue JSON for admin editing (`_id`, `name`, `location`, `aliases`, `linkedEventCount`) |
  | `GET /api/{db}/venues/{venue_id}/events` | Linked events for one venue (`events`: `id`, `eventName`, `date`, `url`) — used by the expandable **Events** column on `/admin/venues` |
  | `PUT /api/{db}/venues/{venue_id}` | Save edited venue JSON back to MongoDB |
  | `DELETE /api/{db}/venues/{venue_id}` | Delete a venue; body `{ "replacementVenueId": "..." }` reassigns linked events, or `{ "deleteLinkedEvents": true }` deletes them first |
  | `POST /api/admin/verify-password` | Admin gate — body `{ "password": "..." }`; checks `ADMIN_PASSWORD` in `.env` |
  | `POST /api/admin/run-once` | Trigger one full pipeline pass (same as `python -m agent run-once`); body `{ "password": "..." }`; returns `{ "ok": true, "message": "..." }` — used by **Run pipeline now** on `/admin` |
  | `POST /api/admin/run-targeted` | Trigger one full pipeline pass using a single admin-supplied DuckDuckGo phrase (skips planner LLM and venue templates); body `{ "password": "...", "query": "..." }`; returns `{ "ok": true, "message": "...", "query": "..." }` — used by **Targeted search** on `/admin` |
  | `POST /api/admin/dedupe-events` | Re-scan the active topic's `events` collection for duplicates without a new crawl; body `{ "password": "..." }`; runs deterministic dedupe on all rows, then LLM semantic dedupe when configured; returns `{ "ok": true, "message": "...", "removed_deterministic", "removed_semantic", "total_removed" }` — used by **Remove duplicate events** on `/admin` |
  | `GET /api/{db}/users[?limit=50&skip=0]` | Weekly email subscribers (`id`, `email`, `subscribed_at`) — used by `/admin/users` |
  | `GET /api/{db}/comments[?limit=50&skip=0]` | Visitor comments (`id`, `name`, `comment`, `date`) — used by `/admin/comments` |
  | `DELETE /api/{db}/comments/{id}` | Remove one visitor comment (admin cleanup) |
  | `GET /api/config` | Public UI flags — `{ "emailSignupEnabled": true/false, "googleAnalyticsMeasurementId": "G-…" \| null }` from `EMAIL_SIGNUP_ENABLED` and `GOOGLE_ANALYTICS_MEASUREMENT_ID` in `.env` |
  | `POST /api/{db}/users/subscribe` | Weekly email signup — body `{ "email": "..." }`; saves to the topic's `users` collection (403 when signup is disabled) |
  | `POST /api/{db}/comments` | Visitor feedback — body `{ "name": "...", "comment": "..." }`; saves to the topic's `comments` collection with a server-set UTC `date` |
  | `GET /robots.txt` | SEO — allows crawlers, blocks `/admin`, links the sitemap. Generated from the request host (no hard-coded domain); nginx proxies the site-root path here |
  | `GET /sitemap.xml` | SEO — sitemap of `/`, `/about`, plus one URL per distinct tag (`/tags/{tag}`) and venue (`/venues/{slug}`) in the active topic's display window |

  `{db}` accepts either the topic id or the raw database name. Example for the default topic: `http://127.0.0.1:8765/api/bgc/events`.

- **Venue migration** (link existing event venue strings to the `venues` collection):

  ```powershell
  .\venv\Scripts\python.exe -m agent migrate-venues
  ```

  Each topic database gets a `venues` collection (`name`, `location`, `aliases`). Events store a nested `venue` document `{ name, id }` linking to that collection; suburb/city lives on the venue record. Events also store a `tags` string array (up to three labels). After each merge, an LLM pass assigns tags to untagged rows, preferring tags already in the database. The Angular list page exposes a search bar (bookmarkable via `?search=`), tag filter pills, and venue filters.

- **Venue-first mining** (prioritise big venues' full gig lists): when a known venue is recognised in the search results, the crawl step finds that venue's own **"What's On"** page, mines it as a **priority seed** (following pagination so long listings are exploited exhaustively), and stores the link on the venue document as **`events_link`** (with `website` and `events_link_checked`). On later runs the stored `events_link` is reused directly — no need to rediscover it until it goes stale (`VENUE_EVENTS_LINK_TTL_DAYS`, default 30). Each venue also gets a **`last_event_date`** (the latest event date seen for it) so you can tell how far ahead its listings already reach. **Rotation:** remembered venues are mined **least-recently-first** (tracked per venue as **`last_mined`**, with a random tie-break), so coverage spreads across *all* known venues over successive runs instead of always re-crawling the same alphabetically-first few. **Discovery always runs** — even when the memory tier is full — so new venues keep getting linked and the rotation pool keeps growing (previously discovery stopped once you had `MAX_VENUE_SEEDS` linked venues, freezing the pool). Tune with `VENUE_MINING_ENABLED` (default `true`), `MAX_VENUE_SEEDS` (remembered venues reused per run, default 4), and `MAX_VENUE_DISCOVERIES_PER_RUN` (new venues discovered per run, default 3) in `.env`.

- **Fair crawl budget (round-robin)**: the bounded same-site crawl shares its page budget (`MAX_CRAWL_PAGES_TOTAL`, default 28) **across all seeds at once** — one page per seed per round — instead of draining one seed before starting the next. This stops the priority venue seeds from consuming the whole budget and starving the search-result seeds, so each run's actual search terms genuinely influence which pages get crawled (previously reports kept showing the same few venue hosts every run regardless of the search terms).

- **Smarter crawl page selection** (focus the budget on gigs): when the bounded same-site crawl expands links, pages that are essentially never music listings — `/cart`, `/checkout`, `/login`, `/my-account`, legal/privacy pages, and "win a competition" pages — are **dropped before they are enqueued** (whole-segment matching, so `/winery-sessions` is not mistaken for `/win`). Generic content pages (`/menu`, `/about`, `/gallery`, `/shop`, …) are kept but **de-prioritised** below event/ticket/whats-on pages, so a venue's `/ticketed-events` page is crawled ahead of its `/street-food-lineup`. This keeps the limited per-run page budget on pages likely to contain events. Disable with `CRAWL_SKIP_NON_EVENT_PAGES=false` in `.env`.

- **Targeted venue searches** (actively re-check known venues): each run the planner injects a random **3–6** queries built straight from the `venues` collection in the form **"What's on in {venue} in {location}, Australia"** (the `{location}` uses the venue's own stored location, falling back to a configured region such as Brisbane or Gold Coast). These targeted queries take priority over the LLM-generated ones — some generated queries are discarded so the total stays within `MAX_SEARCH_QUERIES` — then every event is read as usual. Configure the template, fallback regions, and 3–6 range per topic in `prompt_guides.yaml` (`venue_query_template`, `venue_query_locations`, `venue_query_min`, `venue_query_max`).

- **Smarter crawl page selection** (spend the page budget on gigs, not carts): when the bounded same-site crawl expands links, it now reads the **likely subject** of each page from its URL. Dedicated **event / ticket / what's-on** pages are crawled first; transactional, account, and legal pages (`/cart`, `/checkout`, `/login`, `/privacy`, the `/win` of a competition) and **food-only** pages (`/street-food-lineup`, `/menu`) are **skipped before they are fetched** — unless the same URL also names an event (e.g. `/food-and-live-music` is kept). Generic content pages (`/about`, `/news`, `/gallery`) are kept but crawled last. So for a find like Miami Marketta the crawler heads straight for `/ticketed-events` instead of wasting fetches on `/cart` and `/street-food-lineup`. Set `CRAWL_SKIP_NON_EVENT_PAGES=false` in `.env` to disable the hard-skip and fall back to ranking-only behaviour.

- **Venue tidy-up** (end of each run): after merge, exclusions, and dedupe, the pipeline deletes any venue in the `venues` collection that has **zero linked events** — e.g. when all its gigs were culled. The count appears on each run report as **orphan venues removed** so the admin venues page stays clean.

- **Schema migrations** (one-shot database changes before each pipeline run):

  Add numbered Python files under **`migrations/`** (e.g. `001_remove_poster_url.py`). Each file defines `MIGRATION_ID` and `run(db_name)`. Pending migrations run automatically at the start of every `serve` / `run-once` pass and are recorded in the topic database's `schema_migrations` collection. Delete migration files from the repo once they have been applied everywhere.

## Windows Task Scheduler (start at logon)

- Program: `...\venv\Scripts\python.exe`
- Arguments: `-m agent serve`
- Start in: project root

Or use `scripts\start_serve.ps1` after adjusting paths.

### Web UI (Angular)

The UI under **`web/`** is the public site **Gigsorooni**. It reads `topics/topics.json` for the active topic (MongoDB `db` name, etc.) and loads events from the **REST API** above — not from files on disk.

Run **two processes** in separate terminals:

```powershell
# Terminal 1 — API (MongoDB must be running; see MONGODB_URI in .env)
.\venv\Scripts\python.exe -m agent api --host 127.0.0.1 --port 8765

# Terminal 2 — Angular dev server
cd web
npm install
npm start
```

Then open the URL printed by the dev server (typically `http://localhost:4200/`). The **About** page (`/about`) walks through the LangGraph pipeline step by step — expandable panels for each stage, with PNG flow diagrams under `web/public/about/` (regenerate with `.\venv\Scripts\python.exe scripts\generate_about_diagrams.py` after installing Pillow). **Tag and venue filters are bookmarkable:** `/tags/rock` shows only rock-tagged gigs; `/venues/the-triffid` shows only that venue (slug from the venue name). Filtering is **instant and client-side** — the list is fetched once per topic and cached in the browser (`EventsStore`); changing filters only narrows the rows already in memory (no extra round-trip to MongoDB). Click an active filter again or use “show all gigs” to return to `/`. Use **Admin** in the nav (or `/admin`) for **Run pipeline now**, **Remove duplicate events**, a **targeted search** panel (one DuckDuckGo phrase, full pipeline), and links to pipeline reports, venue records, email subscribers, and visitor comments — you'll be prompted for the password set as **`ADMIN_PASSWORD`** in `.env` (stored in the browser's `sessionStorage` for the tab session). **Reports** (`/admin/reports`) lists past pipeline runs with expandable detail. The old `/reports` URL redirects to `/admin/reports`. If the API is not running, the browser console will show a proxy error (`ECONNREFUSED` on port 8765).

**Google Analytics 4 (optional):** set your Measurement ID in `.env`:

```env
GOOGLE_ANALYTICS_MEASUREMENT_ID=G-XXXXXXXXXX
```

The Angular app loads it from `GET /api/config` and records **page views only** (no custom events) via the official gtag.js script. Each SPA navigation sends one `page_view` with the router path and query string preserved (e.g. `/?search=beatles`, `/venues/the-triffid`). When the variable is blank, analytics is disabled silently. All tracking code lives in `web/src/app/analytics/analytics.service.ts`.

**Testing with GA4 DebugView:**

1. In [Google Analytics](https://analytics.google.com/) → **Admin** → your property → **DebugView**, leave the page open.
2. Install the [Google Analytics Debugger](https://chromewebstore.google.com/detail/google-analytics-debugger/jnkmfdileelhofjcijamephohjechhna) Chrome extension (or enable debug mode on your GA4 data stream).
3. Set `GOOGLE_ANALYTICS_MEASUREMENT_ID` in `.env`, restart the API, refresh the Angular app, and browse routes (`/`, `/tags/jazz`, `/venues/the-zoo`, `/?search=beatles`).
4. In DebugView you should see `page_view` events with `page_path` matching each URL. Duplicate navigations to the same URL should not produce a second event.

## Behavior

- Each successful run merges new curated resources into the topic's **`events`** collection (with deduplication, exclusion rules, poster caching into **`images`**, and optional LLM semantic dedupe).
- A run report is stored in MongoDB **`reports`** and includes planner queries, crawled URLs (grouped by host), merge statistics, the **LLM model** used for that run (from `OLLAMA_MODEL` when Ollama is enabled, or `OPENAI_MODEL` for OpenAI), and the **planner temperature** sampled for that run.
- The **planner** (search-query generation) draws a fresh temperature each run from `PLANNER_TEMPERATURE_MIN`–`PLANNER_TEMPERATURE_MAX` (default `0.0`–`1.0`) so query wording varies more across runs. Curator, tagging, exclusion, and semantic-dedupe calls stay at **temperature 0** — they need stable structured answers, not creative variation.
- If the **first LLM call** (planner) fails — for example a missing/wrong `OLLAMA_MODEL` — the run **aborts immediately** (`LLMInvocationError`). It does not continue with targeted venue searches alone, because later steps still need the same model.
- `data/<topic_id>/snapshot.json` stores a fingerprint of the current run for change detection (gitignored).

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests\ -q
```

## Layout

- `topics/` — registry (`topics.json`) plus per-topic YAML and UI assets.
- `src/agent/` — LangGraph workflow, DuckDuckGo search, MongoDB stores, REST API, scheduler.
- `migrations/` — numbered one-shot schema migrations applied at pipeline startup.
- `web/` — Angular app driven by `topics.json` and the MongoDB-backed REST API (`src/agent/api.py`).
- `data/` — per-topic snapshot files only (gitignored); events and images live in MongoDB.

## Digital Ocean
Removed from DO on the 6/7/'26. SImply didn;t need to be paying the $5/mo when I wasn't using it. I can test it all locally. 