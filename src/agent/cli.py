"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys

from agent.runner import LLMNotReadyError, execute_run_once
from agent.scheduler import serve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Research subjects and save curated events to MongoDB.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_once = sub.add_parser("run-once", help="Run a single research + save pass.")
    p_once.add_argument(
        "--dry-run",
        action="store_true",
        help="Curate results only; do not write to MongoDB or snapshot.",
    )

    sub.add_parser(
        "serve",
        help="Run on a fixed interval (SCHEDULE_INTERVAL_HOURS in .env).",
    )

    p_migrate = sub.add_parser(
        "migrate-mongodb",
        help="Move legacy spreadsheet/events.json/images into MongoDB.",
    )
    p_migrate.add_argument(
        "--keep-files",
        action="store_true",
        help="Do not delete legacy files after migration.",
    )

    sub.add_parser(
        "migrate-venues",
        help="Link existing event venue strings to the venues collection.",
    )

    p_api = sub.add_parser("api", help="Run the HTTP API for the Angular app.")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)

    from agent.llm_factory import verify_llm_at_startup
    from agent.runner import prepare_run_environment

    prepare_run_environment()

    if args.command == "run-once":
        try:
            result = execute_run_once(dry_run=args.dry_run)
        except LLMNotReadyError as exc:
            logger.error("%s", exc)
            return 3
        msg = result.get("run_log_message", "")
        print(msg)
        return 0

    if args.command == "serve":
        if not verify_llm_at_startup():
            logger.error(
                "LLM backend is not reachable or misconfigured — fix .env then retry."
            )
            return 3
        serve()
        return 0

    if args.command == "migrate-mongodb":
        from agent.migrate_mongodb import migrate_all_topics
        from agent.mongodb import validate_mongodb_uri

        try:
            validate_mongodb_uri()
        except ValueError as exc:
            logger.error("%s", exc)
            return 2
        try:
            results = migrate_all_topics(remove_files=not args.keep_files)
        except Exception as exc:
            logger.error("Migration failed: %s", exc)
            return 1
        for topic_id, stats in results.items():
            print(
                f"{topic_id}: {stats['events']} events, "
                f"{stats['images']} images, "
                f"{stats['files_removed']} legacy file(s) removed"
            )
        return 0

    if args.command == "migrate-venues":
        from agent.migrate_venues import migrate_all_topic_venues
        from agent.mongodb import validate_mongodb_uri

        try:
            validate_mongodb_uri()
        except ValueError as exc:
            logger.error("%s", exc)
            return 2
        try:
            results = migrate_all_topic_venues()
        except Exception as exc:
            logger.error("Venue migration failed: %s", exc)
            return 1
        for topic_id, stats in results.items():
            print(
                f"{topic_id}: {stats['events_linked']} event(s) linked, "
                f"{stats['venues_total']} venue(s) total "
                f"({stats['venues_created']} new, "
                f"{stats['lookup_keys_removed']} legacy key field(s) removed)"
            )
        return 0

    if args.command == "api":
        import uvicorn

        uvicorn.run("agent.api:app", host=args.host, port=args.port, log_level="info")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
