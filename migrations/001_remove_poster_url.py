"""Schema migrations run once per topic database before pipeline passes."""

MIGRATION_ID = "001_remove_poster_url"


def run(db_name: str) -> dict[str, int]:
    """Drop ``poster_url`` from events — upstream URLs live on ``images`` documents."""
    from agent.mongodb import EVENTS_COLLECTION, get_database

    coll = get_database(db_name)[EVENTS_COLLECTION]
    result = coll.update_many({}, {"$unset": {"poster_url": ""}})
    return {"events_updated": int(result.modified_count)}
