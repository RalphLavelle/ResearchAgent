"""Shared pytest fixtures — in-memory MongoDB via mongomock."""

from __future__ import annotations

import mongomock
import pytest


@pytest.fixture(autouse=True)
def mongodb_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route all MongoDB calls to an isolated in-memory database."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost/test")

    from agent import mongodb

    mongodb.reset_client_cache()
    client = mongomock.MongoClient()
    monkeypatch.setattr(mongodb, "get_client", lambda: client)
    monkeypatch.setattr("agent.local_output.active_db_name", lambda: "test-db")

    client["test-db"]["events"].drop()
    client["test-db"]["venues"].drop()
    client["test-db"]["images"].drop()
    client["test-db"]["reports"].drop()
    client["test-db"]["sources"].drop()
    client["test-db"]["strategy_scores"].drop()
    client["test-db"]["users"].drop()
    client["test-db"]["schema_migrations"].drop()

    yield

    mongodb.reset_client_cache()
