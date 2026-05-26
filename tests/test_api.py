"""Tests for the HTTP API."""

from agent.local_output import MergeStats
from agent.report_store import save_run_report
from starlette.testclient import TestClient

from agent.api import create_app


def test_get_reports_returns_saved_rows() -> None:
    save_run_report(
        "test-db",
        queries=["Gold Coast gigs"],
        crawled_urls=["https://example.com/events"],
        merge_stats=MergeStats(
            added=1,
            skipped=0,
            removed_past=0,
            removed_exclusion=0,
            removed_dedupe=0,
            total_after=1,
        ),
    )

    client = TestClient(create_app())
    response = client.get("/api/test-db/reports")

    assert response.status_code == 200
    body = response.json()
    assert len(body["reports"]) >= 1
    latest = body["reports"][0]
    assert latest["searches"] == ["Gold Coast gigs"]
    assert "example.com" in latest["urls"]
    assert latest["changes"]["added (new rows)"] == 1


def test_get_reports_unknown_db_still_resolves() -> None:
    client = TestClient(create_app())
    response = client.get("/api/unknown-db-xyz/reports")
    assert response.status_code == 200
    assert response.json()["reports"] == []
