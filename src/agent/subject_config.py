"""Load and validate the subject-matter YAML configuration.

The SubjectConfig model captures everything that is specific to a research
topic: prompts, queries, and output labels. The Python engine imports this
at startup and uses it everywhere, so changing the YAML file (or pointing
SUBJECT_MATTER_CONFIG in .env to a different file) changes the topic
without touching any engine code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class SubjectConfig(BaseModel):
    """All domain-specific text for one research topic.

    Each field maps directly to a key in the subject_matter.yaml file.
    Pydantic validates the types and raises a clear error if the YAML is
    missing a required field — much friendlier than a raw KeyError.

    Unknown keys (e.g. legacy ``resource_types``) are ignored so older YAML files
    still load after slimming ``Resource``.
    """

    model_config = ConfigDict(extra="ignore")

    # Short label shown in log lines and run summaries.
    topic: str

    # Heading and introductory paragraph written into the Markdown output.
    output_title: str
    output_description: str

    # ── LLM prompts ───────────────────────────────────────────────────────────
    # These are the exact strings passed to the language model.
    planner_system_prompt: str
    planner_user_message: str
    curator_system_prompt: str

    # default_queries removed (Task 8): if the LLM is unavailable the pipeline
    # logs an error and returns empty rather than silently falling back.


def load_subject_config(path: Path) -> SubjectConfig:
    """Read a YAML file and return a validated SubjectConfig.

    Args:
        path: Absolute or relative path to the YAML configuration file.

    Returns:
        A fully validated SubjectConfig instance.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If the YAML is missing required fields.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Subject-matter config not found: {path}\n"
            "Set SUBJECT_MATTER_CONFIG in .env to the correct path, "
            "or add a topic under topics/ and set active in topics/topics.json."
        )

    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)  # safe_load prevents arbitrary Python execution
    return SubjectConfig(**data)
