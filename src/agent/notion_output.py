"""Push curated research Markdown to a Notion page via the official REST API (httpx)."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agent import config
from agent.display_time import format_generated_timestamp
from agent.event_window import (
    format_event_weekday_date,
    sort_resources_by_event_date_asc,
    split_title_parts,
)
from agent.models import Resource

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
# Default API version; override with NOTION_API_VERSION in env if Notion deprecates it.
DEFAULT_NOTION_VERSION = "2022-06-28"
_MAX_RICH_TEXT = 2000
_APPEND_BATCH = 100
# Notion asks integrations to stay near ~3 req/s on average; small pause between deletes.
_DELETE_DELAY_SEC = 0.35


def hyphenate_uuid(hex32: str) -> str:
    """Turn 32 hex chars into a UUID string for Notion API paths."""
    p = hex32.lower()
    return f"{p[0:8]}-{p[8:12]}-{p[12:16]}-{p[16:20]}-{p[20:32]}"


def parse_notion_page_id(raw: str) -> str:
    """
    Accept a Notion page URL, a hyphenated UUID, or 32 hex characters.
    Returns hyphenated UUID for API calls.
    """
    s = raw.strip()
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.I,
    )
    if uuid_re.match(s):
        return s

    # Plain 32-char hex (no hyphens)
    if len(s) == 32 and re.fullmatch(r"[0-9a-f]+", s, re.I):
        return hyphenate_uuid(s)

    # Notion URLs use slug like "Page-Title-<32hex>" — take last hyphen segment.
    for part in reversed(re.split(r"[-/?#]", s)):
        token = part.split("?")[0].strip()
        if len(token) == 32 and re.fullmatch(r"[0-9a-f]+", token, re.I):
            return hyphenate_uuid(token)

    raise ValueError(
        "NOTION_RESEARCH_PAGE_ID must be a Notion page URL or UUID "
        "(share the page with your integration, then copy the page link)."
    )


def _annotations(
    *,
    bold: bool = False,
    italic: bool = False,
) -> dict[str, Any]:
    """Notion expects a full annotations object when any style is set."""
    return {
        "bold": bold,
        "italic": italic,
        "strikethrough": False,
        "underline": False,
        "code": False,
        "color": "default",
    }


def _text_segment(
    content: str,
    *,
    bold: bool = False,
    italic: bool = False,
    link_url: str | None = None,
) -> dict[str, Any]:
    """One rich_text object (caller keeps each segment under Notion's length limits)."""
    text_obj: dict[str, Any] = {"content": content}
    if link_url:
        text_obj["link"] = {"url": link_url}
    else:
        text_obj["link"] = None
    seg: dict[str, Any] = {"type": "text", "text": text_obj}
    seg["annotations"] = _annotations(bold=bold, italic=italic)
    return seg


def _notion_cell_rich_text(
    content: str,
    *,
    bold: bool = False,
    italic: bool = False,
    link_url: str | None = None,
) -> list[dict[str, Any]]:
    """Rich text array for one table cell (Notion requires full annotations on each segment)."""
    chunks = _split_plain_chunks(content) if content else [""]
    out: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        out.append(
            _text_segment(
                chunk or "—",
                bold=bold,
                italic=italic,
                link_url=link_url if i == 0 and link_url else None,
            )
        )
    return out or [_text_segment("—", bold=bold, italic=italic)]


def _table_row_block(cells: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """One Notion table_row (``cells`` length must match table ``table_width``)."""
    return {
        "object": "block",
        "type": "table_row",
        "table_row": {"cells": cells},
    }


def _notion_event_cell_segments(r: Resource) -> list[dict[str, Any]]:
    """Rich text for the Event column: optional poster glyph + act name, both linked to URL.

    Notion table cells cannot embed images; when a thumbnail exists a small
    ``🖼 `` glyph (linked to the event URL) is prepended as a visual indicator.
    The venue is now a separate column so it is NOT included here.
    """
    url = (r.url or "").strip()
    act, _venue, _location = split_title_parts(r.title or "")
    segs: list[dict[str, Any]] = []
    if url.startswith("http"):
        thumb = (r.thumbnail_url or "").strip()
        if thumb.startswith("http"):
            segs.append(_text_segment("🖼 ", link_url=url))
        segs.append(_text_segment(act if act else "—", link_url=url))
    else:
        segs.append(_text_segment((act or (r.title or "")).strip() or "—"))
    if r.participatory:
        segs.append(_text_segment(" (open mic)", italic=True))
    return segs if segs else [_text_segment("—")]


def _notion_venue_cell_segments(r: Resource) -> list[dict[str, Any]]:
    """Rich text for the Venue column: venue + location joined with ', '."""
    _act, venue, location = split_title_parts(r.title or "")
    parts = [p for p in [venue, location] if p]
    text = ", ".join(parts) if parts else "—"
    return _notion_cell_rich_text(text)


def _events_table_block(resources: list[Resource]) -> dict[str, Any]:
    """Three-column Notion table: Event (linked act + optional poster glyph), Venue, Date."""
    width = 3
    header_cells = [
        _notion_cell_rich_text("Event", bold=True),
        _notion_cell_rich_text("Venue", bold=True),
        _notion_cell_rich_text("Date", bold=True),
    ]
    child_rows: list[dict[str, Any]] = [_table_row_block(header_cells)]

    if not resources:
        child_rows.append(
            _table_row_block(
                [
                    _notion_cell_rich_text(
                        "No gigs found in the next 30-day window. Try broadening searches or check back later."
                    ),
                    _notion_cell_rich_text("—"),
                    _notion_cell_rich_text("—"),
                ]
            )
        )
    else:
        for r in resources:
            child_rows.append(
                _table_row_block(
                    [
                        _notion_event_cell_segments(r),
                        _notion_venue_cell_segments(r),
                        _notion_cell_rich_text(format_event_weekday_date(r.date)),
                    ]
                )
            )

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": child_rows,
        },
    }


def _split_plain_chunks(text: str) -> list[str]:
    if not text:
        return []
    return [text[i : i + _MAX_RICH_TEXT] for i in range(0, len(text), _MAX_RICH_TEXT)]


def _paragraph_from_segments(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a paragraph block from rich_text segments."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": segments},
    }


def _label_value_paragraph(label: str, value: str) -> dict[str, Any]:
    """Bold ``Label:`` followed by plain value (value may be long — chunked)."""
    parts: list[dict[str, Any]] = [_text_segment(f"{label}: ", bold=True)]
    for i, chunk in enumerate(_split_plain_chunks(value)):
        parts.append(_text_segment(chunk, bold=False))
    return _paragraph_from_segments(parts)


def _label_link_paragraph(label: str, url: str) -> dict[str, Any]:
    """Bold label plus a clickable link (display text is the URL, trimmed for readability)."""
    display = url.strip()
    if len(display) > 120:
        display = display[:117] + "..."
    parts: list[dict[str, Any]] = [
        _text_segment(f"{label}: ", bold=True),
        _text_segment(display, link_url=url.strip()),
    ]
    return _paragraph_from_segments(parts)


def _intro_paragraph_with_bold_phrase(
    before: str,
    bold_middle: str,
    after: str,
) -> dict[str, Any]:
    """One paragraph mixing plain and bold (for the LangGraph note in the intro)."""
    return _paragraph_from_segments(
        [
            _text_segment(before),
            _text_segment(bold_middle, bold=True),
            _text_segment(after),
        ]
    )


def _external_image_block(image_url: str) -> dict[str, Any]:
    """Notion image block from a public HTTPS URL."""
    return {
        "object": "block",
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": image_url.strip()},
        },
    }


def _rich_paragraph(content: str) -> dict[str, Any]:
    """One paragraph block; splits plain text into <=2000-char rich_text segments."""
    chunks = [_text_segment(p) for p in _split_plain_chunks(content)]
    return _paragraph_from_segments(chunks)


def _heading(level: int, content: str) -> dict[str, Any]:
    key = f"heading_{level}"
    title = content.strip()[:_MAX_RICH_TEXT] or "(untitled)"
    return {
        "object": "block",
        "type": key,
        key: {
            "rich_text": [{"type": "text", "text": {"content": title}}],
        },
    }


def _divider() -> dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


def markdown_to_notion_blocks(markdown: str) -> list[dict[str, Any]]:
    """Map our Markdown lines to Notion block payloads (simple line-based rules)."""
    blocks: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "---":
            blocks.append(_divider())
            continue
        if stripped.startswith("# "):
            blocks.append(_heading(1, stripped[2:].strip()))
            continue
        if stripped.startswith("## "):
            blocks.append(_heading(2, stripped[3:].strip()))
            continue
        blocks.append(_rich_paragraph(line.rstrip()))
    return blocks


def resources_to_notion_blocks(resources: list[Resource]) -> list[dict[str, Any]]:
    """
    Build Notion blocks: title, ``Generated`` line, divider, then a two-column
    event table (soonest first). No description paragraph; posters are indicated
    in-table (Notion cannot show inline images inside table cells).
    """
    resources = sort_resources_by_event_date_asc(list(resources))
    ts = format_generated_timestamp()

    blocks: list[dict[str, Any]] = [
        _heading(1, config.SUBJECT.output_title),
        _paragraph_from_segments([_text_segment(f"Generated: {ts}", italic=True)]),
        _divider(),
        _events_table_block(resources),
    ]
    return blocks


def _notion_headers(token: str, api_version: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": api_version,
        "Content-Type": "application/json",
    }


def _list_all_block_ids(
    client: httpx.Client, page_id: str, headers: dict[str, str]
) -> list[str]:
    """Collect direct child block IDs under the page (paginated)."""
    ids: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {"page_size": "100"}
        if cursor:
            params["start_cursor"] = cursor
        r = client.get(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=headers,
            params=params,
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        for b in data.get("results") or []:
            bid = b.get("id")
            if bid:
                ids.append(bid)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return ids


def _delete_blocks(
    client: httpx.Client, block_ids: list[str], headers: dict[str, str]
) -> None:
    for bid in block_ids:
        r = client.delete(f"{NOTION_API}/blocks/{bid}", headers=headers, timeout=60.0)
        r.raise_for_status()
        time.sleep(_DELETE_DELAY_SEC)


def _append_blocks(
    client: httpx.Client,
    page_id: str,
    blocks: list[dict[str, Any]],
    headers: dict[str, str],
) -> None:
    for i in range(0, len(blocks), _APPEND_BATCH):
        batch = blocks[i : i + _APPEND_BATCH]
        r = client.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=headers,
            json={"children": batch},
            timeout=120.0,
        )
        r.raise_for_status()


def notion_sync_needed(current_fingerprint: str, state_path: Path) -> bool:
    """
    True if Notion should receive the current research body.

    Used so a first-time setup still syncs when the user adds Notion credentials
    after a run already created ``snapshot.json`` (local skip path would
    otherwise never call the API).
    """
    if not state_path.exists():
        return True
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("fingerprint") != current_fingerprint
    except (json.JSONDecodeError, OSError):
        return True


def mark_notion_synced(current_fingerprint: str, state_path: Path) -> None:
    """Record a successful Notion push (retry next run if sync fails before this)."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "fingerprint": current_fingerprint,
                "synced_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )


def sync_research_page(
    *,
    token: str,
    page_id_raw: str,
    resources: list[Resource],
    api_version: str = DEFAULT_NOTION_VERSION,
) -> None:
    """
    Replace the page's block children with structured blocks (links, images, bold labels).

    The Notion page must be shared with the integration that owns ``token``.
    """
    page_id = parse_notion_page_id(page_id_raw)
    headers = _notion_headers(token, api_version)
    blocks = resources_to_notion_blocks(resources)
    if not blocks:
        blocks = [_rich_paragraph("(empty research output)")]

    with httpx.Client() as client:
        existing = _list_all_block_ids(client, page_id, headers)
        if existing:
            _delete_blocks(client, existing, headers)
        _append_blocks(client, page_id, blocks, headers)

    logger.info("Synced research to Notion page %s (%d blocks)", page_id, len(blocks))
