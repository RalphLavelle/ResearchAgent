# Research Agent

Python app using **LangGraph** that searches for high-quality resources about building AI agents (books, ebooks, courses, websites), prefers **LangGraph** when a hit is framework-specific, and saves results as **Markdown** on your computer.

**Default output folder:** `Desktop/AgentAI` (your user profile’s Desktop). Override with **`OUTPUT_DIR`** or **`AGENT_AI_DIR`** in `.env`.

Files written:

| File | Purpose |
|------|--------|
| `agent_research.md` | Full curated list (rewritten when research results change) |
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

By default the app uses:

`%USERPROFILE%\Desktop\AgentAI`

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

- **Dry run** (no Markdown files, no snapshot file):

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

## Behavior

- If curated content **changed** since last run, **`agent_research.md`** is rewritten and a line is appended to **`run_log.md`**.
- If nothing **meaningfully** changed, only **`run_log.md`** gets a new line (no rewrite of the main file).
- `data/snapshot.json` stores a fingerprint (gitignored).

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests\ -q
```

## Layout

- `src/agent/` — LangGraph workflow, DuckDuckGo search, local Markdown writer, scheduler.
