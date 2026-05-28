"""Tests for schedule interval parsing from .env."""

from agent.config import parse_schedule_interval_hours


def test_defaults_to_one_hour_when_unset() -> None:
    assert parse_schedule_interval_hours(None) == 1.0
    assert parse_schedule_interval_hours("") == 1.0
    assert parse_schedule_interval_hours("   ") == 1.0


def test_parses_integer_hours() -> None:
    assert parse_schedule_interval_hours("2") == 2.0


def test_parses_fractional_hours() -> None:
    assert parse_schedule_interval_hours("1.5") == 1.5


def test_bad_value_falls_back_to_one_hour() -> None:
    assert parse_schedule_interval_hours("not_a_number") == 1.0


def test_enforces_minimum_interval() -> None:
    assert parse_schedule_interval_hours("0") == 0.05
    assert parse_schedule_interval_hours("-5") == 0.05
