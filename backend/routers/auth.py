from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend.scraper.browser import PLATFORM_CONFIG, create_session, session_exists

router = APIRouter(prefix="/api/auth", tags=["auth"])

AuthStatus = Literal["authenticated", "missing"]

# Track in-progress login tasks so the UI can show "connecting" state
_connecting: set[str] = set()


class PlatformStatus(BaseModel):
    status: AuthStatus
    connecting: bool


class AuthStatusResponse(BaseModel):
    instagram: PlatformStatus
    xiaohongshu: PlatformStatus


class StartAuthResponse(BaseModel):
    started: bool
    platform: str


def _platform_status(platform: str) -> PlatformStatus:
    return PlatformStatus(
        status="authenticated" if session_exists(platform) else "missing",
        connecting=platform in _connecting,
    )


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status() -> AuthStatusResponse:
    return AuthStatusResponse(
        instagram=_platform_status("instagram"),
        xiaohongshu=_platform_status("xiaohongshu"),
    )


async def _run_login(platform: str) -> None:
    _connecting.add(platform)
    try:
        await create_session(platform)
    except Exception:
        pass
    finally:
        _connecting.discard(platform)


@router.post("/{platform}", response_model=StartAuthResponse)
async def start_auth(
    platform: str, background_tasks: BackgroundTasks
) -> StartAuthResponse:
    if platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")

    if platform in _connecting:
        return StartAuthResponse(started=False, platform=platform)

    background_tasks.add_task(_run_login, platform)
    return StartAuthResponse(started=True, platform=platform)
