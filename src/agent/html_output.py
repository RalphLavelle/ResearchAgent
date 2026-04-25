"""Render the event HTML table from the user-editable template file.

The template lives at ``templates/event_table.html`` (project root).  Edit
that file freely to change the look of the output — the tokens below are
the only parts the app replaces at runtime.

Top-level tokens (replaced once per render):
    {{TITLE}}       — output title from subject_matter.yaml
    {{GENERATED}}   — local-timezone timestamp
    {{ROWS}}        — all rendered event rows inserted here

Row tokens (replaced once per event, between ROW_TEMPLATE_START/END):
    {{IMAGE_CELL}}  — pre-rendered <a><img/></a> when a poster exists, else ""
    {{EVENT_URL}}   — direct link to the event / ticket page
    {{EVENT_NAME}}  — act or event name
    {{VENUE}}       — venue + suburb/city joined (e.g. "The Tivoli, Brisbane")
    {{DATE}}        — formatted date string (e.g. "Wed 7 May 2026")
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path

from agent import config
from agent.display_time import format_generated_timestamp
from agent.event_window import (
    format_event_weekday_date,
    sort_resources_by_event_date_asc,
    split_title_parts,
)
from agent.models import Resource

logger = logging.getLogger(__name__)

# Template is at the project root (two levels above src/agent/).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = _PROJECT_ROOT / "templates" / "event_table.html"
HTML_FILENAME = "agent_research.html"

_ROW_START_MARKER = "<!-- ROW_TEMPLATE_START -->"
_ROW_END_MARKER = "<!-- ROW_TEMPLATE_END -->"


# ── Template loading ──────────────────────────────────────────────────────────


def _load_template() -> tuple[str, str]:
    """Read the template and split it into (outer, row_template).

    The row template is the text between the ROW_TEMPLATE_START/END comment
    markers.  The outer template has ``{{ROWS}}`` where those markers were.

    Raises:
        FileNotFoundError: Template file is missing.
        ValueError: Markers are absent or malformed.
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"HTML template not found: {TEMPLATE_PATH}\n"
            "The file should be at templates/event_table.html in the project root."
        )
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    m = re.search(
        rf"{re.escape(_ROW_START_MARKER)}(.*?){re.escape(_ROW_END_MARKER)}",
        source,
        re.DOTALL,
    )
    if not m:
        raise ValueError(
            f"Template is missing {_ROW_START_MARKER} / {_ROW_END_MARKER} markers. "
            "Restore them from the original templates/event_table.html."
        )
    row_template = m.group(1)
    # Replace the whole marker+content block in the outer template with {{ROWS}}
    outer = source[: m.start()] + "{{ROWS}}" + source[m.end() :]
    # Also remove the large documentation comment block (everything after {{ROWS}})
    outer = re.sub(r"\n<!--\n\s*═+.*?═+\n.*?-->\n", "", outer, flags=re.DOTALL)
    return outer, row_template


# ── Row rendering ─────────────────────────────────────────────────────────────


def _image_cell(event_url: str, image_url: str) -> str:
    """Render the poster thumbnail anchor, or empty string when no image exists."""
    if not image_url:
        return ""
    esc_url = html.escape(event_url, quote=True)
    esc_img = html.escape(image_url, quote=True)
    return (
        f'<a href="{esc_url}">'
        f'<img class="poster" src="{esc_img}" alt="poster" />'
        f"</a>"
    )


def _render_row(row_tmpl: str, r: Resource) -> str:
    """Fill all tokens in the row template for one event resource."""
    act, venue, location = split_title_parts(r.title or "")
    venue_str = ", ".join(filter(None, [venue, location]))
    url = (r.url or "").strip()
    img = (r.thumbnail_url or "").strip()
    date_str = format_event_weekday_date(r.date)

    summary = (r.summary or "").strip()

    row = row_tmpl
    row = row.replace("{{IMAGE_CELL}}", _image_cell(url, img))
    row = row.replace("{{EVENT_URL}}", html.escape(url, quote=True))
    row = row.replace("{{EVENT_NAME}}", html.escape(act or "—"))
    row = row.replace("{{VENUE}}", html.escape(venue_str))
    row = row.replace("{{SUMMARY}}", html.escape(summary))
    row = row.replace("{{DATE}}", html.escape(date_str))
    return row


# ── Public API ────────────────────────────────────────────────────────────────


def render_html(resources: list[Resource]) -> str:
    """Render the full HTML page from the template and event data."""
    outer, row_tmpl = _load_template()
    resources = sort_resources_by_event_date_asc(list(resources))

    if resources:
        rows_html = "".join(_render_row(row_tmpl, r) for r in resources)
    else:
        rows_html = (
            '      <tr>'
            '<td colspan="5" style="text-align:center;color:#888;padding:2rem">'
            "No gigs found in the current 30-day window."
            "</td></tr>\n"
        )

    page = outer
    page = page.replace("{{TITLE}}", html.escape(config.SUBJECT.output_title))
    page = page.replace("{{GENERATED}}", html.escape(format_generated_timestamp()))
    page = page.replace("{{ROWS}}", rows_html)
    return page


def write_html(resources: list[Resource]) -> Path:
    """Write ``agent_research.html`` to the configured output directory.

    Returns the path that was written so callers can log it.
    """
    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / HTML_FILENAME
    try:
        path.write_text(render_html(resources), encoding="utf-8")
        logger.info("HTML output written: %s", path)
    except Exception as exc:
        logger.error("Failed to write HTML output to %s: %s", path, exc)
        raise
    return path
