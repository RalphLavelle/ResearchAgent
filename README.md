# Research Agent

Python app using **LangGraph** that researches **configurable topics**, curates structured rows, and saves results to a **spreadsheet** plus **`events.json`** under **`data/<topic>/`**.

**Topics registry:** `topics/topics.json` — the `active` id selects prompts, exclusions, schedule, UI chrome, and the data subfolder name.

**Default output folder:** `data/<data_dir>/` for the active topic (e.g. `data/live-music-brisbane-gold-coast/`). Override with **`OUTPUT_DIR`** or **`AGENT_AI_DIR`** in `.env`.

Files written (per active topic):

| File | Purpose |
|------|--------|
| `agent_research.xlsx` | Spreadsheet source of truth (merge + dedupe each run) |
| `events.json` | JSON feed consumed by the Angular UI (spreadsheet-derived) |
| `Run_<AEST>.md` | One per run: planner queries, crawled URLs (grouped by host), and curated `Resource` records |

Search uses LangChain's **`DuckDuckGoSearchRun`** (via its `api_wrapper`) — no search API key.

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

### 2. LLM backend

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

### 3. Topics (optional)

The bundled topic **Live music in Brisbane and the Gold Coast** lives under `topics/live-music-brisbane-gold-coast/` (`subject_matter.yaml`, `prompt_guides.yaml`, `exclusions.yaml`, `schedule.yaml`, `assets/`).

To add a topic, use the **topic-creator** skill (`.cursor/skills/topic-creator/SKILL.md`) or copy the live-music folder and register a new entry in `topics.json`.

If you previously wrote to a flat `data/` folder, the agent **auto-migrates** those files into `data/<data_dir>/` on startup (when the topic subfolder has no `events.json` yet). You can keep `OUTPUT_DIR=data` in `.env` — it now resolves to the active topic subfolder.

### 4. Output folder (optional)

By default the app writes under:

`.\data\<active-topic-data_dir>\`

To pin a custom folder, set in `.env`:

```env
OUTPUT_DIR=D:\MyData\AgentAI
```

### 5. Schedule file (optional)

Edit `topics/<active-topic>/schedule.yaml` (interval settings). The `serve` command reloads it without restarting.

## Commands

- **One shot:**

  ```powershell
  .\venv\Scripts\python.exe -m agent run-once
  ```

- **Dry run** (no spreadsheet / JSON / log writes):

  ```powershell
  .\venv\Scripts\python.exe -m agent run-once --dry-run
  ```

- **Scheduled mode:**

  ```powershell
  .\venv\Scripts\python.exe -m agent serve
  ```

## Windows Task Scheduler (start at logon)

- Program: `...\venv\Scripts\python.exe`
- Arguments: `-m agent serve`
- Start in: project root

Or use `scripts\start_serve.ps1` after adjusting paths.

### Web UI (Angular)

The UI under **`web/`** reads `topics/topics.json` for the active topic (title, background, data path) and loads `data/<topic>/events.json`.

```powershell
cd web
npm install
npm start
```

Then open the URL printed by the dev server (typically `http://localhost:4200/`). Run the Python agent at least once so `data/<topic>/events.json` exists.

## Behavior

- Each successful run produces a fresh **`Run_<AEST timestamp>.md`** report under the output folder. The report has three sections — *Searches* (planner queries), *Search and crawl* (URLs grouped by host), *Normalize* (curated `Resource` JSON with source URLs) — so you can audit exactly what each LLM-driven step did.
- If curated content **changed** since last run, the spreadsheet, `events.json`, and (when configured) Notion are also refreshed.
- If nothing **meaningfully** changed, the spreadsheet and `events.json` are still rewritten so they always reflect the latest source-of-truth, but downstream Notion sync is skipped.
- `data/<topic>/snapshot.json` stores a fingerprint (gitignored).

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests\ -q
```

## Layout

- `topics/` — registry (`topics.json`) plus per-topic YAML, schedule, and UI assets.
- `src/agent/` — LangGraph workflow, DuckDuckGo search, spreadsheet + JSON outputs, scheduler.
- `web/` — Angular shell driven by `topics.json` and `data/<topic>/events.json`.
