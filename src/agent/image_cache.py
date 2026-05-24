"""Self-host event poster bytes in MongoDB (Task 4).

Posters are stored once per upstream URL in the topic's ``images`` collection.
Many events can share one blob when they point at the same remote image.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import httpx

from agent import image_store
from agent.models import Resource

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; AIAgentResearch/0.1; +https://example.local) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(10.0, connect=4.0)
MAX_BYTES = 4 * 1024 * 1024


@dataclass
class DedupeStats:
    """Legacy hook for CLI startup — MongoDB dedupes by source URL automatically."""

    files_removed: int = 0
    events_relinked: int = 0


from agent.image_store import file_name_for_source  # re-export for tests/migration


def api_image_url(db_name: str, image_id: str) -> str:
    """Public URL path served by ``agent.api`` for one cached poster."""
    return f"/api/{db_name}/images/{image_id}"


def _download(url: str) -> tuple[bytes, str] | None:
    if not url or not url.lower().startswith("http"):
        return None
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            ext = image_store.ext_for_content_type(ctype)
            if not ext:
                logger.debug(
                    "image cache: rejecting %s — content-type %r is not a known image",
                    url,
                    ctype,
                )
                return None
            data = resp.content
            if len(data) > MAX_BYTES:
                logger.debug(
                    "image cache: rejecting %s — %d bytes exceeds %d cap",
                    url,
                    len(data),
                    MAX_BYTES,
                )
                return None
            return data, ctype
    except Exception as exc:
        logger.debug("image cache: fetch failed for %s: %s", url, exc)
        return None


def cache_thumbnails(resources: list[Resource], *, db_name: str) -> list[Resource]:
    """Download each distinct poster URL once; many events may share one blob."""
    image_id_updates: dict[str, str | None] = {}
    out: list[Resource] = []

    for r in resources:
        eid = (r.id or "").strip()
        url = (r.thumbnail_url or "").strip()

        # Already an API path from a previous run.
        if url.startswith(f"/api/{db_name}/images/"):
            out.append(r)
            continue

        if not eid or not url or not url.lower().startswith("http"):
            image_id_updates[eid] = None
            out.append(r.model_copy(update={"thumbnail_url": None}) if url and not url.startswith("http") else r)
            continue

        existing_id = image_store.find_image_by_source(db_name, url)
        if existing_id:
            image_id_updates[eid] = existing_id
            out.append(
                r.model_copy(update={"thumbnail_url": api_image_url(db_name, existing_id)})
            )
            continue

        result = _download(url)
        if result is None:
            image_id_updates[eid] = None
            out.append(r.model_copy(update={"thumbnail_url": None}))
            continue

        data, ctype = result
        ext = image_store.ext_for_content_type(ctype) or ".jpg"
        fname = image_store.file_name_for_source(url, ext)
        image_store.store_image(
            db_name,
            image_id=fname,
            source_url=url,
            data=data,
            content_type=ctype,
        )
        image_id_updates[eid] = fname
        out.append(r.model_copy(update={"thumbnail_url": api_image_url(db_name, fname)}))

    if image_id_updates:
        image_store.bulk_update_event_image_ids(db_name, image_id_updates)

    return out


def garbage_collect(active_event_ids: Iterable[str], *, db_name: str) -> int:
    """Delete poster blobs no longer referenced by any active event."""
    from agent.mongodb import EVENTS_COLLECTION, get_database

    active = {eid for eid in active_event_ids if eid}
    coll = get_database(db_name)[EVENTS_COLLECTION]
    keep_ids: set[str] = set()
    for doc in coll.find({"_id": {"$in": list(active)}}, {"image_id": 1}):
        iid = str(doc.get("image_id") or "").strip()
        if iid:
            keep_ids.add(iid)
    return image_store.delete_images_not_in(db_name, keep_ids)


def dedupe_existing_images(*, db_name: str) -> DedupeStats:
    """No-op for MongoDB — source URL dedup happens at cache time."""
    return DedupeStats()


def dedupe_images_for_all_topics(*, data_base: object = None) -> int:
    """Legacy CLI hook — MongoDB handles dedup per source URL."""
    return 0
