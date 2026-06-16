"""One-off repair: download poster bytes for events whose image docs are
data-less placeholders (Task 12 fallout). Safe to re-run — already-cached
posters are skipped, and orphaned placeholders are garbage-collected.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from agent import image_cache, image_store  # noqa: E402
from agent.mongodb import EVENTS_COLLECTION, IMAGES_COLLECTION, get_database  # noqa: E402
from agent.models import Resource  # noqa: E402


def repair(db_name: str) -> None:
    coll = get_database(db_name)[EVENTS_COLLECTION]
    sources = image_store.source_urls_by_event_id(db_name)
    resources: list[Resource] = []
    for doc in coll.find({}, {"_id": 1, "event": 1}):
        eid = str(doc.get("_id") or "")
        src = sources.get(eid)
        if not src:
            continue
        resources.append(
            Resource(
                id=eid,
                title=str(doc.get("event") or ""),
                url="https://placeholder.local/" + eid,
                date="2099-12-31",
                thumbnail_url=src,
            )
        )

    print(f"[{db_name}] events with upstream poster URLs: {len(resources)}")
    image_cache.cache_thumbnails(resources, db_name=db_name)
    removed = image_cache.garbage_collect([r.id for r in resources], db_name=db_name)

    imgs = get_database(db_name)[IMAGES_COLLECTION]
    with_data = imgs.count_documents({"data": {"$exists": True, "$ne": None}})
    without_data = imgs.count_documents(
        {"$or": [{"data": {"$exists": False}}, {"data": None}]}
    )
    print(
        f"[{db_name}] GC removed {removed} placeholder(s) | "
        f"images with data: {with_data} | without data: {without_data}"
    )


if __name__ == "__main__":
    import sys

    targets = sys.argv[1:] or ["bgc", "galway-music"]
    for db in targets:
        try:
            repair(db)
        except Exception as exc:  # noqa: BLE001
            print(f"[{db}] skipped: {exc}")
