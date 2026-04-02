"""Schedule management endpoints.

GET  /api/schedule      — return current daily scrape time
POST /api/schedule      — update daily scrape time  {"time": "HH:MM"}
POST /api/run/now       — trigger an immediate scrape
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, field_validator

from backend.scheduler import get_schedule_time, set_schedule_time

logger = logging.getLogger(__name__)
router = APIRouter()


class ScheduleResponse(BaseModel):
    time: str


class ScheduleUpdate(BaseModel):
    time: str

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("time must be HH:MM")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            raise ValueError("time must be HH:MM")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("hour must be 0–23 and minute must be 0–59")
        return v


class RunNowResponse(BaseModel):
    status: str


@router.get("/api/schedule", response_model=ScheduleResponse)
async def get_schedule() -> ScheduleResponse:
    """Return the currently configured daily scrape time."""
    return ScheduleResponse(time=get_schedule_time())


@router.post("/api/schedule", response_model=ScheduleResponse)
async def update_schedule(body: ScheduleUpdate) -> ScheduleResponse:
    """Update the daily scrape time.  Takes effect immediately."""
    try:
        set_schedule_time(body.time)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ScheduleResponse(time=body.time)


@router.post("/api/run/now", response_model=RunNowResponse)
async def run_now(background_tasks: BackgroundTasks) -> RunNowResponse:
    """Trigger an immediate scrape in the background and return straight away."""
    from backend.scraper.orchestrator import run_scrape  # lazy import

    async def _run() -> None:
        try:
            await run_scrape()
        except Exception:
            logger.exception("Background run_now scrape failed.")

    background_tasks.add_task(_run)
    return RunNowResponse(status="started")
