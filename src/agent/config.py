"""Load settings from environment (never overwrite .env — document only)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from agent.subject_config import SubjectConfig, load_subject_config

# Load .env from project root when running as package
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def _get_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    if raw:
        return Path(raw).expanduser().resolve()
    return default


SCHEDULE_CONFIG_PATH = _get_path(
    "SCHEDULE_CONFIG_PATH",
    _ROOT / "config" / "schedule.yaml",
)
DATA_DIR = _get_path("DATA_DIR", _ROOT / "data")

# Path to the active subject-matter YAML. Point SUBJECT_MATTER_CONFIG in
# your .env to a different file to research a completely different topic.
SUBJECT_MATTER_CONFIG_PATH = _get_path(
    "SUBJECT_MATTER_CONFIG",
    _ROOT / "config" / "subject_matter.yaml",
)

# Loaded once at startup; all other modules import config.SUBJECT to read
# the prompts, queries, and labels for the current research topic.
SUBJECT: SubjectConfig = load_subject_config(SUBJECT_MATTER_CONFIG_PATH)
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
# Last research fingerprint successfully pushed to Notion (under data/, gitignored).
NOTION_SYNC_STATE_PATH = DATA_DIR / "notion_sync_state.json"

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

# Spreadsheet, HTML, run_log: repo data/ by default (same as DATA_DIR). Override with OUTPUT_DIR or AGENT_AI_DIR.
_output_raw = (os.environ.get("OUTPUT_DIR") or os.environ.get("AGENT_AI_DIR") or "").strip()
if _output_raw:
    OUTPUT_DIR = Path(_output_raw).expanduser().resolve()
else:
    OUTPUT_DIR = DATA_DIR

MAX_SEARCH_QUERIES = int(os.environ.get("MAX_SEARCH_QUERIES", "8"))
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

# Larger excerpts keep whole calendar/listing HTML text for the curator (was 7000 in code default).
CRAWL_MAX_TEXT_PER_PAGE = int(os.environ.get("CRAWL_MAX_TEXT_PER_PAGE", "38000"))

# Characters from search+crawl forwarded to curator (preserve crawl suffix when trimming).
CURATOR_INPUT_MAX_CHARS = int(os.environ.get("CURATOR_INPUT_MAX_CHARS", "260000"))

# Optional: sync research to a Notion page.
# Create an internal integration, paste its secret; share the target page with that integration.
NOTION_ENABLED = (os.environ.get("NOTION_ENABLED") or "false").strip().lower()
NOTION_INTEGRATION_TOKEN = (os.environ.get("NOTION_INTEGRATION_TOKEN") or "").strip()
NOTION_RESEARCH_PAGE_ID = (os.environ.get("NOTION_RESEARCH_PAGE_ID") or "").strip()
NOTION_API_VERSION = (
    (os.environ.get("NOTION_API_VERSION") or "").strip() or "2022-06-28"
)


def notion_sync_configured() -> bool:
    """True when both token and page id are set (Notion sync runs on full writes)."""
    return bool(NOTION_INTEGRATION_TOKEN and NOTION_RESEARCH_PAGE_ID and NOTION_ENABLED == "true")
