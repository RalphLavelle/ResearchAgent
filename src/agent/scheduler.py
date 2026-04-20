"""Reloadable interval scheduler around APScheduler."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from agent import config
from agent.workflow import run_once

logger = logging.getLogger(__name__)

POLL_SEC = 45.0


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _load_interval_hours(path: Path) -> float:
    if not path.exists():
        return 1.0
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        h = float(raw.get("interval_hours", 1.0))
        return max(0.05, h)  # at least 3 minutes
    except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
        logger.warning("Bad schedule file %s: %s; using 1h", path, exc)
        return 1.0


def serve() -> None:
    """
    Run the agent on a reloadable interval until process exit.

    Edit config/schedule.yaml (interval_hours) while running; the next poll picks it up.
    """
    sched = BackgroundScheduler()
    path = config.SCHEDULE_CONFIG_PATH
    last_mtime: float = 0.0
    current_hours: float | None = None

    def job() -> None:
        try:
            run_once(dry_run=False)
        except Exception:
            logger.exception("Scheduled run failed")

    def reschedule_if_needed() -> None:
        nonlocal last_mtime, current_hours
        mtime = _safe_mtime(path)

        if mtime == last_mtime and current_hours is not None:
            return
        last_mtime = mtime
        hours = _load_interval_hours(path)
        if current_hours == hours and sched.get_jobs():
            return
        current_hours = hours
        sched.remove_all_jobs()
        sched.add_job(
            job,
            IntervalTrigger(hours=hours),
            id="research",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
        )
        logger.info("Schedule: every %s hour(s) (from %s)", hours, path)

    sched.start()
    reschedule_if_needed()
    try:
        while True:
            reschedule_if_needed()
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        logger.info("Shutting down scheduler.")
    finally:
        sched.shutdown(wait=False)
