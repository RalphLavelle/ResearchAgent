"""HTTP API for the Angular app — events and poster images from MongoDB."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agent import config
from agent.event_search import load_search_api_payload
from agent.event_store import load_events_api_payload, load_spotlight_api_payload
from agent.image_store import fetch_image
from agent.mongodb import get_database
from agent.report_store import list_reports
from agent.runner import LLMInvocationError, LLMNotReadyError, execute_run_once
from agent.topics import load_topics
from agent import user_store, venue_store

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


# NOTE: Read-only handlers are plain ``def`` (not ``async def``) on purpose.
# They call blocking PyMongo. Starlette runs sync endpoints in a worker
# threadpool, so a slow DB call never freezes the single event loop and
# concurrent requests (e.g. the events JSON racing ~100 poster-image loads)
# no longer serialise behind each other.
def get_events(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        payload: dict[str, Any] = load_events_api_payload(db_name)
        return JSONResponse(payload)
    except Exception as exc:
        logger.exception("API events error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def post_events_search(request: Request) -> JSONResponse:
    """Natural-language search over the display-window events (LLM-filtered)."""
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        query = str(body.get("query") or "").strip()
        if not query:
            return JSONResponse({"error": "query is required"}, status_code=400)
        payload = await run_in_threadpool(load_search_api_payload, db_name, query)
        return JSONResponse(payload)
    except LLMNotReadyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except LLMInvocationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception as exc:
        logger.exception("API events search error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


def get_events_spotlight(request: Request) -> JSONResponse:
    """Return up to four random upcoming events that have cached poster images."""
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        limit_raw = request.query_params.get("limit", "4")
        limit = max(1, min(4, int(limit_raw)))
        exclude_raw = request.query_params.get("exclude", "")
        exclude_ids = {part.strip() for part in exclude_raw.split(",") if part.strip()}
        payload = load_spotlight_api_payload(db_name, limit=limit, exclude_ids=exclude_ids)
        return JSONResponse(payload)
    except ValueError:
        return JSONResponse({"error": "Invalid limit query parameter"}, status_code=400)
    except Exception as exc:
        logger.exception("API events spotlight error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


def get_reports(request: Request) -> JSONResponse:
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
        "location": str(doc.get("location") or ""),
        # Venue-first mining fields (Task 1) — shown on the admin venues page.
        "events_link": str(doc.get("events_link") or ""),
        "last_event_date": str(doc.get("last_event_date") or ""),
    }


def get_venues(request: Request) -> JSONResponse:
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


def get_venue(request: Request) -> JSONResponse:
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
        # Reading the body needs ``await`` (async handler), but the blocking
        # MongoDB write is offloaded so it never stalls the event loop.
        saved = await run_in_threadpool(venue_store.update_venue, db_name, venue_id, body)
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
        delete_linked = bool(body.get("deleteLinkedEvents"))
        replacement_id = str(body.get("replacementVenueId") or "").strip() or None
        if not delete_linked and not replacement_id:
            linked = await run_in_threadpool(
                venue_store.count_events_for_venue, db_name, venue_id
            )
            if linked:
                return JSONResponse(
                    {
                        "error": (
                            "replacementVenueId is required when not "
                            "deleting linked events"
                        )
                    },
                    status_code=400,
                )
        # Offload the blocking re-link / event delete + venue delete.
        stats = await run_in_threadpool(
            venue_store.delete_venue,
            db_name,
            venue_id,
            replacement_venue_id=replacement_id,
            delete_linked_events=delete_linked,
        )
        return JSONResponse(stats)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("API venue delete error for db=%s id=%s", db_name, venue_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


def _user_to_api(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id") or ""),
        "email": str(doc.get("email") or ""),
        "subscribed_at": str(doc.get("subscribed_at") or ""),
    }


def get_users(request: Request) -> JSONResponse:
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        limit_raw = request.query_params.get("limit", "50")
        skip_raw = request.query_params.get("skip", "0")
        limit = max(1, min(50, int(limit_raw)))
        skip = max(0, int(skip_raw))
        docs, total = user_store.list_users_page(db_name, limit=limit, skip=skip)
        return JSONResponse(
            {
                "users": [_user_to_api(doc) for doc in docs],
                "total": total,
                "limit": limit,
                "skip": skip,
            }
        )
    except ValueError:
        return JSONResponse({"error": "Invalid limit or skip query parameter"}, status_code=400)
    except Exception as exc:
        logger.exception("API users error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def post_user_subscribe(request: Request) -> JSONResponse:
    """Save an email address for the weekly digest (``users`` collection)."""
    db_key = request.path_params["db"]
    db_name = _resolve_db(db_key)
    if not db_name:
        return JSONResponse({"error": "Unknown topic"}, status_code=404)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        email = str(body.get("email") or "").strip()
        if not email:
            return JSONResponse({"error": "email is required"}, status_code=400)
        saved = await run_in_threadpool(user_store.subscribe, db_name, email)
        return JSONResponse(
            {
                "email": str(saved.get("email") or ""),
                "subscribed_at": str(saved.get("subscribed_at") or ""),
            }
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("API user subscribe error for db=%s", db_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def post_admin_run_once(request: Request) -> JSONResponse:
    """Run one full research pipeline pass (same as ``python -m agent run-once``)."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        password = str(body.get("password") or "")
        expected = config.ADMIN_PASSWORD
        if not expected:
            return JSONResponse(
                {"error": "Admin password is not configured on the server"},
                status_code=503,
            )
        if not secrets.compare_digest(password, expected):
            return JSONResponse({"error": "Incorrect password"}, status_code=401)

        def _run() -> str:
            result = execute_run_once(dry_run=False)
            return str(result.get("run_log_message") or "")

        message = await run_in_threadpool(_run)
        return JSONResponse({"ok": True, "message": message})
    except LLMNotReadyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except LLMInvocationError as exc:
        # Planner (first) LLM call failed — pipeline aborted on purpose.
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception as exc:
        logger.exception("API admin run-once error")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def post_admin_verify_password(request: Request) -> JSONResponse:
    """Check the admin password from ``ADMIN_PASSWORD`` in ``.env``."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        password = str(body.get("password") or "")
        expected = config.ADMIN_PASSWORD
        if not expected:
            return JSONResponse(
                {"error": "Admin password is not configured on the server"},
                status_code=503,
            )
        if not secrets.compare_digest(password, expected):
            return JSONResponse({"error": "Incorrect password"}, status_code=401)
        return JSONResponse({"ok": True})
    except Exception as exc:
        logger.exception("API admin verify-password error")
        return JSONResponse({"error": str(exc)}, status_code=500)


def get_image(request: Request) -> Response:
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
            Route("/api/{db}/events/spotlight", get_events_spotlight, methods=["GET"]),
            Route("/api/{db}/events/search", post_events_search, methods=["POST"]),
            Route("/api/{db}/events", get_events, methods=["GET"]),
            Route("/api/{db}/reports", get_reports, methods=["GET"]),
            Route("/api/{db}/venues", get_venues, methods=["GET"]),
            Route("/api/{db}/venues/{venue_id}", get_venue, methods=["GET"]),
            Route("/api/{db}/venues/{venue_id}", put_venue, methods=["PUT"]),
            Route("/api/{db}/venues/{venue_id}", delete_venue, methods=["DELETE"]),
            Route("/api/{db}/users", get_users, methods=["GET"]),
            Route("/api/{db}/users/subscribe", post_user_subscribe, methods=["POST"]),
            Route("/api/{db}/images/{image_id}", get_image, methods=["GET"]),
            Route("/api/admin/run-once", post_admin_run_once, methods=["POST"]),
            Route("/api/admin/verify-password", post_admin_verify_password, methods=["POST"]),
            Route("/health", lambda r: JSONResponse({"ok": True}), methods=["GET"]),
        ],
    )


app = create_app()
