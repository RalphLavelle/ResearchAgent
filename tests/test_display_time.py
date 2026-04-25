"""Tests for local-time display used in research output."""

from agent.display_time import format_generated_timestamp, display_timezone


def test_display_timezone_returns_zoneinfo() -> None:
    tz = display_timezone()
    assert tz.key == "Australia/Brisbane"


def test_format_generated_timestamp_non_empty() -> None:
    s = format_generated_timestamp()
    assert len(s) >= 10
    assert any(c.isdigit() for c in s)
