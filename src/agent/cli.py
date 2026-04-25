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

    sub.add_parser("serve", help="Run on reloadable interval (see config/schedule.yaml).")

    args = parser.parse_args(argv)

    if args.command == "run-once":
        result = run_once(dry_run=args.dry_run)
        msg = result.get("run_log_message", "")
        print(msg)
        return 0

    if args.command == "serve":
        serve()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
