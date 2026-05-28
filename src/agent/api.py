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
from agent.json_output import build_events_payload_from_rows
from agent.image_store import fetch_image
from agent.mongodb import EVENTS_COLLECTION, get_database
from agent.report_store import list_reports
from agent.topics import load_topics
from agent import venue_store

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
        coll = get_database(db_name)[EVENTS_COLLECTION]
        from agent.image_cache import api_image_url

        thumbnail_urls: dict[str, str | None] = {}
        for doc in coll.find({}, {"_id": 1, "image_id": 1}):
            eid = str(doc["_id"])
            iid = str(doc.get("image_id") or "").strip()
            thumbnail_urls[eid] = api_image_url(db_name, iid) if iid else None

        payload: dict[str, Any] = build_events_payload_from_rows(
            rows,
            thumbnail_urls=thumbnail_urls,
        )
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


def _venue_to_api(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id") or ""),
        "name": str(doc.get("name") or ""),
        "aliases": [str(alias) for alias in (doc.get("aliases") or [])],
    }


async def get_venues(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        if request.query_params.get("all") == "true":
            docs = venue_store.list_venues(db_name)
            return JSONResponse(
                {
                    "venues": [_venue_to_api(doc) for doc in docs],
                    "total": len(docs),
                    "limit": len(docs),
                    "skip": 0,
                }
            )
        limit_raw = request.query_params.get("limit", "50")
        skip_raw = request.query_params.get("skip", "0")
        limit = max(1, min(50, int(limit_raw)))
        skip = max(0, int(skip_raw))
        docs, total = venue_store.list_venues_page(db_name, limit=limit, skip=skip)
        return JSONResponse(
            {
                "venues": [_venue_to_api(doc) for doc in docs],
                "total": total,
                "limit": limit,
                "skip": skip,
            }
        )
    except ValueError:
        return JSONResponse({"error": "Invalid limit or skip query parameter"}, status_code=400)
    except Exception as exc:
        logger.exception("API venues error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def get_venue(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    venue_id = request.path_params["venue_id"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        doc = venue_store.get_venue(db_name, venue_id)
        if not doc:
            return JSONResponse({"error": "Venue not found"}, status_code=404)
        payload = venue_store.venue_document_to_json(doc)
        payload["linkedEventCount"] = venue_store.count_events_for_venue(db_name, venue_id)
        return JSONResponse(payload)
    except Exception as exc:
        logger.exception("API venue read error for db=%s id=%s", db_name, venue_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def put_venue(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    venue_id = request.path_params["venue_id"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        saved = venue_store.update_venue(db_name, venue_id, body)
        return JSONResponse(venue_store.venue_document_to_json(saved))
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("API venue update error for db=%s id=%s", db_name, venue_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def delete_venue(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    venue_id = request.path_params["venue_id"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        replacement_id = str(body.get("replacementVenueId") or "").strip()
        if not replacement_id:
            return JSONResponse({"error": "replacementVenueId is required"}, status_code=400)
        stats = venue_store.delete_venue(
            db_name,
            venue_id,
            replacement_venue_id=replacement_id,
        )
        return JSONResponse(stats)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("API venue delete error for db=%s id=%s", db_name, venue_id)
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
            Route("/api/{db}/venues", get_venues, methods=["GET"]),
            Route("/api/{db}/venues/{venue_id}", get_venue, methods=["GET"]),
            Route("/api/{db}/venues/{venue_id}", put_venue, methods=["PUT"]),
            Route("/api/{db}/venues/{venue_id}", delete_venue, methods=["DELETE"]),
            Route("/api/{db}/images/{image_id}", get_image, methods=["GET"]),
            Route("/health", lambda r: JSONResponse({"ok": True}), methods=["GET"]),
        ],
    )


app = create_app()
