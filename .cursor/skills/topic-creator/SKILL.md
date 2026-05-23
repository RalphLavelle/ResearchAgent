---
name: topic-creator
description: Scaffold a new research topic in topics/topics.json and topics/<id>/ with subject_matter.yaml, exclusions.yaml, schedule.yaml, assets, and data subfolder wiring. Use when the user asks to add a topic, switch subject matter, or create topic configuration.
---

# Topic creator

Add a new entry to the multi-topic research pipeline. Each topic owns its YAML prompts, exclusions, schedule, UI assets, and a **`data/<data_dir>/`** output subfolder.

## When to use

- User asks for a new research topic or subject matter
- User wants to duplicate the live-music setup for another region or domain
- User mentions `topics.json`, `ACTIVE_TOPIC`, or per-topic config

## Steps (in order)

1. **Choose an id** — kebab-case slug from the display name (e.g. `Live music in Sydney` → `live-music-sydney`). Use `agent.topics.slugify_topic_id` or equivalent kebab-case rules.

2. **Create the folder** — `topics/<id>/` with:
   - `subject_matter.yaml` — copy from `topics/live-music-brisbane-gold-coast/subject_matter.yaml` and rewrite prompts for the new domain (planner + curator). Keep engine-neutral field names.
   - `prompt_guides.yaml` — copy from the live-music topic or `topics/_template/prompt_guides.example.yaml`. Holds **engine-injected** fragments (date-window geography/priority, resource labels). Generic ISO window logic stays in Python.
   - `exclusions.yaml` — copy from an existing topic or start with empty `drop_terms: []` and `exclusions: []`.
   - `schedule.yaml` — copy from an existing topic or `topics/_template/schedule.example.yaml`.
   - `assets/bg.jpg` — topic background (copy a suitable image or reuse `web/public/bg.jpg` as placeholder).

3. **Register in `topics/topics.json`** — add under `"topics"`:
   ```json
   "<id>": {
     "name": "<Human-readable title>",
     "data_dir": "<id>",
     "background_image": "/topics/<id>/assets/bg.jpg",
     "site_title": "<Short nav label>",
     "site_emoji": "🎵",
     "home_heading": "<H1 on home page>"
   }
   ```
   Set `"active": "<id>"` when the user wants this topic to run immediately.

4. **Data folder** — pipeline writes to `data/<data_dir>/` by default. No manual folder required; the agent creates it on first run. If migrating flat `data/` files, move spreadsheet, `events.json`, `Run_*.md`, `images/`, and `snapshot.json` into `data/<data_dir>/`.

5. **Environment** — optional overrides in `.env`:
   - `ACTIVE_TOPIC=<id>` — override `topics.json` active without editing JSON
   - `OUTPUT_DIR=` — remove or point at `data/<data_dir>/` if a legacy flat path was set
   - Per-file overrides still work: `SUBJECT_MATTER_CONFIG`, `EVENT_EXCLUSIONS_CONFIG`, `SCHEDULE_CONFIG_PATH`

6. **Verify**
   - Python: `venv\Scripts\python.exe -m pytest tests/test_topics.py -q`
   - Agent: `venv\Scripts\python.exe -m agent run-once --dry-run` (checks LLM + topic YAML load)
   - Web: `cd web && npm start` — header, home heading, background, and `data/<data_dir>/events.json` should match the new topic after a pipeline run

## Do not

- Hard-code region names in `src/agent/` Python — keep domain text in YAML only
- Overwrite the user's `.env` without asking
- Delete existing topic folders unless the user explicitly requests it

## Reference layout

```
topics/
  topics.json
  live-music-brisbane-gold-coast/
    subject_matter.yaml
    prompt_guides.yaml
    exclusions.yaml
    schedule.yaml
    assets/bg.jpg
  _template/
    schedule.example.yaml
data/
  live-music-brisbane-gold-coast/
    agent_research.xlsx
    events.json
    Run_*.md
    images/
```
