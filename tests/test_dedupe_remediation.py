"""Tests for admin dedupe remediation (Task 18)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agent.event_store import save_existing_rows, venue_to_mongo
from agent.local_output import (
    _IDX_EVENT,
    _load_existing_rows,
    run_deterministic_dedupe,
    run_dedupe_remediation,
)


def _future_row(
    eid: str,
    act: str,
    url: str,
    *,
    venue: str = "The Tivoli",
    days_ahead: int = 10,
) -> list:
    d = date.today() + timedelta(days=days_ahead)
    return [
        act,
        venue,
        "Brisbane",
        d,
        url,
        "",
        "",
        "",
        "2026-01-01",
        eid,
        "",
        [],
    ]


def test_run_deterministic_dedupe_merges_exact_act_date_duplicates() -> None:
    save_existing_rows(
        "test-db",
        {
            "evt-a": _future_row("evt-a", "Jazz Night", "https://a.example/jazz"),
            "evt-b": _future_row("evt-b", "jazz night", "https://b.example/jazz"),
        },
    )

    removed = run_deterministic_dedupe("test-db")
    assert removed == 1

    rows = _load_existing_rows("test-db")
    assert len(rows) == 1
    kept = next(iter(rows.values()))
    assert str(kept[_IDX_EVENT]).lower() == "jazz night"
    assert "https://b.example/jazz" in str(kept[5] or "")


def test_run_deterministic_dedupe_merges_partial_act_same_venue() -> None:
    save_existing_rows(
        "test-db",
        {
            "evt-short": _future_row(
                "evt-short",
                "Singer One",
                "https://a.example/one",
                venue="Mo's Desert Clubhouse",
            ),
            "evt-long": _future_row(
                "evt-long",
                "Singer One, with Singer Two",
                "https://b.example/one",
                venue="Mo's Desert Clubhouse",
            ),
        },
    )

    removed = run_deterministic_dedupe("test-db")
    assert removed == 1
    rows = _load_existing_rows("test-db")
    assert len(rows) == 1
    assert "Singer One, with Singer Two" in str(next(iter(rows.values()))[_IDX_EVENT])


def test_run_dedupe_remediation_includes_semantic_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_existing_rows(
        "test-db",
        {
            "evt-a": _future_row("evt-a", "Band A", "https://a.example/a"),
            "evt-b": _future_row("evt-b", "band a", "https://b.example/a"),
        },
    )
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", True)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)
    monkeypatch.setattr(
        "agent.local_output.run_llm_semantic_dedupe",
        lambda _db=None: 0,
    )

    result = run_dedupe_remediation("test-db")
    assert result.removed_deterministic == 1
    assert result.removed_semantic == 0
    assert result.total_removed == 1


def test_post_admin_dedupe_events_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from agent.api import create_app

    monkeypatch.setattr("agent.config.ADMIN_PASSWORD", "secret-admin")
    monkeypatch.setattr(
        "agent.api.run_dedupe_remediation",
        lambda _db: type(
            "R",
            (),
            {
                "removed_deterministic": 2,
                "removed_semantic": 1,
                "total_removed": 3,
            },
        )(),
    )

    client = TestClient(create_app())
    response = client.post(
        "/api/admin/dedupe-events",
        json={"password": "secret-admin"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["total_removed"] == 3
    assert "Removed 3 duplicate" in body["message"]
