"""MongoDB connection helpers for topic-scoped databases.

Each research topic uses the ``db`` property from ``topics.json`` as its
database name. Collections: ``events``, ``venues``, ``images`` (poster blobs),
``reports``, and ``users`` (weekly email subscribers).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pymongo.database import Database
    from pymongo.mongo_client import MongoClient

EVENTS_COLLECTION = "events"
VENUES_COLLECTION = "venues"
IMAGES_COLLECTION = "images"
REPORTS_COLLECTION = "reports"
USERS_COLLECTION = "users"

# Hostnames that are NOT valid Atlas cluster endpoints (common copy-paste mistakes).
_INVALID_ATLAS_HOSTS = frozenset(
    {
        "mongodb.com",
        "www.mongodb.com",
        "cloud.mongodb.com",
        "account.mongodb.com",
    }
)


def _uri_host(uri: str) -> str:
    """Extract hostname from a MongoDB URI without logging credentials."""
    normalised = uri.replace("mongodb+srv://", "https://").replace("mongodb://", "http://")
    return (urlparse(normalised).hostname or "").strip().lower()


def validate_mongodb_uri(uri: str | None = None) -> str:
    """Return the URI when it looks usable; raise ``ValueError`` with help text."""
    value = (uri or mongodb_uri()).strip()
    if not value:
        raise ValueError(
            "MONGODB_URI is not set in .env.\n"
            "Local dev: MONGODB_URI=mongodb://localhost:27017/\n"
            "Atlas: Database → Connect → Drivers → copy the connection string."
        )

    host = _uri_host(value)
    if not host:
        raise ValueError(
            "MONGODB_URI has no hostname. Paste the full Atlas string, e.g.\n"
            "mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true"
        )

    if host in _INVALID_ATLAS_HOSTS:
        raise ValueError(
            f"MONGODB_URI host is {host!r}, which is not a cluster address.\n"
            "Atlas connection strings use a host like cluster0.ab12cd.mongodb.net — "
            "not mongodb.com.\n"
            "Atlas → your cluster → Connect → Drivers → copy connection string."
        )

    if value.startswith("mongodb+srv://") and not host.endswith(".mongodb.net"):
        raise ValueError(
            f"MONGODB_URI host {host!r} does not look like Atlas (expected *.mongodb.net).\n"
            "Check you copied the full string from Atlas → Connect → Drivers."
        )

    if "<password>" in value or "<db_password>" in value:
        raise ValueError(
            "MONGODB_URI still contains a placeholder password.\n"
            "Replace <password> with your database user's password (URL-encode special chars)."
        )

    return value


def mongodb_uri() -> str:
    """Return the configured Atlas / local connection string."""
    return (os.environ.get("MONGODB_URI") or "").strip()


def mongodb_configured() -> bool:
    return bool(mongodb_uri())


@lru_cache
def get_client() -> MongoClient:
    """Singleton client — reused for the process lifetime."""
    from pymongo import MongoClient

    uri = validate_mongodb_uri()
    return MongoClient(uri)


@lru_cache
def ensure_collection_indexes(db_name: str) -> None:
    """Create indexes that help venue admin queries and poster lookups (idempotent)."""
    name = (db_name or "").strip()
    if not name:
        return
    db = get_client()[name]
    db[EVENTS_COLLECTION].create_index("venue.id", background=True)
    db[EVENTS_COLLECTION].create_index("date", background=True)
    db[EVENTS_COLLECTION].create_index("image_id", background=True, sparse=True)
    db[IMAGES_COLLECTION].create_index("source_url", background=True)
    db[USERS_COLLECTION].create_index("email", unique=True, background=True)


def get_database(db_name: str) -> Database:
    """Return the database for one topic (``topics.json`` ``db`` field)."""
    name = (db_name or "").strip()
    if not name:
        raise ValueError("Topic database name is empty.")
    return get_client()[name]


def reset_client_cache() -> None:
    """Clear cached client (tests only)."""
    cached = getattr(get_client, "cache_clear", None)
    if callable(cached):
        cached()
