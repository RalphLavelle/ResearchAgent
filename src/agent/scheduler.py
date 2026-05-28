"""Interval scheduler around APScheduler (interval from .env only)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from agent import config
from agent.workflow import run_once

logger = logging.getLogger(__name__)


def serve() -> None:
    """
    Run the agent on a fixed hourly interval until process exit.

    Set ``SCHEDULE_INTERVAL_HOURS`` in ``.env`` (default 1). Restart ``serve``
    after changing it.
    """
    hours = config.SCHEDULE_INTERVAL_HOURS
    sched = BackgroundScheduler()

    def job() -> None:
        start = time.perf_counter()
        logger.info("Scheduled research pass starting.")
        try:
            run_once(dry_run=False)
        except Exception:
            logger.exception("Scheduled run failed")
        finally:
            logger.info(
                "Scheduled research pass finished in %.1f s.", time.perf_counter() - start
            )

    sched.add_job(
        job,
        IntervalTrigger(hours=hours),
        id="research",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    sched.start()
    logger.info(
        "Schedule: every %s hour(s) (SCHEDULE_INTERVAL_HOURS in .env)", hours
    )
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down scheduler.")
    finally:
        sched.shutdown(wait=False)
