# AIAgent — How It Works

## Overview

This is a **scheduled research agent** that automatically searches the web for upcoming live music events in the Gold Coast and Brisbane area, then saves the results to a local spreadsheet, an HTML file, and optionally a Notion page.

It runs on a timer (configurable in minutes or hours). Each run is a pipeline — a chain of small steps that takes a blank slate at one end and produces updated output files at the other.

---

## The Big Picture

```
[Timer fires]
     │
     ▼
  PLAN ──(LLM)──► generates search queries
     │
     ▼
  SEARCH ──(DuckDuckGo)──► raw text blobs
     │
     ▼
  CRAWL ──(httpx + BeautifulSoup)──► more raw text from the same websites
     │
     ▼
  NORMALIZE ──(LLM)──► structured list of events (Resource objects)
     │
     ▼
  ENRICH ──(httpx)──► add poster thumbnail URLs from Open Graph tags
     │
     ▼
  FINGERPRINT ──► SHA-256 hash of the event list; compare to last run
     │
     ▼
  OUTPUT ──► merge into spreadsheet (.xlsx)
          ──► write HTML file
          ──► sync to Notion (if configured)
```

Each box in this chain is called a **node**. The nodes are wired together by **LangGraph**, a library that manages the flow of state between them.

---

## How LangGraph Fits In

LangGraph is the scaffolding that holds the pipeline together. Think of it like a flowchart runner:

- You define **nodes** (functions that do one job each).
- You define **edges** (arrows connecting one node to the next).
- LangGraph compiles these into a **graph**, then `invoke`s it, passing a shared state dictionary from node to node.

The shared state object is `AgentState` — a Python dictionary-like structure that every node can read from and write to. As the pipeline progresses, the state fills up:

| After node | New state keys |
|---|---|
| `plan` | `queries` |
| `search` | `raw_search_text` |
| `crawl` | `raw_search_text` (extended) |
| `normalize` | `resources` |
| `enrich` | `resources` (with thumbnails added) |
| `fingerprint` | `fingerprint`, `fingerprint_unchanged` |
| `output` | `run_log_message` |

The graph is compiled once and invoked fresh on each scheduled run. There is no memory between runs except what is written to disk (the snapshot and spreadsheet).

---

## What the LLM Actually Does

The LLM is called at exactly **two points** in the pipeline. These are the parts that genuinely require intelligence, and where rule-based code would struggle badly.

### 1. PLAN — generating search queries

**Node:** `node_plan` in `graph_nodes.py`  
**Model:** GPT-4o (or whatever `OPENAI_MODEL` is set to in `.env`)  
**Output type:** `PlanQueries` — a Pydantic model with a `list[str]` of queries

The planner receives a system prompt and a user message, both loaded from `config/subject_matter.yaml`. It is asked to produce up to `MAX_SEARCH_QUERIES` (default 8) specific, varied search strings — things like `"Crowbar Brisbane tickets May 2026"` or `"open mic Gold Coast Thursday"`.

**Why an LLM?** Generating useful, varied, non-redundant search queries for a given topic requires understanding the topic — knowing the relevant venues, ticket vendors, event directories, and useful keywords. A fixed list of queries would quickly go stale and miss new angles. The LLM essentially acts as a domain expert who knows how to query for events.

### 2. NORMALIZE — extracting structured events from noisy text

**Node:** `node_normalize` in `graph_nodes.py`  
**Output type:** `ResourceListPayload` — a Pydantic model wrapping a `list[Resource]`

The normalizer receives up to 200,000 characters of raw messy text — DuckDuckGo search snippets, HTML stripped from crawled pages, link lines — and must turn it into a clean, structured list of events.

Each event (`Resource`) has:
- `title` — formatted as `"Act @ Venue, Location"` (e.g. `"The Beths @ The Tivoli, Brisbane"`)
- `url` — direct link to the ticket page or event detail
- `date` — ISO format (`2026-05-07`)
- `summary` — one sentence
- `participatory` — whether the audience can perform (open mic, jam, etc.)
- `thumbnail_url` — left empty here; filled in by the enrich step

**Why an LLM?** The raw text from DuckDuckGo and crawled pages is highly variable and messy. Consider what the code would have to handle without an LLM:
- Search snippets that mention an event buried in a paragraph of unrelated text
- Dates written as "this Friday", "May 7", "07/05/26", or "next week"
- Venue names that vary slightly across sources
- Pages that list dozens of events in free-form HTML
- Distinguishing "The Beths are playing at The Tivoli on May 7" from "The Tivoli — What's On (general listing)"
- Deciding which URL is the specific event page vs a generic venue homepage

None of these are trivial pattern-matching problems. The LLM reads the text the same way a human would and extracts the signal from the noise.

**Structured output** is crucial here. Rather than asking the LLM to write text back, the code uses `.with_structured_output(ResourceListPayload)` — this forces the model to return valid JSON that matches the Pydantic schema. If the shape is wrong, Pydantic raises an error immediately. This is what makes the LLM output usable as data rather than prose.

---

## What the Code Does Without the LLM

The rest of the pipeline — the majority of the code — requires no AI at all:

### Search (`node_search`)
Runs each query against DuckDuckGo using `run_searches()`, collects raw text snippets, and concatenates them. Pure HTTP calls.

### Crawl (`node_crawl`, `site_crawl.py`)
Takes promising URLs from the search text, fetches their HTML with `httpx`, strips the boilerplate (scripts, styles, nav) with `BeautifulSoup`, and follows internal links up to a configurable depth and page count. The output is more raw text for the normalizer to mine. No AI involved — just controlled web scraping.

### Enrich (`node_enrich`, `enrich.py`)
For each event URL, fetches the HTML and looks for `og:image` (Open Graph) meta tags. This is how poster thumbnails are collected. Pure HTTP + HTML parsing.

### Fingerprint (`node_fingerprint`, `snapshot.py`)
Computes a SHA-256 hash of the sorted list of `(url, title)` pairs. Compares it to the hash saved from the previous run. If they match, the data hasn't changed and Notion sync can be skipped.

### Spreadsheet output (`local_output.py`)
This module is the **source of truth** for events. It:
- Loads the existing `agent_research.xlsx` file
- Removes rows whose event date has passed
- Merges in new events (with deduplication — see below)
- Writes the result back

Deduplication happens at two levels:
1. **Exact URL** — if a URL is already in the sheet, the row is skipped
2. **Semantic** — if the same act name and date appear (regardless of venue text spelling), the new URL is added to a **Sources** column on the existing row rather than creating a duplicate row, but only if it comes from a different domain

The spreadsheet uses `openpyxl` and is saved atomically (written to a `.tmp` file first, then renamed) to avoid corruption if Excel has the file open.

### HTML output (`html_output.py`, `templates/event_table.html`)
Reads all rows from the spreadsheet (not just the current run's results) and renders them into `agent_research.html` using a user-editable HTML template. Tokens like `{{EVENT_NAME}}` and `{{DATE}}` are replaced in the template at render time. The template can be freely edited to change styling without touching Python.

### Notion sync (`notion_output.py`)
If credentials are configured, syncs the full event list to a Notion page. This works by:
1. Deleting all existing child blocks on the page
2. Posting new blocks: a heading, a "Generated" timestamp, a divider, and a native Notion table

The Notion table has three columns: Event (linked), Venue, Date. Because Notion table cells cannot contain images, a `🖼` glyph is prepended to the event name when a thumbnail exists.

---

## Configuration and Decoupling

One of the important design decisions is that the Python engine is **topic-agnostic**. Nothing in the code mentions music, Gold Coast, or Brisbane. All topic-specific content lives in `config/subject_matter.yaml`:

- The planner system prompt (what kind of queries to write)
- The planner user message (what to search for)
- The curator system prompt (how to filter and structure results)
- Output labels and titles

To research a completely different subject — say, upcoming art exhibitions — you only need to create a new YAML file and set `SUBJECT_MATTER_CONFIG` in `.env` to point to it. The engine runs identically.

Similarly, the scheduler interval lives in `config/schedule.yaml`, not in code. Minutes take priority over hours if both are set.

---

## Data Flow Summary

```
subject_matter.yaml
        │ (prompts)
        ▼
    LLM (plan) ──────────────► search queries
                                      │
                              DuckDuckGo API
                                      │
                              raw search text
                                      │
                              same-site crawl (httpx)
                                      │
                              extended raw text
                                      │
                            LLM (normalize) ──────► Resource objects
                                                          │
                                                  enrich thumbnails (httpx)
                                                          │
                                                  fingerprint (SHA-256)
                                                          │
                                             ┌────────────┴─────────────┐
                                             ▼                          ▼
                                    agent_research.xlsx         agent_research.html
                                    (accumulates across         (regenerated from
                                      all runs)                  spreadsheet)
                                             │
                                             ▼
                                    Notion page (optional,
                                    synced when spreadsheet
                                    fingerprint changes)
```

---

## File Map

| File | Role |
|---|---|
| `src/agent/workflow.py` | Assembles the LangGraph graph and exposes `run_once()` |
| `src/agent/graph_nodes.py` | One function per pipeline node; orchestrates everything |
| `src/agent/models.py` | Pydantic data models: `Resource`, `AgentState`, `PlanQueries`, `ResourceListPayload` |
| `src/agent/config.py` | Loads `.env` + `subject_matter.yaml` into module-level constants |
| `src/agent/subject_config.py` | Pydantic model for the YAML; validates it at startup |
| `src/agent/site_crawl.py` | Bounded same-origin web crawler |
| `src/agent/local_output.py` | Spreadsheet read/write, merge-and-expire, deduplication |
| `src/agent/html_output.py` | HTML rendering from the editable template |
| `src/agent/notion_output.py` | Notion REST API integration |
| `src/agent/snapshot.py` | SHA-256 fingerprinting and snapshot persistence |
| `src/agent/event_window.py` | Date parsing, 30-day window filtering, title splitting |
| `src/agent/display_time.py` | Local-timezone "Generated:" timestamp formatting |
| `src/agent/scheduler.py` | APScheduler wrapper; reads interval from `schedule.yaml` |
| `src/agent/cli.py` | Command-line entry points |
| `config/subject_matter.yaml` | All topic-specific text and LLM prompts |
| `config/schedule.yaml` | Scheduler interval (minutes or hours) |
| `templates/event_table.html` | User-editable HTML template for the output file |
