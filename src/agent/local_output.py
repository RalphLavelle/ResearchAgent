"""Write curated events to a persistent .xlsx spreadsheet and run_log.md.

Key behaviours (Task 8 + 9):
- Output is ``agent_research.xlsx``.
- Columns: Event, Venue, Location, Date, URL, Poster URL, Summary, Added.
  (Title format "Act @ Venue, Location" is split by split_title_parts().)
- The spreadsheet **accumulates**: existing rows are merged, past events
  are removed, new events are added. Nothing is overwritten by new LLM text.
- ``run_log.md`` is unchanged.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from agent import config
from agent.display_time import format_generated_timestamp
from agent.event_window import (
    parse_event_sort_date,
    split_title_parts,
    utc_today,
)
from agent.models import Resource

logger = logging.getLogger(__name__)

RESEARCH_FILENAME = "agent_research.xlsx"
RUN_LOG_FILENAME = "run_log.md"

# ── Schema ────────────────────────────────────────────────────────────────────
# Column names in display order.  The Date cell stores a Python date object
# so Excel can sort natively; the number format renders it as "Wed 7 May 2026".
_COLS = ["Event", "Venue", "Location", "Date", "URL", "Poster URL", "Summary", "Added"]
_DATE_FORMAT = "ddd d mmm yyyy"

# 0-based indices into _COLS (used for row list access)
_IDX_EVENT    = 0
_IDX_VENUE    = 1
_IDX_LOCATION = 2
_IDX_DATE     = 3
_IDX_URL      = 4
_IDX_POSTER   = 5
_IDX_SUMMARY  = 6
_IDX_ADDED    = 7

# Column widths (0 = hide the column)
_COL_WIDTHS = [30, 22, 16, 18, 55, 0, 40, 20]


def output_directory() -> Path:
    """Desktop/AgentAI by default; override with OUTPUT_DIR or AGENT_AI_DIR in .env."""
    return config.OUTPUT_DIR


# ── Row ↔ Resource conversion ─────────────────────────────────────────────────


def _resource_to_row(r: Resource) -> list:
    """Convert a Resource to a list matching ``_COLS`` order."""
    act, venue, location = split_title_parts(r.title or "")
    raw_date = parse_event_sort_date(r.date)  # Python date or None
    return [
        act or "—",                           # Event
        venue,                                # Venue
        location,                             # Location
        raw_date,                             # Date (Excel date cell)
        (r.url or "").strip(),                # URL
        (r.thumbnail_url or "").strip(),      # Poster URL (hidden)
        (r.summary or "").strip(),            # Summary
        format_generated_timestamp(),         # Added
    ]


def _row_date(row: list) -> date | None:
    """Extract the date from a row; normalise datetime → date."""
    val = row[_IDX_DATE] if len(row) > _IDX_DATE else None
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def _row_to_resource(row: list) -> Resource:
    """Reconstruct a Resource from a spreadsheet row.

    The title is rebuilt in "Act @ Venue, Location" format so that
    ``split_title_parts`` in html_output / notion_output can decompose it
    the same way it was originally split.
    """
    act      = str(row[_IDX_EVENT]    or "").strip()
    venue    = str(row[_IDX_VENUE]    or "").strip()
    location = str(row[_IDX_LOCATION] or "").strip()
    url      = str(row[_IDX_URL]      or "").strip()
    poster   = str(row[_IDX_POSTER]   or "").strip()
    summary  = str(row[_IDX_SUMMARY]  or "").strip()
    raw_date = _row_date(row)

    # Reassemble "Act @ Venue, Location"
    venue_loc = ", ".join(filter(None, [venue, location]))
    if venue_loc:
        title = f"{act} @ {venue_loc}"
    else:
        title = act

    return Resource(
        title=title,
        url=url,
        date=raw_date.isoformat() if raw_date else "",
        thumbnail_url=poster or None,
        summary=summary,
    )


def load_spreadsheet_resources(path: Path | None = None) -> list[Resource]:
    """Read the current spreadsheet and return its rows as Resource objects.

    This is the public API that other modules (html_output, notion_output)
    should use to get the **source-of-truth** event list rather than using
    whatever the LLM returned in the current run.

    Args:
        path: Override the default spreadsheet path (mainly for tests).
    """
    if path is None:
        path = output_directory() / RESEARCH_FILENAME
    rows = _load_existing_rows(path)
    return [_row_to_resource(row) for row in rows.values()]


# ── Spreadsheet I/O ───────────────────────────────────────────────────────────


def _load_existing_rows(path: Path) -> dict[str, list]:
    """Load the spreadsheet as an ordered dict keyed by URL (lowercase).

    Expects the current header (``_COLS``). If the file uses a different layout,
    logs a warning and returns no rows so a fresh workbook can be written.
    """
    rows: dict[str, list] = {}
    if not path.exists():
        return rows
    try:
        wb = load_workbook(path)
        ws = wb.active
        header = [str(c.value or "") for c in next(ws.iter_rows(min_row=1, max_row=1))]
        try:
            col_index = {name: header.index(name) for name in _COLS}
        except ValueError:
            logger.warning(
                "Spreadsheet is missing one or more expected columns %s; "
                "ignoring existing file (header: %s).",
                _COLS,
                header,
            )
            return rows

        url_col = col_index["URL"]
        for raw_row in ws.iter_rows(min_row=2, values_only=True):
            if url_col >= len(raw_row):
                continue
            url_key = str(raw_row[url_col] or "").strip().lower()
            if not url_key.startswith("http"):
                continue
            row_list = [
                raw_row[col_index[c]] if col_index[c] < len(raw_row) else None
                for c in _COLS
            ]
            rows[url_key] = row_list
    except Exception as exc:
        logger.warning("Could not read existing spreadsheet (%s); starting fresh.", exc)
    return rows


def _write_workbook(path: Path, rows: dict[str, list]) -> None:
    """Sort by date and save all rows to the spreadsheet file."""
    def sort_key(row: list) -> date:
        d = _row_date(row)
        return d if d is not None else date.max

    sorted_rows = sorted(rows.values(), key=sort_key)

    wb = Workbook()
    ws = wb.active
    ws.title = "Events"

    # Header row
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(fill_type="solid", fgColor="2E4057")
    for col_i, name in enumerate(_COLS, start=1):
        cell = ws.cell(row=1, column=col_i, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Data rows
    for row_i, row_vals in enumerate(sorted_rows, start=2):
        for col_i, value in enumerate(row_vals[: len(_COLS)], start=1):
            cell = ws.cell(row=row_i, column=col_i, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            # Date column: apply number format so Excel renders it as "Wed 7 May 2026"
            if col_i == _IDX_DATE + 1 and isinstance(value, (date, datetime)):
                cell.number_format = _DATE_FORMAT
            # URL column: hyperlink styling
            elif col_i == _IDX_URL + 1 and str(value or "").startswith("http"):
                cell.hyperlink = str(value)
                cell.font = Font(color="0563C1", underline="single")

    # Column widths / visibility
    for col_i, w in enumerate(_COL_WIDTHS, start=1):
        letter = get_column_letter(col_i)
        if w == 0:
            ws.column_dimensions[letter].hidden = True
        else:
            ws.column_dimensions[letter].width = w

    ws.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file first, then replace the real path atomically. If the
    # workbook is open in Excel, Windows locks the file and ``save``/``replace``
    # fails with PermissionError — we then save a sidecar the user can swap in.
    tmp_path = path.with_name(path.name + ".tmp")
    wb.save(tmp_path)
    try:
        os.replace(tmp_path, path)
    except PermissionError:
        fallback = path.with_name(path.stem + "_unlock_me" + path.suffix)
        try:
            if fallback.exists():
                fallback.unlink()
        except OSError:
            pass
        os.replace(tmp_path, fallback)
        logger.error(
            "%s is locked (usually because it is open in Excel). "
            "Wrote the latest data to %s — close the workbook, then delete or "
            "rename the old file and rename %s to %s, or re-run the agent.",
            path.name,
            fallback.name,
            fallback.name,
            path.name,
        )
        raise PermissionError(
            f"{path.name} is in use (close it in Excel if open). "
            f"Latest data saved as {fallback.name}."
        ) from None
    logger.info("Spreadsheet written: %d events → %s", len(sorted_rows), path)


# ── Merge + expire ────────────────────────────────────────────────────────────


def merge_and_write(new_resources: list[Resource]) -> tuple[int, int, int]:
    """Merge new events into the persistent spreadsheet.

    Steps:
    1. Load existing rows from disk.
    2. Drop rows whose event date is in the past.
    3. Add new resources not already present (keyed by URL).
    4. Sort soonest-first and save.

    Returns:
        (added, skipped_duplicate, removed_past) counts.
    """
    out_dir = output_directory()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RESEARCH_FILENAME
    today = utc_today()

    existing = _load_existing_rows(path)

    # Remove past events
    before = len(existing)
    existing = {k: v for k, v in existing.items() if (_row_date(v) or date.max) >= today}
    removed_past = before - len(existing)

    # Merge new resources
    added = 0
    skipped = 0
    for r in new_resources:
        key = (r.url or "").strip().lower()
        if not key.startswith("http"):
            continue
        if key in existing:
            skipped += 1
        else:
            existing[key] = _resource_to_row(r)
            added += 1

    _write_workbook(path, existing)
    logger.info(
        "Spreadsheet: +%d added, %d already present, %d expired removed → %d total",
        added, skipped, removed_past, len(existing),
    )
    return added, skipped, removed_past


# ── Public API (called from graph_nodes) ─────────────────────────────────────


def write_output(
    resources: list[Resource],
    *,
    append_log_only: bool,
    log_line: str,
) -> None:
    """Merge new events into the spreadsheet and append to run_log.md.

    Always calls merge_and_write so expired rows are cleaned up even when
    no new events were found (``append_log_only`` is kept for API compat).
    """
    out_dir = output_directory()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / RUN_LOG_FILENAME

    merge_and_write(resources)
    _append_log(log_path, log_line)
    logger.info("Appended run log entry to %s", log_path)


def _append_log(log_path: Path, log_line: str) -> None:
    """Append a single log line to the run log, creating it when missing."""
    block = f"\n- {log_line}\n"
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    if not existing.strip():
        log_path.write_text("# Run log\n\n" + block.lstrip(), encoding="utf-8")
    else:
        log_path.write_text(existing.rstrip() + block, encoding="utf-8")
