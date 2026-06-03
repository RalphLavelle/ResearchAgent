"""Tests for weekly email subscriber storage."""

from agent import user_store
from agent.mongodb import USERS_COLLECTION, get_database


def test_subscribe_saves_normalized_email() -> None:
    doc = user_store.subscribe("test-db", "  Fan@Example.COM  ")

    assert doc["email"] == "fan@example.com"
    assert doc["subscribed_at"]

    stored = get_database("test-db")[USERS_COLLECTION].find_one({"email": "fan@example.com"})
    assert stored is not None
    assert stored["_id"] == doc["_id"]


def test_subscribe_is_idempotent() -> None:
    first = user_store.subscribe("test-db", "fan@example.com")
    second = user_store.subscribe("test-db", "FAN@EXAMPLE.COM")

    assert first["_id"] == second["_id"]
    assert get_database("test-db")[USERS_COLLECTION].count_documents({}) == 1


def test_subscribe_rejects_invalid_email() -> None:
    try:
        user_store.subscribe("test-db", "not-an-email")
    except ValueError as exc:
        assert "Invalid" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_is_valid_email() -> None:
    assert user_store.is_valid_email("user@example.com")
    assert not user_store.is_valid_email("")
    assert not user_store.is_valid_email("missing-at-sign")


def test_list_users_page_newest_first() -> None:
    user_store.subscribe("test-db", "older@example.com")
    user_store.subscribe("test-db", "newer@example.com")

    docs, total = user_store.list_users_page("test-db", limit=50, skip=0)

    assert total == 2
    assert len(docs) == 2
    emails = [doc["email"] for doc in docs]
    assert "newer@example.com" in emails
    assert "older@example.com" in emails
