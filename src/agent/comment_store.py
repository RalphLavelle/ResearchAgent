"""MongoDB storage for visitor comments and suggestions (``comments`` collection)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent.mongodb import COMMENTS_COLLECTION, ensure_collection_indexes, get_database

logger = logging.getLogger(__name__)

_MAX_NAME_LEN = 100
_MAX_COMMENT_LEN = 2000


def add_comment(db_name: str, raw_name: str, raw_comment: str) -> dict[str, Any]:
    """Save one visitor comment. Raises ``ValueError`` when validation fails."""
    name = (raw_name or "").strip()
    comment = (raw_comment or "").strip()

    if not name:
        raise ValueError("name is required")
    if not comment:
        raise ValueError("comment is required")
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"name must be {_MAX_NAME_LEN} characters or fewer")
    if len(comment) > _MAX_COMMENT_LEN:
        raise ValueError(f"comment must be {_MAX_COMMENT_LEN} characters or fewer")

    ensure_collection_indexes(db_name)
    coll = get_database(db_name)[COMMENTS_COLLECTION]
    now = datetime.now(timezone.utc).isoformat()
    doc: dict[str, Any] = {
        "_id": str(uuid4()),
        "name": name,
        "comment": comment,
        "date": now,
    }
    coll.insert_one(doc)
    logger.info("Visitor comment saved in db=%s from %r", db_name, name)
    return doc


def list_comments_page(
    db_name: str,
    *,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return one page of comments (newest first) and the total count."""
    ensure_collection_indexes(db_name)
    coll = get_database(db_name)[COMMENTS_COLLECTION]
    total = coll.count_documents({})
    docs = list(coll.find().sort("date", -1).skip(skip).limit(limit))
    return docs, total


def delete_comment(db_name: str, comment_id: str) -> bool:
    """Delete one comment by id. Returns True when a row was removed."""
    cid = (comment_id or "").strip()
    if not cid:
        return False
    coll = get_database(db_name)[COMMENTS_COLLECTION]
    result = coll.delete_one({"_id": cid})
    if result.deleted_count:
        logger.info("Visitor comment deleted in db=%s id=%s", db_name, cid)
    return result.deleted_count > 0
