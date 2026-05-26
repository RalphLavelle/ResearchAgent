"""HTTP API for the Angular app — events and poster images from MongoDB."""

from __future__ import annotations

import logging
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agent import config
from agent.event_store import load_existing_rows
from agent.json_output import build_events_payload
from agent.image_store import fetch_image
from agent.local_output import _row_to_resource
from agent.mongodb import EVENTS_COLLECTION, get_database
from agent.report_store import list_reports
from agent.topics import load_topics

logger = logging.getLogger(__name__)


def _topic_db_names() -> dict[str, str]:
    """Map topic id → MongoDB database name."""
    reg = load_topics(config.TOPICS_CONFIG_PATH)
    return {tid: entry.db for tid, entry in reg.topics.items()}


def _resolve_db(topic_or_db: str) -> str | None:
    """Accept topic id or raw db name."""
    names = _topic_db_names()
    if topic_or_db in names:
        return names[topic_or_db]
    if topic_or_db in names.values():
        return topic_or_db
    return topic_or_db or None


async def get_events(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        rows = load_existing_rows(db_name)
        resources = [_row_to_resource(row) for row in rows.values()]
        # Apply cached poster API URLs from event image_id fields.
        coll = get_database(db_name)[EVENTS_COLLECTION]
        image_map = {
            str(doc["_id"]): str(doc.get("image_id") or "")
            for doc in coll.find({}, {"_id": 1, "image_id": 1})
        }
        from agent.image_cache import api_image_url

        enriched = []
        for r in resources:
            iid = image_map.get(r.id, "").strip()
            if iid:
                enriched.append(
                    r.model_copy(update={"thumbnail_url": api_image_url(db_name, iid)})
                )
            else:
                enriched.append(r)
        payload: dict[str, Any] = build_events_payload(enriched)
        return JSONResponse(payload)
    except Exception as exc:
        logger.exception("API events error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def get_reports(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        limit_raw = request.query_params.get("limit", "100")
        limit = max(1, min(500, int(limit_raw)))
        return JSONResponse({"reports": list_reports(db_name, limit=limit)})
    except ValueError:
        return JSONResponse({"error": "Invalid limit query parameter"}, status_code=400)
    except Exception as exc:
        logger.exception("API reports error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def get_image(request: Request) -> Response:
    db_key = request.path_params["db"]
    image_id = request.path_params["image_id"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return Response(status_code=404)
    try:
        fetched = fetch_image(db_name, image_id)
        if not fetched:
            return Response(status_code=404)
        data, content_type = fetched
        return Response(content=data, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        logger.exception("API image error db=%s id=%s", db_name, image_id)
        return Response(status_code=500)


def create_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/api/{db}/events", get_events, methods=["GET"]),
            Route("/api/{db}/reports", get_reports, methods=["GET"]),
            Route("/api/{db}/images/{image_id}", get_image, methods=["GET"]),
            Route("/health", lambda r: JSONResponse({"ok": True}), methods=["GET"]),
        ],
    )


app = create_app()
