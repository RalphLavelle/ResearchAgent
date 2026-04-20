---
name: LangGraph Local Markdown Research Agent
overview: Build a Python app using LangGraph that researches high-quality “how to build an AI agent” resources, prefers LangGraph when framework-specific, saves results as Markdown under Desktop/AgentAI (or OUTPUT_DIR), with configurable schedule and fingerprint-based skip of main file rewrite when unchanged.
todos: []
isProject: false
---

# LangGraph + local Markdown (from [plan.md](plan.md))

## Output

- **Folder:** `Desktop/AgentAI` by default (`OUTPUT_DIR` / `AGENT_AI_DIR` optional).
- **`agent_research.md`** — full curated list when fingerprint changes.
- **`run_log.md`** — append-only run lines.
- **`data/snapshot.json`** — fingerprint (gitignored).

## Stack

LangGraph, DuckDuckGo (LangChain `DuckDuckGoSearchRun`), OpenAI, APScheduler, optional reloadable `config/schedule.yaml`.

## Configuration

`OPENAI_API_KEY`, optional `OUTPUT_DIR`, search tuning vars — see [README.md](../README.md).
