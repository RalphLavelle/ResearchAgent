"""One-time migration: link existing event venue strings to the venues collection."""

from __future__ import annotations

import logging
from typing import Any

from agent.event_store import venue_id_from_doc, venue_name_from_doc, venue_to_mongo
from agent.mongodb import EVENTS_COLLECTION, get_database
from agent.topics import TopicEntry, load_topics
from agent import config, venue_store

logger = logging.getLogger(__name__)


def _venue_needs_update(doc: dict[str, Any], venue_id: str, canonical: str) -> bool:
    """True when the event document is not yet in nested ``venue`` shape."""
    venue = doc.get("venue")
    if doc.get("venue_id"):
        return True
    if not isinstance(venue, dict):
        return True
    return venue.get("name") != canonical or venue.get("id") != venue_id


def migrate_topic_venues(topic: TopicEntry) -> dict[str, int]:
    """Create venue records and set nested ``venue: {name, id}`` on events."""
    db_name = topic.db
    coll = get_database(db_name)[EVENTS_COLLECTION]
    events = list(coll.find())
    linked = 0
    created_before = len(venue_store.list_venues(db_name))

    for doc in events:
        raw = venue_name_from_doc(doc)
        if not raw:
            continue
        venue_id, canonical = venue_store.resolve_or_create(db_name, raw)
        if not _venue_needs_update(doc, venue_id, canonical):
            continue
        coll.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"venue": venue_to_mongo(canonical, venue_id)},
                "$unset": {"venue_id": ""},
            },
        )
        linked += 1

    venues_after = len(venue_store.list_venues(db_name))
    stats = {
        "events_scanned": len(events),
        "events_linked": linked,
        "venues_created": max(0, venues_after - created_before),
        "venues_total": venues_after,
    }
    logger.info(
        "Venue migration for %s (%s): %s",
        topic.name,
        db_name,
        stats,
    )
    return stats


def migrate_all_topic_venues() -> dict[str, dict[str, int]]:
    """Run venue migration for every registered topic."""
    registry = load_topics(config.TOPICS_CONFIG_PATH)
    results: dict[str, dict[str, int]] = {}
    for topic_id, entry in registry.topics.items():
        results[topic_id] = migrate_topic_venues(entry)
    return results
