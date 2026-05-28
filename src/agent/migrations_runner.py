"""Discover and apply one-shot schema migrations before pipeline runs."""

from __future__ import annotations

import importlib.util
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import config
from agent.mongodb import get_database
from agent.topics import load_topics

logger = logging.getLogger(__name__)

SCHEMA_MIGRATIONS_COLLECTION = "schema_migrations"
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _load_migration_modules() -> list[tuple[str, Any]]:
    """Return ``(migration_id, module)`` pairs sorted by filename."""
    if not MIGRATIONS_DIR.is_dir():
        return []

    loaded: list[tuple[str, Any]] = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9]*_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        migration_id = str(getattr(module, "MIGRATION_ID", "") or path.stem).strip()
        run_fn = getattr(module, "run", None)
        if not migration_id or not callable(run_fn):
            logger.warning("Skipping invalid migration file: %s", path.name)
            continue
        loaded.append((migration_id, module))
    return loaded


def _applied_migration_ids(db_name: str) -> set[str]:
    coll = get_database(db_name)[SCHEMA_MIGRATIONS_COLLECTION]
    return {str(doc["_id"]) for doc in coll.find({}, {"_id": 1})}


def run_pending_migrations_for_db(db_name: str) -> list[dict[str, Any]]:
    """Apply migrations not yet recorded for one topic database."""
    results: list[dict[str, Any]] = []
    applied = _applied_migration_ids(db_name)
    coll = get_database(db_name)[SCHEMA_MIGRATIONS_COLLECTION]

    for migration_id, module in _load_migration_modules():
        if migration_id in applied:
            continue
        stats = module.run(db_name)
        coll.insert_one(
            {
                "_id": migration_id,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "stats": stats,
            }
        )
        logger.info(
            "Applied migration %s to db=%s: %s",
            migration_id,
            db_name,
            stats,
        )
        results.append({"migration_id": migration_id, "stats": stats})
    return results


def run_pending_migrations() -> dict[str, list[dict[str, Any]]]:
    """Apply pending migrations for every registered topic database."""
    registry = load_topics(config.TOPICS_CONFIG_PATH)
    summary: dict[str, list[dict[str, Any]]] = {}
    for topic_id, entry in registry.topics.items():
        try:
            summary[topic_id] = run_pending_migrations_for_db(entry.db)
        except Exception:
            logger.exception("Schema migration failed for topic %s (%s)", topic_id, entry.db)
            raise
    return summary
