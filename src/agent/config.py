"""Load settings from environment (never overwrite .env — document only)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

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
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# Markdown output: Desktop/AgentAI by default. Override with OUTPUT_DIR or AGENT_AI_DIR.
_output_raw = (os.environ.get("OUTPUT_DIR") or os.environ.get("AGENT_AI_DIR") or "").strip()
if _output_raw:
    OUTPUT_DIR = Path(_output_raw).expanduser().resolve()
else:
    OUTPUT_DIR = Path.home() / "Desktop" / "AgentAI"

MAX_SEARCH_QUERIES = int(os.environ.get("MAX_SEARCH_QUERIES", "6"))
SEARCH_DELAY_SEC = float(os.environ.get("SEARCH_DELAY_SEC", "1.5"))
MAX_DDG_RESULTS_PER_QUERY = int(os.environ.get("MAX_DDG_RESULTS_PER_QUERY", "5"))
