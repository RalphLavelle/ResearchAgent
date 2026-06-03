"""MongoDB storage for weekly email subscribers (``users`` collection)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent.mongodb import USERS_COLLECTION, ensure_collection_indexes, get_database

logger = logging.getLogger(__name__)

# Practical RFC 5322 subset — enough to reject obvious typos without extra deps.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def normalize_email(raw: str) -> str:
    """Lowercase and trim an email address for storage and lookup."""
    return (raw or "").strip().lower()


def is_valid_email(raw: str) -> bool:
    """Return True when *raw* looks like a deliverable email address."""
    email = normalize_email(raw)
    if not email or len(email) > 254:
        return False
    return bool(_EMAIL_RE.match(email))


def subscribe(db_name: str, raw_email: str) -> dict[str, Any]:
    """Insert a weekly-email subscriber or return the existing row (idempotent).

    Raises ``ValueError`` when the email fails validation.
    """
    email = normalize_email(raw_email)
    if not is_valid_email(email):
        raise ValueError("Invalid email address.")

    ensure_collection_indexes(db_name)
    coll = get_database(db_name)[USERS_COLLECTION]
    existing = coll.find_one({"email": email})
    if existing:
        logger.debug("Weekly email signup: %r already subscribed in db=%s", email, db_name)
        return existing

    now = datetime.now(timezone.utc).isoformat()
    doc: dict[str, Any] = {
        "_id": str(uuid4()),
        "email": email,
        "subscribed_at": now,
    }
    coll.insert_one(doc)
    logger.info("Weekly email signup: saved %r in db=%s", email, db_name)
    return doc


def list_users_page(
    db_name: str,
    *,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return one page of subscribers (newest first) and the total count."""
    coll = get_database(db_name)[USERS_COLLECTION]
    total = coll.count_documents({})
    docs = list(coll.find().sort("subscribed_at", -1).skip(skip).limit(limit))
    return docs, total
