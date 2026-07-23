"""Tests for the in-process events API cache."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.events_api_cache import (
    get_events_api_payload,
    invalidate_events_api_cache,
    reset_events_api_cache,
    warm_events_api_cache,
)


@pytest.fixture(autouse=True)
def _clear_events_cache() -> None:
    reset_events_api_cache()
    yield
    reset_events_api_cache()


def test_get_events_api_payload_caches_by_db_and_window() -> None:
    calls: list[str] = []

    def loader(db_name: str) -> dict:
        calls.append(db_name)
        return {"generated": "2026-01-01", "events": [{"id": "a"}]}

    first = get_events_api_payload("test-db", loader)
    second = get_events_api_payload("test-db", loader)

    assert first == second
    assert calls == ["test-db"]


def test_invalidate_events_api_payload_forces_reload() -> None:
    calls: list[str] = []

    def loader(db_name: str) -> dict:
        calls.append(db_name)
        return {"generated": "2026-01-01", "events": []}

    get_events_api_payload("test-db", loader)
    invalidate_events_api_cache("test-db")
    get_events_api_payload("test-db", loader)

    assert calls == ["test-db", "test-db"]


def test_warm_events_api_payload_skips_loader_on_next_get() -> None:
    calls: list[str] = []

    def loader(db_name: str) -> dict:
        calls.append(db_name)
        return {"generated": "2026-01-01", "events": [{"id": "warmed"}]}

    warm_events_api_cache("test-db", loader)
    payload = get_events_api_payload("test-db", loader)

    assert payload["events"][0]["id"] == "warmed"
    assert calls == ["test-db"]


def test_cache_disabled_bypasses_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_API_CACHE_ENABLED", "false")
    from agent import config

    monkeypatch.setattr(config, "EVENTS_API_CACHE_ENABLED", False)

    calls: list[str] = []

    def loader(db_name: str) -> dict:
        calls.append(db_name)
        return {"generated": "2026-01-01", "events": []}

    get_events_api_payload("test-db", loader)
    get_events_api_payload("test-db", loader)

    assert calls == ["test-db", "test-db"]


def test_cache_key_changes_when_display_window_changes() -> None:
    calls: list[str] = []

    def loader(db_name: str) -> dict:
        calls.append(db_name)
        return {"generated": "2026-01-01", "events": []}

    with patch(
        "agent.events_api_cache.api_window_iso_bounds",
        side_effect=[("2026-07-01", "2026-07-31"), ("2026-07-02", "2026-08-01")],
    ):
        get_events_api_payload("test-db", loader)
        get_events_api_payload("test-db", loader)

    assert calls == ["test-db", "test-db"]


def test_api_get_events_uses_cache_after_warm() -> None:
    from starlette.testclient import TestClient

    from agent import api
    from agent.events_api_cache import warm_events_api_cache

    stale_payload = {
        "generated": "2026-07-01T00:00:00Z",
        "events": [{"id": "stale", "eventName": "Stale"}],
    }

    with patch.object(api, "load_events_api_payload", return_value=stale_payload):
        warm_events_api_cache("test-db", api.load_events_api_payload)

    with patch.object(
        api,
        "load_events_api_payload",
        return_value={"generated": "2026-07-02", "events": [{"id": "fresh"}]},
    ) as loader:
        client = TestClient(api.create_app())
        response = client.get("/api/test-db/events")

    assert response.status_code == 200
    assert response.json()["events"][0]["id"] == "stale"
    loader.assert_not_called()
