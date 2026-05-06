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


def _load_interval(path: Path) -> tuple[str, int | float]:
    """Read the schedule YAML and return (unit, value) for APScheduler.

    Rules:
    - If ``interval_minutes`` is present **and greater than 0**, it takes
      priority and the ``interval_hours`` value is ignored entirely.
    - Otherwise ``interval_hours`` is used (default 1).

    Returns a tuple like ``("minutes", 5)`` or ``("hours", 1)`` so the
    caller can pass it directly to ``IntervalTrigger``.
    """
    if not path.exists():
        return "hours", 1.0
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        minutes = float(raw.get("interval_minutes", 0))
        if minutes > 0:
            # interval_minutes is set — use it regardless of interval_hours
            return "minutes", max(1.0, minutes)
        hours = float(raw.get("interval_hours", 1.0))
        return "hours", max(0.05, hours)
    except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
        logger.warning("Bad schedule file %s: %s; using 1h", path, exc)
        return "hours", 1.0


def serve() -> None:
    """
    Run the agent on a reloadable interval until process exit.

    Edit config/schedule.yaml while running; the next poll picks up the change.
    Set ``interval_minutes`` > 0 to override ``interval_hours`` entirely.
    """
    sched = BackgroundScheduler()
    path = config.SCHEDULE_CONFIG_PATH
    last_mtime: float = 0.0
    current_interval: tuple[str, int | float] | None = None

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

    def reschedule_if_needed() -> None:
        nonlocal last_mtime, current_interval
        mtime = _safe_mtime(path)

        if mtime == last_mtime and current_interval is not None:
            return
        last_mtime = mtime
        unit, value = _load_interval(path)
        if current_interval == (unit, value) and sched.get_jobs():
            return
        current_interval = (unit, value)
        sched.remove_all_jobs()
        sched.add_job(
            job,
            IntervalTrigger(**{unit: value}),
            id="research",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
        )
        logger.info("Schedule: every %s %s (from %s)", value, unit, path)

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
