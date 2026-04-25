"""Tests for event date parsing, window filter, and sort order."""

from datetime import date, timedelta

import pytest

from agent.event_window import (
    filter_events_in_upcoming_window,
    format_event_weekday_date,
    parse_event_sort_date,
    sort_resources_by_event_date_asc,
    split_band_venue_title,
    split_title_parts,
    utc_today,
)
from agent.models import Resource


def test_parse_iso_prefix() -> None:
    assert parse_event_sort_date("2026-05-03") == date(2026, 5, 3)
    assert parse_event_sort_date("2026-05-03 (doors 7pm)") == date(2026, 5, 3)


def test_parse_invalid() -> None:
    assert parse_event_sort_date("") is None
    assert parse_event_sort_date("Sat 3 May") is None


def test_filter_drops_outside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = date(2026, 4, 1)
    monkeypatch.setattr("agent.event_window.utc_today", lambda: fixed)
    rows = [
        Resource(title="A", url="https://a.example/e", date="2026-04-15"),
        Resource(title="B", url="https://b.example/e", date="2026-03-01"),
        Resource(title="C", url="https://c.example/e", date=""),
        Resource(title="D", url="https://d.example/e", date="2026-05-01"),
    ]
    out = filter_events_in_upcoming_window(rows, days=30)
    titles = {r.title for r in out}
    assert titles == {"A", "D"}


def test_sort_ascending_soonest_first() -> None:
    rows = [
        Resource(title="Early", url="https://x/1", date="2026-04-10"),
        Resource(title="Late", url="https://x/2", date="2026-04-25"),
        Resource(title="Mid", url="https://x/3", date="2026-04-15"),
    ]
    out = sort_resources_by_event_date_asc(rows)
    assert [r.title for r in out] == ["Early", "Mid", "Late"]


def test_utc_today_is_date() -> None:
    assert isinstance(utc_today(), date)


def test_format_event_weekday_date() -> None:
    assert "Wed" in format_event_weekday_date("2026-06-03")
    assert "Jun" in format_event_weekday_date("2026-06-03")


def test_split_band_venue_title() -> None:
    act, suf = split_band_venue_title("The Beths @ The Tivoli")
    assert act == "The Beths"
    assert suf == " @ The Tivoli"
    solo, none = split_band_venue_title("Festival Pass")
    assert solo == "Festival Pass"
    assert none is None


def test_split_title_parts_full() -> None:
    act, venue, loc = split_title_parts("The Beths @ The Tivoli, Brisbane")
    assert act == "The Beths"
    assert venue == "The Tivoli"
    assert loc == "Brisbane"


def test_split_title_parts_no_location() -> None:
    act, venue, loc = split_title_parts("The Beths @ The Tivoli")
    assert act == "The Beths"
    assert venue == "The Tivoli"
    assert loc == ""


def test_split_title_parts_act_only() -> None:
    act, venue, loc = split_title_parts("Open Mic Night")
    assert act == "Open Mic Night"
    assert venue == ""
    assert loc == ""
