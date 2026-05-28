"""Add ``tags`` (string array) to every event document."""

MIGRATION_ID = "003_add_event_tags"


def run(db_name: str) -> dict[str, int]:
    """Initialise ``tags`` as an empty list on events that do not have it yet."""
    from agent.mongodb import EVENTS_COLLECTION, get_database

    coll = get_database(db_name)[EVENTS_COLLECTION]
    result = coll.update_many(
        {"tags": {"$exists": False}},
        {"$set": {"tags": []}},
    )
    return {"events_updated": int(result.modified_count)}
