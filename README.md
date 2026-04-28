# Research Agent

Python app using **LangGraph** that researches configurable topics, curates structured rows, and saves results to a **spreadsheet** plus generated HTML under **`data/`** (override with `.env`).

**Default output folder:** `data/` at the repo root (spreadsheet + logs + HTML). Override with **`OUTPUT_DIR`** or **`AGENT_AI_DIR`** in `.env`.

Files written:

| File | Purpose |
|------|--------|
| `agent_research.xlsx` | Spreadsheet source of truth (merge + dedupe each run) |
| `agent_research.html` | Rendered event table (same content the Angular app embeds) |
| `run_log.md` | Timestamped lines every run (append-only) |

Search uses LangChain’s **`DuckDuckGoSearchRun`** (via its `api_wrapper`) — no search API key.

## Setup

### 1. Python and install

Python 3.11+ and a virtualenv (`venv/`).

```powershell
.\venv\Scripts\pip.exe install -e ".[dev]"
```

### 2. OpenAI

Copy `env.example` to `.env` and set `OPENAI_API_KEY` (and optional `OPENAI_MODEL`).

### 3. Output folder (optional)

By default the app writes under:

`.\data\`

To use a different folder, set in `.env`:

```env
OUTPUT_DIR=D:\MyData\AgentAI
```

### 4. Schedule file (optional)

Copy `config/schedule.example.yaml` to `config/schedule.yaml` and set `interval_hours`. Edit while `serve` runs to change cadence without restarting.

## Commands

- **One shot:**

  ```powershell
  .\venv\Scripts\python.exe -m agent run-once
  ```

- **Dry run** (no spreadsheet / HTML / log writes):

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

The UI under **`web/`** embeds `data/agent_research.html` so you can browse the spreadsheet-backed table in the browser.

```powershell
cd web
npm install
npm start
```

Then open the URL printed by the dev server (typically `http://localhost:4200/`). Run the Python agent at least once so `data/agent_research.html` exists.

## Behavior

- If curated content **changed** since last run, the spreadsheet (and HTML) are updated and a line is appended to **`run_log.md`**.
- If nothing **meaningfully** changed, only **`run_log.md`** gets a new line (no rewrite of the main spreadsheet/HTML).
- `data/snapshot.json` stores a fingerprint (gitignored).

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests\ -q
```

## Layout

- `src/agent/` — LangGraph workflow, DuckDuckGo search, spreadsheet/HTML writer, scheduler.
- `web/` — Angular shell that embeds the generated HTML from `data/`.
