# Event deduplication

Task 18 (`docs/tasks/18.md`): investigate how duplicates creep into the database and provide a way to fix them from the admin area without hand-editing MongoDB.

## Short answer

**The normal merge step does consider pre-existing records** — but only when **new** events arrive from a pipeline run. It does **not** re-scan the whole database for duplicates that already coexist. The optional **LLM semantic dedupe** pass *does* scan all existing rows, but only at the end of a successful pipeline run and only when an LLM backend is available.

That gap is how duplicates accumulate when the LLM pass is skipped or when two similar rows were inserted before rules caught them.

**Remedy implemented:** an admin button on **Reports** calls `POST /api/admin/dedupe-events`, which runs a full-database deterministic dedupe pass and then the LLM semantic pass when configured.

---

## How dedupe works today (each pipeline run)

At the end of every run, `write_output` in `src/agent/local_output.py` runs these steps in order:

1. **`merge_and_write`** — merge *this run's* new curator resources into MongoDB
2. **Event exclusions** — drop rows matching exclusion rules
3. **Event tagging** — LLM assigns tags (optional)
4. **Poster cache** — download thumbnails
5. **`run_llm_semantic_dedupe`** — LLM clusters same-day semantic duplicates (optional)
6. Venue tidy-up

### Step 1: `merge_and_write` (deterministic, always runs)

```text
Load ALL existing rows from MongoDB
  → drop past-dated rows
  → for each NEW resource from this run:
       compare against existing (+ rows added earlier in the same run)
       apply duplicate rules or insert
  → save
```

**Pre-existing records are in scope** for every comparison. When a new gig matches an old row (same act + date, or partial act + same venue + date), the new URL is merged into **Sources** instead of creating another row.

**What it does not do:** if two duplicate rows are *already* in the database and *no new ingest* triggers a match, this step leaves them alone.

### Step 5: `run_llm_semantic_dedupe` (LLM, conditional)

```text
Load ALL existing rows
  → send full list to LLM
  → merge clusters (same calendar day, same real-world event, different wording)
  → save
```

This **does** consider pre-existing records against each other. It catches duplicates deterministic rules miss (e.g. different venue wording, reordered titles).

**Gated by:**

- `config.llm_inference_enabled()` must be true
- Only runs inside `write_output` after a pipeline pass
- Failures are logged and skipped (`removed_dedupe: 0` on the report)

So if the LLM is down, misconfigured, or the planner aborts the run before `write_output`, **semantic dedupe never runs** and wording-only duplicates remain.

---

## Duplicate rules (deterministic)

| Rule | Match | Action |
|------|-------|--------|
| Re-ingest | Same URL **and** same act + date | Skip; may upgrade poster |
| Exact semantic | Same normalised act + same date (venue ignored) | Add URL to Sources if different domain |
| Partial act | One act name contains the other (min 4 chars) + same venue + same date | Keep longer act name; add URL to Sources |
| LLM semantic | Same day + same real-world event (judgment) | Merge rows; keep richest text/poster |

---

## Why duplicates still appear

| Cause | Example |
|-------|---------|
| LLM semantic pass skipped | Ollama/OpenAI down → only deterministic rules apply |
| No new ingest to trigger merge | Two dupes already in DB; next run finds nothing new to compare |
| Wording differs beyond deterministic rules | "Dead of Winter @ Mo's" vs "Dead of Winter Festival Band Comp @ Burleigh" — needs LLM pass |
| Same run, both rows added before index updated | Rare; `dedup_index` is updated as rows are added within one `merge_and_write` call |
| Historical data | Rows inserted before Task 13/14 dedupe improvements |

---

## Remedy: admin “Remove duplicates” button

### API

`POST /api/admin/dedupe-events` — same password gate as **Run pipeline now**.

Runs `run_dedupe_remediation(db)` on the **active topic's** MongoDB database:

1. **`run_deterministic_dedupe`** — full collection re-scan using the same exact + partial rules as merge (no new resources required)
2. **`run_llm_semantic_dedupe`** — when an LLM backend is configured

Response example:

```json
{
  "ok": true,
  "message": "Removed 3 duplicate event row(s) (2 deterministic, 1 semantic (LLM)).",
  "removed_deterministic": 2,
  "removed_semantic": 1,
  "total_removed": 3
}
```

### UI

**Admin → Reports** — **Remove duplicate events** button next to **Run pipeline now**. Does not run the full research pipeline; only dedupes the current database.

### When to use it

- After fixing LLM connectivity and wanting to clean up without a full crawl
- When reports show `removed_dedupe: 0` but the public list still has obvious duplicates
- After bulk imports or rule changes

Running **Run pipeline now** also attempts dedupe at the end of the merge, but only if the run completes through `write_output` and the LLM is up for the semantic pass. The dedicated dedupe button is faster and works even when you do not want a new crawl.

---

## Key files

| Area | Path |
|------|------|
| Merge + deterministic remediation | `src/agent/local_output.py` |
| LLM semantic clustering | `src/agent/semantic_dedupe.py` |
| Admin API | `src/agent/api.py` → `post_admin_dedupe_events` |
| Admin UI | `web/src/app/reports/reports.ts`, `reports.html` |
| Tests | `tests/test_dedupe_remediation.py`, `tests/test_semantic_dedupe.py` |

## Recommendation summary

| Question | Answer |
|----------|--------|
| Does merge consider pre-existing records? | **Yes**, when new events are ingested |
| Does merge re-scan existing-only dupes? | **No** |
| Does LLM dedupe consider pre-existing records? | **Yes**, all rows — but only after each pipeline run when LLM is available |
| Best fix for stranded duplicates? | **Admin dedupe button** (implemented) + keep LLM configured so automatic semantic pass runs after each successful pipeline |
