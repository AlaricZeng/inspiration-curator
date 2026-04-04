"""APScheduler setup for daily scrape jobs."""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()

# Module-level cache of the current schedule time (HH:MM).
# Initialised from the SCRAPE_TIME env var; updated via set_schedule_time().
_current_time: str = os.getenv("SCRAPE_TIME", "08:00")


async def _daily_scrape() -> None:
    """Entry point for the scheduled daily scrape job."""
    from backend.scraper.orchestrator import run_scrape  # avoid circular import at module load

    logger.info("Daily scrape triggered by scheduler.")
    try:
        await run_scrape(force=True)
    except Exception:
        logger.exception("Unhandled error in daily scrape job.")


def get_schedule_time() -> str:
    """Return the current schedule time as 'HH:MM'."""
    return _current_time


def set_schedule_time(time: str) -> None:
    """Update the daily scrape schedule to *time* (format 'HH:MM').

    Reschedules the APScheduler job immediately; persists in-memory for the
    lifetime of the process.  Set SCRAPE_TIME in .env to survive restarts.

    Raises:
        ValueError: if *time* is not a valid 'HH:MM' string.
    """
    global _current_time

    try:
        hour_s, minute_s = time.split(":")
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"Invalid time format {time!r} — expected HH:MM (e.g. '08:30')."
        ) from exc

    _current_time = time

    if _scheduler.running:
        _scheduler.reschedule_job(
            "daily_scrape",
            trigger=CronTrigger(hour=hour, minute=minute),
        )
        logger.info("Rescheduled daily scrape to %s.", time)


def start_scheduler() -> None:
    hour, minute = _current_time.split(":")
    _scheduler.add_job(
        _daily_scrape,
        CronTrigger(hour=int(hour), minute=int(minute)),
        id="daily_scrape",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started; daily scrape at %s.", _current_time)


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
