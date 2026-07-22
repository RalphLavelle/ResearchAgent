"""Tests for visitor comment storage."""

import pytest

from agent import comment_store
from agent.mongodb import COMMENTS_COLLECTION, get_database


def test_add_comment_saves_name_comment_and_date() -> None:
    doc = comment_store.add_comment("test-db", " Alex ", "  More venues please!  ")

    assert doc["name"] == "Alex"
    assert doc["comment"] == "More venues please!"
    assert doc["date"]

    stored = get_database("test-db")[COMMENTS_COLLECTION].find_one({"_id": doc["_id"]})
    assert stored is not None
    assert stored["name"] == "Alex"
    assert stored["comment"] == "More venues please!"


def test_add_comment_requires_name() -> None:
    with pytest.raises(ValueError, match="name is required"):
        comment_store.add_comment("test-db", "   ", "Hello")


def test_add_comment_requires_comment() -> None:
    with pytest.raises(ValueError, match="comment is required"):
        comment_store.add_comment("test-db", "Alex", "   ")


def test_add_comment_rejects_overlong_fields() -> None:
    with pytest.raises(ValueError, match="name must be"):
        comment_store.add_comment("test-db", "x" * 101, "Hi")

    with pytest.raises(ValueError, match="comment must be"):
        comment_store.add_comment("test-db", "Alex", "y" * 2001)


def test_list_comments_page_newest_first() -> None:
    comment_store.add_comment("test-db", "First", "One")
    comment_store.add_comment("test-db", "Second", "Two")

    docs, total = comment_store.list_comments_page("test-db", limit=50, skip=0)
    assert total == 2
    assert len(docs) == 2
    assert docs[0]["name"] == "Second"
    assert docs[1]["name"] == "First"


def test_delete_comment_removes_row() -> None:
    doc = comment_store.add_comment("test-db", "Alex", "Remove me")
    cid = str(doc["_id"])

    assert comment_store.delete_comment("test-db", cid) is True
    assert comment_store.delete_comment("test-db", cid) is False
    assert comment_store.delete_comment("test-db", "missing") is False
