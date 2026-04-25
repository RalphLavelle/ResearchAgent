"""Tests for Notion page ID parsing and Markdown → block mapping (no HTTP)."""

from pathlib import Path

import pytest

from agent.models import Resource
from agent.notion_output import (
    markdown_to_notion_blocks,
    mark_notion_synced,
    notion_sync_needed,
    parse_notion_page_id,
    resources_to_notion_blocks,
)


def test_parse_hyphenated_uuid() -> None:
    u = "1a2b3c4d-5e6f-7890-abcd-ef1234567890"
    assert parse_notion_page_id(u) == u


def test_parse_32_hex() -> None:
    # 32 hex chars (UUID without hyphens)
    h = "550e8400e29b41d4a716446655440000"
    assert parse_notion_page_id(h) == "550e8400-e29b-41d4-a716-446655440000"


def test_parse_notion_url_slug() -> None:
    url = "https://www.notion.so/Research-550e8400e29b41d4a716446655440000"
    assert parse_notion_page_id(url) == "550e8400-e29b-41d4-a716-446655440000"


def test_parse_invalid_raises() -> None:
    with pytest.raises(ValueError, match="NOTION_RESEARCH_PAGE_ID"):
        parse_notion_page_id("not-a-page-id")


def test_markdown_to_blocks_headings_and_divider() -> None:
    md = "# Title\n\n## Sub\n\n---\n\nHello"
    blocks = markdown_to_notion_blocks(md)
    assert blocks[0]["type"] == "heading_1"
    assert blocks[1]["type"] == "heading_2"
    assert blocks[2]["type"] == "divider"
    assert blocks[3]["type"] == "paragraph"


def test_notion_sync_needed_missing_file(tmp_path: Path) -> None:
    state = tmp_path / "notion_sync_state.json"
    assert notion_sync_needed("abc", state) is True


def test_notion_sync_needed_same_fp(tmp_path: Path) -> None:
    state = tmp_path / "notion_sync_state.json"
    mark_notion_synced("same-fp", state)
    assert notion_sync_needed("same-fp", state) is False
    assert notion_sync_needed("other-fp", state) is True


def test_resources_to_notion_blocks_three_columns() -> None:
    r = Resource(
        title="The Beths @ The Tivoli, Brisbane",
        url="https://example.com/tickets/123",
        resource_type="event",
        price="Unknown",
        date="2026-06-03",
        summary="",
        participatory=False,
        thumbnail_url="https://example.com/poster.webp",
    )
    blocks = resources_to_notion_blocks([r])
    types = [b["type"] for b in blocks]
    assert "heading_1" in types
    assert "table" in types
    assert "image" not in types
    assert types.count("paragraph") == 1

    table = next(b for b in blocks if b["type"] == "table")
    # Now 3 columns: Event, Venue, Date
    assert table["table"]["table_width"] == 3
    children = table["table"]["children"]
    assert len(children) == 2  # header row + 1 data row
    data_row = children[1]
    cells = data_row["table_row"]["cells"]
    assert len(cells) == 3

    # Cell 0: act name linked; venue NOT in this cell
    event_cell = cells[0]
    linked_beths = any(
        seg.get("type") == "text"
        and "Beths" in (seg.get("text") or {}).get("content", "")
        and (seg.get("text") or {}).get("link", {}).get("url") == "https://example.com/tickets/123"
        for seg in event_cell
    )
    assert linked_beths
    tivoli_in_event_cell = any(
        "Tivoli" in (seg.get("text") or {}).get("content", "") for seg in event_cell
    )
    assert not tivoli_in_event_cell, "Venue should be in its own column, not the Event column"

    # Cell 1: venue + location
    venue_text = "".join((s.get("text") or {}).get("content", "") for s in cells[1])
    assert "Tivoli" in venue_text
    assert "Brisbane" in venue_text

    # Cell 2: date
    date_text = "".join((s.get("text") or {}).get("content", "") for s in cells[2])
    assert "Wed" in date_text
    assert "Jun" in date_text
