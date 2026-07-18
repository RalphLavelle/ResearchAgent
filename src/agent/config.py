"""Load settings from environment (never overwrite .env — document only)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from agent.exclusion_config import EventExclusionsConfig, load_event_exclusions
from agent.prompt_guides import load_prompt_guides
from agent.subject_config import SubjectConfig, load_subject_config
from agent.topics import (
    load_topics,
    resolve_output_dir,
    topic_config_dir,
    topic_data_dir,
)

# Load .env from project root when running as package
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def _get_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    if raw:
        return Path(raw).expanduser().resolve()
    return default


TOPICS_CONFIG_PATH = _get_path("TOPICS_CONFIG", _ROOT / "topics" / "topics.json")
TOPICS = load_topics(TOPICS_CONFIG_PATH)

_active_raw = (os.environ.get("ACTIVE_TOPIC") or "").strip()
ACTIVE_TOPIC_ID = _active_raw or TOPICS.active
if ACTIVE_TOPIC_ID not in TOPICS.topics:
    raise ValueError(
        f"Unknown active topic {ACTIVE_TOPIC_ID!r}. "
        f"Set ACTIVE_TOPIC in .env or fix topics.json (available: {list(TOPICS.topics)})."
    )
ACTIVE_TOPIC = TOPICS.topics[ACTIVE_TOPIC_ID]
TOPIC_DIR = topic_config_dir(_ROOT, ACTIVE_TOPIC_ID)

# Base data root — run reports and snapshots per topic id.
DATA_BASE_DIR = _get_path("DATA_DIR", _ROOT / "data")
DATA_DIR = topic_data_dir(DATA_BASE_DIR, ACTIVE_TOPIC_ID)

def parse_schedule_interval_hours(raw: str | None) -> float:
    """Parse ``SCHEDULE_INTERVAL_HOURS`` from .env (hours only; default 1)."""
    if raw is None or raw.strip() == "":
        return 1.0
    try:
        hours = float(raw.strip())
    except ValueError:
        return 1.0
    return max(0.05, hours)


SCHEDULE_INTERVAL_HOURS = parse_schedule_interval_hours(
    os.environ.get("SCHEDULE_INTERVAL_HOURS")
)

SUBJECT_MATTER_CONFIG_PATH = _get_path(
    "SUBJECT_MATTER_CONFIG",
    TOPIC_DIR / "subject_matter.yaml",
)

EVENT_EXCLUSIONS_CONFIG_PATH = _get_path(
    "EVENT_EXCLUSIONS_CONFIG",
    TOPIC_DIR / "exclusions.yaml",
)

# Loaded once at startup; all other modules import config.SUBJECT to read
# the prompts, queries, and labels for the current research topic.
SUBJECT: SubjectConfig = load_subject_config(SUBJECT_MATTER_CONFIG_PATH)
PROMPT_GUIDES = load_prompt_guides(TOPIC_DIR / "prompt_guides.yaml")
# Snapshot at import for introspection; ``exclusion_prune`` reloads this path each pass.
EVENT_EXCLUSIONS: EventExclusionsConfig = load_event_exclusions(
    EVENT_EXCLUSIONS_CONFIG_PATH
)
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"


def _env_flag(name: str, *, default: bool = True) -> bool:
    """Parse ``true``/``false`` style env vars; *unset* keeps ``default``."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Exactly one of these two flags should be True in .env.
# Neither defaults to True — you must explicitly pick a backend.
OPENAI_ENABLED = _env_flag("OPENAI_ENABLED", default=False)
OLLAMA_ENABLED = _env_flag("OLLAMA_ENABLED", default=False)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

OLLAMA_MODEL = (os.environ.get("OLLAMA_MODEL") or "qwen3.5:0.8b").strip()
OLLAMA_BASE_URL = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1").strip().rstrip("/")
OLLAMA_API_KEY = (os.environ.get("OLLAMA_API_KEY") or "ollama").strip()
OLLAMA_DISABLE_THINKING_TEMPLATE = _env_flag("OLLAMA_DISABLE_THINKING_TEMPLATE", default=True)
OLLAMA_EXTRA_BODY_JSON = os.environ.get("OLLAMA_EXTRA_BODY_JSON", "")

OLLAMA_THINKING_OFF_EXTRA_BODY: dict[str, dict[str, bool]] = {
    "chat_template_kwargs": {"enable_thinking": False},
}


def is_ollama_cloud() -> bool:
    """True when the Ollama backend targets a remote cloud service.

    Detected two ways:
    - The model tag contains ``cloud`` (e.g. ``kimi-k2.6:cloud``,
      ``gpt-oss:120b-cloud``).  The tag is the part after the last ``:``.
    - ``OLLAMA_BASE_URL`` points to a non-localhost host (e.g. ``https://ollama.com/v1``).

    When True, local-Ollama-specific parameters like thinking-template
    ``extra_body`` are skipped because cloud models don't support them.
    """
    from urllib.parse import urlparse

    host = (urlparse(OLLAMA_BASE_URL).hostname or "").lower()
    is_remote = host not in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "")
    # Tag is the portion after the last colon (e.g. "120b-cloud" or "cloud").
    tag = OLLAMA_MODEL.rsplit(":", maxsplit=1)[-1].lower() if ":" in OLLAMA_MODEL else ""
    is_cloud_model = "cloud" in tag
    return is_remote or is_cloud_model


def active_llm_model_label() -> str:
    if OPENAI_ENABLED:
        return OPENAI_MODEL
    if OLLAMA_ENABLED:
        return OLLAMA_MODEL
    return "(no backend enabled)"


def llm_inference_enabled() -> bool:
    """True when planner/curator paths should invoke the configured chat backend."""
    if OPENAI_ENABLED:
        return bool(OPENAI_API_KEY.strip())
    if OLLAMA_ENABLED:
        return True
    return False

# Run reports, snapshots: data/<topic_id>/ by default.
# Curated events + posters: MongoDB database named ACTIVE_TOPIC.db.
_output_raw = (os.environ.get("OUTPUT_DIR") or os.environ.get("AGENT_AI_DIR") or "").strip()
OUTPUT_DIR = resolve_output_dir(
    data_base=DATA_BASE_DIR,
    topic_dir=DATA_DIR,
    env_override=_output_raw or None,
)

# YouTube Data API v3 — server-side only (Task 6). Enable the API in Google Cloud
# and restrict the key to your API host. See docs/features/youtube.md.
YOUTUBE_API_KEY = (os.environ.get("YOUTUBE_API_KEY") or "").strip()

MONGODB_URI = (os.environ.get("MONGODB_URI") or "").strip()

# Password required to access the Angular admin section (checked via ``POST /api/admin/verify-password``).
ADMIN_PASSWORD = (os.environ.get("ADMIN_PASSWORD") or "").strip()

MAX_SEARCH_QUERIES = int(os.environ.get("MAX_SEARCH_QUERIES", "8"))


def _planner_temperature_bounds() -> tuple[float, float]:
    """Return ``(min, max)`` for the planner's per-run randomised temperature.

    Defaults to the usual creative range ``[0.0, 1.0]``. Set both
    ``PLANNER_TEMPERATURE_MIN`` and ``PLANNER_TEMPERATURE_MAX`` to the same
    value for a fixed temperature. Legacy ``PLANNER_TEMPERATURE`` still works
    as a fixed value when the min/max env vars are unset.
    """

    def _read(name: str) -> float | None:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            return None
        try:
            return float(str(raw).strip())
        except ValueError:
            return None

    lo = _read("PLANNER_TEMPERATURE_MIN")
    hi = _read("PLANNER_TEMPERATURE_MAX")
    fixed = _read("PLANNER_TEMPERATURE")
    if lo is None and hi is None and fixed is not None:
        lo = hi = fixed
    if lo is None:
        lo = 0.0
    if hi is None:
        hi = 1.0
    # OpenAI/Ollama accept roughly 0–2; clamp so a typo cannot blow up sampling.
    lo = max(0.0, min(2.0, lo))
    hi = max(0.0, min(2.0, hi))
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


PLANNER_TEMPERATURE_MIN, PLANNER_TEMPERATURE_MAX = _planner_temperature_bounds()
# Back-compat alias: midpoint of the configured range (used only in docs/tests).
PLANNER_TEMPERATURE = (PLANNER_TEMPERATURE_MIN + PLANNER_TEMPERATURE_MAX) / 2.0
SEARCH_DELAY_SEC = float(os.environ.get("SEARCH_DELAY_SEC", "1.5"))
# Higher default = more snippets per query for the curator to mine individual gigs from.
MAX_DDG_RESULTS_PER_QUERY = int(os.environ.get("MAX_DDG_RESULTS_PER_QUERY", "10"))

# Bounded same-origin crawl after DuckDuckGo (Task 6). Disable with CRAWL_ENABLED=false.
_crawl_raw = (os.environ.get("CRAWL_ENABLED") or "true").strip().lower()
CRAWL_ENABLED = _crawl_raw in ("1", "true", "yes", "on")
MAX_CRAWL_SEEDS = int(os.environ.get("MAX_CRAWL_SEEDS", "5"))
MAX_CRAWL_PAGES_TOTAL = int(os.environ.get("MAX_CRAWL_PAGES_TOTAL", "28"))
MAX_CRAWL_DEPTH = int(os.environ.get("MAX_CRAWL_DEPTH", "2"))
MAX_CRAWL_PAGES_PER_SEED = int(os.environ.get("MAX_CRAWL_PAGES_PER_SEED", "12"))
CRAWL_DELAY_SEC = float(os.environ.get("CRAWL_DELAY_SEC", "0.35"))

# Task 4: when expanding links during the crawl, drop clearly non-event pages
# (cart, checkout, login, legal, "win a competition", etc.) before they are
# enqueued so the bounded page budget is spent on gig/event/whats-on pages.
# Set CRAWL_SKIP_NON_EVENT_PAGES=false to fall back to the old behaviour.
CRAWL_SKIP_NON_EVENT_PAGES = _env_flag("CRAWL_SKIP_NON_EVENT_PAGES", default=True)

# Venue-first mining (Task 1): when a known venue is recognised in search
# results, the agent finds its "What's On" page, stores the link on the venue
# document, and mines that page (incl. pagination) as a top-priority seed on
# later runs so big venues are exploited exhaustively.
VENUE_MINING_ENABLED = _env_flag("VENUE_MINING_ENABLED", default=True)
# Max *remembered* venue "What's On" pages reused as priority crawl seeds per
# run. These rotate least-recently-mined-first (see venue_crawl) so coverage
# spreads across all known venues instead of repeating the same few each run.
MAX_VENUE_SEEDS = int(os.environ.get("MAX_VENUE_SEEDS", "4"))
# Max *new* venues whose "What's On" page is discovered each run. Discovery runs
# even when the memory seeds above are full, so the pool of linked venues keeps
# growing — which is what makes the rotation meaningful over time.
MAX_VENUE_DISCOVERIES_PER_RUN = int(os.environ.get("MAX_VENUE_DISCOVERIES_PER_RUN", "3"))
# Re-verify a stored events_link after this many days (0 = never re-check).
VENUE_EVENTS_LINK_TTL_DAYS = int(os.environ.get("VENUE_EVENTS_LINK_TTL_DAYS", "30"))

# Larger excerpts keep whole calendar/listing HTML text for the curator (was 7000 in code default).
CRAWL_MAX_TEXT_PER_PAGE = int(os.environ.get("CRAWL_MAX_TEXT_PER_PAGE", "38000"))

# Characters from search+crawl forwarded to curator (preserve crawl suffix when trimming).
# Lower default (was 260000): very large inputs make the curator emit a huge
# JSON list that can overflow a cloud model's output limit and get truncated.
CURATOR_INPUT_MAX_CHARS = int(os.environ.get("CURATOR_INPUT_MAX_CHARS", "180000"))
