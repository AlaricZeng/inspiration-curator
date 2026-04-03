from __future__ import annotations

import asyncio
import logging
from typing import Literal, Optional  # noqa: F401 — Optional needed for Pydantic on py39

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

import instaloader as _ig_lib

from backend.scraper.browser import PLATFORM_CONFIG, create_session
from backend.scraper.browser import session_exists as xhs_session_exists
import backend.scraper.instagram_loader as ig_loader

router = APIRouter(prefix="/api/auth", tags=["auth"])
_log = logging.getLogger(__name__)

AuthStatus = Literal["authenticated", "missing"]

# Track in-progress login tasks so the UI can show "connecting" state
_connecting: set[str] = set()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PlatformStatus(BaseModel):
    status: AuthStatus
    connecting: bool


class AuthStatusResponse(BaseModel):
    instagram: PlatformStatus
    xiaohongshu: PlatformStatus


class StartAuthResponse(BaseModel):
    started: bool
    platform: str
    detail: Optional[str] = None


class InstagramLoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _platform_status(platform: str) -> PlatformStatus:
    if platform == "instagram":
        exists = ig_loader.session_exists()
    else:
        exists = xhs_session_exists(platform)
    return PlatformStatus(
        status="authenticated" if exists else "missing",
        connecting=platform in _connecting,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status() -> AuthStatusResponse:
    return AuthStatusResponse(
        instagram=_platform_status("instagram"),
        xiaohongshu=_platform_status("xiaohongshu"),
    )


@router.post("/instagram", response_model=StartAuthResponse)
async def auth_instagram(body: InstagramLoginRequest) -> StartAuthResponse:
    """Login to Instagram with username + password via instaloader (no browser)."""
    if "instagram" in _connecting:
        return StartAuthResponse(started=False, platform="instagram", detail="Login already in progress.")

    _connecting.add("instagram")
    try:
        # Run synchronous instaloader login in a thread so we don't block the event loop
        await asyncio.get_event_loop().run_in_executor(
            None, ig_loader.create_session, body.username, body.password
        )
        return StartAuthResponse(started=True, platform="instagram")
    except _ig_lib.exceptions.BadCredentialsException:
        raise HTTPException(status_code=401, detail="Invalid Instagram username or password.")
    except _ig_lib.exceptions.TwoFactorAuthRequiredException:
        raise HTTPException(status_code=422, detail="Two-factor authentication is required. Disable 2FA temporarily or use cookie import (Option 1).")
    except _ig_lib.exceptions.ConnectionException as exc:
        raise HTTPException(status_code=503, detail=f"Instagram connection error: {exc}")
    except Exception as exc:
        _log.error("Instagram login failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        _connecting.discard("instagram")


@router.delete("/instagram", response_model=StartAuthResponse)
async def logout_instagram() -> StartAuthResponse:
    ig_loader.delete_session()
    return StartAuthResponse(started=True, platform="instagram", detail="Session deleted.")


async def _run_xhs_login() -> None:
    _connecting.add("xiaohongshu")
    try:
        await create_session("xiaohongshu")
        _log.info("Xiaohongshu session saved.")
    except Exception as exc:
        _log.error("Xiaohongshu login failed: %s", exc, exc_info=True)
    finally:
        _connecting.discard("xiaohongshu")


@router.post("/xiaohongshu", response_model=StartAuthResponse)
async def auth_xiaohongshu(background_tasks: BackgroundTasks) -> StartAuthResponse:
    """Open a browser window for manual Xiaohongshu login."""
    if "xiaohongshu" in _connecting:
        return StartAuthResponse(started=False, platform="xiaohongshu", detail="Login already in progress.")

    background_tasks.add_task(_run_xhs_login)
    return StartAuthResponse(started=True, platform="xiaohongshu")
