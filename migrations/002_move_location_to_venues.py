"""Move suburb/city from events onto venue records."""

MIGRATION_ID = "002_move_location_to_venues"


def run(db_name: str) -> dict[str, int]:
    """Copy ``location`` from events to venues, then remove it from events."""
    from agent import venue_store
    from agent.event_store import venue_id_from_doc, venue_name_from_doc
    from agent.mongodb import EVENTS_COLLECTION, get_database

    events = get_database(db_name)[EVENTS_COLLECTION]
    venues_updated = 0
    events_updated = 0

    for doc in events.find({"location": {"$exists": True}}):
        location = str(doc.get("location") or "").strip()
        if not location:
            events.update_one({"_id": doc["_id"]}, {"$unset": {"location": ""}})
            events_updated += 1
            continue

        venue_id = venue_id_from_doc(doc)
        if not venue_id:
            name = venue_name_from_doc(doc)
            if name:
                match = venue_store.find_by_name_or_alias(db_name, name)
                if match:
                    venue_id = str(match["_id"])

        if venue_id:
            venue_store.set_location(db_name, venue_id, location)
            venues_updated += 1

        events.update_one({"_id": doc["_id"]}, {"$unset": {"location": ""}})
        events_updated += 1

    return {"venues_updated": venues_updated, "events_updated": events_updated}
