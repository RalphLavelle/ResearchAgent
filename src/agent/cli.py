"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys

from agent.scheduler import serve
from agent.workflow import run_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Research subjects and save to a nominated Notion page.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_once = sub.add_parser("run-once", help="Run a single research + sync pass.")
    p_once.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write Markdown files or snapshot.",
    )

    sub.add_parser("serve", help="Run on reloadable interval (see topics/<id>/schedule.yaml).")

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

    from agent import config
    from agent.image_cache import dedupe_images_for_all_topics
    from agent.llm_factory import verify_llm_at_startup

    try:
        removed = dedupe_images_for_all_topics(data_base=config.DATA_BASE_DIR)
        if removed:
            logger.info(
                "Removed %s duplicate poster file(s) across topic image caches.",
                removed,
            )
    except OSError as exc:
        logger.warning("Image dedupe migration skipped: %s", exc)

    if args.command in ("run-once", "serve") and not verify_llm_at_startup():
        logger.error(
            "LLM backend is not reachable or misconfigured — fix .env then retry."
        )
        return 3

    if args.command == "run-once":
        result = run_once(dry_run=args.dry_run)
        msg = result.get("run_log_message", "")
        print(msg)
        return 0

    if args.command == "serve":
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
