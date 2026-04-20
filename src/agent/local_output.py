"""Write curated resources to a Markdown file under the desktop AgentAI folder."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from agent import config
from agent.models import Resource

logger = logging.getLogger(__name__)

RESEARCH_FILENAME = "agent_research.md"
RUN_LOG_FILENAME = "run_log.md"


def output_directory() -> Path:
    """Desktop/AgentAI by default; override with OUTPUT_DIR or AGENT_AI_DIR in .env."""
    return config.OUTPUT_DIR


def format_markdown(resources: list[Resource]) -> str:
    """Build main research document body."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# AI agent learning resources",
        "",
        f"*Generated: {ts}*",
        "",
        "Curated books, ebooks, courses, and websites for learning how to build AI agents. "
        "When a source is framework-specific, only **LangGraph** is included per project rules.",
        "",
        "---",
        "",
    ]
    for i, r in enumerate(resources, 1):
        lines.append(f"## {i}. {r.title}")
        lines.append("")
        lines.append(f"- **Link:** [{r.url}]({r.url})")
        lines.append(f"- **Type:** {r.resource_type.value}")
        lines.append(f"- **Price:** {r.price}")
        if r.langgraph_specific:
            lines.append("- **LangGraph-specific:** yes")
        if r.summary:
            lines.append(f"- **Summary:** {r.summary}")
        if r.thumbnail_url:
            lines.append(f"- **Thumbnail:** {r.thumbnail_url}")
        lines.append("")
    return "\n".join(lines)


def write_output(
    resources: list[Resource],
    *,
    append_log_only: bool,
    log_line: str,
) -> None:
    """
    If append_log_only: append log_line to run_log.md only.
    Else: rewrite agent_research.md with resources; append log_line to run_log.md.
    """
    out_dir = output_directory()
    out_dir.mkdir(parents=True, exist_ok=True)

    research_path = out_dir / RESEARCH_FILENAME
    log_path = out_dir / RUN_LOG_FILENAME

    if append_log_only:
        block = f"\n- {log_line}\n"
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        if not existing.strip():
            log_path.write_text(
                "# Run log\n\n" + block.lstrip(),
                encoding="utf-8",
            )
        else:
            log_path.write_text(existing.rstrip() + block, encoding="utf-8")
        logger.info("Appended run log to %s", log_path)
        return

    body = format_markdown(resources)
    research_path.write_text(body, encoding="utf-8")
    logger.info("Wrote research to %s", research_path)

    block = f"\n- {log_line}\n"
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    if not existing.strip():
        log_path.write_text("# Run log\n\n" + block.lstrip(), encoding="utf-8")
    else:
        log_path.write_text(existing.rstrip() + block, encoding="utf-8")
    logger.info("Appended run line to %s", log_path)
