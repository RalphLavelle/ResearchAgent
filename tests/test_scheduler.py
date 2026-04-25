"""Tests for schedule interval loading logic."""

from pathlib import Path

import pytest

from agent.scheduler import _load_interval


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_defaults_to_one_hour_when_file_missing(tmp_path: Path) -> None:
    unit, value = _load_interval(tmp_path / "missing.yaml")
    assert unit == "hours"
    assert value == 1.0


def test_hours_used_when_minutes_zero(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    _write_yaml(p, "interval_hours: 2\ninterval_minutes: 0\n")
    unit, value = _load_interval(p)
    assert unit == "hours"
    assert value == 2.0


def test_minutes_takes_priority_when_nonzero(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    _write_yaml(p, "interval_hours: 12\ninterval_minutes: 5\n")
    unit, value = _load_interval(p)
    assert unit == "minutes"
    assert value == 5.0


def test_minutes_only_no_hours_key(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    _write_yaml(p, "interval_minutes: 10\n")
    unit, value = _load_interval(p)
    assert unit == "minutes"
    assert value == 10.0


def test_hours_only_no_minutes_key(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    _write_yaml(p, "interval_hours: 3\n")
    unit, value = _load_interval(p)
    assert unit == "hours"
    assert value == 3.0


def test_bad_yaml_falls_back_to_one_hour(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    _write_yaml(p, "interval_hours: not_a_number\n")
    unit, value = _load_interval(p)
    assert unit == "hours"
    assert value == 1.0
