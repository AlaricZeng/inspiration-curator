"""Today endpoints.

GET  /api/today              — today's run status + keyword + posts
POST /api/today/keyword      — set today's keyword
POST /api/posts/{id}/like    — like a post (save to disk, trigger vibe stub)
POST /api/posts/{id}/skip    — skip a post (delete staging screenshot)
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.curator.storage import INSPIRATION_DIR
from backend.db.models import (
    DailyRun,
    Platform,
    PlatformRun,
    Post,
    PostStatus,
    RunMode,
    RunStatus,
    engine,
)
from backend.scraper.orchestrator import STAGING_DIR

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic I/O models
# ---------------------------------------------------------------------------


class PostOut(BaseModel):
    id: str
    platform: str
    creator: str
    engagement: int
    screenshot_url: Optional[str]
    status: str


class PlatformProgress(BaseModel):
    status: str  # pending | running | done | skipped
    post_count: int


class TodayResponse(BaseModel):
    date: str
    status: str
    keyword: Optional[str]
    pending_count: int
    posts: list[PostOut]
    instagram: Optional[PlatformProgress] = None
    xiaohongshu: Optional[PlatformProgress] = None


class KeywordBody(BaseModel):
    keyword: str


class KeywordResponse(BaseModel):
    keyword: str


class ActionResponse(BaseModel):
    status: str
    saved_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _screenshot_url(path: str | None) -> str | None:
    """Convert an absolute filesystem path to a proxied /screenshots/* URL."""
    if not path:
        return None
    p = Path(path)
    try:
        rel = p.relative_to(INSPIRATION_DIR)
        return f"/screenshots/inspiration/{rel.as_posix()}"
    except ValueError:
        pass
    try:
        rel = p.relative_to(STAGING_DIR)
        return f"/screenshots/staging/{rel.as_posix()}"
    except ValueError:
        pass
    return None


def _posts_for_today(session: Session, today: dt.date) -> list[Post]:
    start = dt.datetime.combine(today, dt.time.min)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min)
    return list(
        session.exec(
            select(Post).where(Post.scraped_at >= start, Post.scraped_at < end)
        ).all()
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/today", response_model=TodayResponse)
async def get_today() -> TodayResponse:
    today = dt.date.today()
    with Session(engine) as session:
        run = session.exec(
            select(DailyRun).where(DailyRun.run_date == today)
        ).first()

        posts = _posts_for_today(session, today) if run is not None else []
        pending_count = sum(1 for p in posts if p.status == PostStatus.pending)

        ig_progress: Optional[PlatformProgress] = None
        xhs_progress: Optional[PlatformProgress] = None
        if run is not None:
            platform_runs = session.exec(
                select(PlatformRun).where(PlatformRun.run_id == run.id)
            ).all()
            for pr in platform_runs:
                prog = PlatformProgress(status=pr.status.value, post_count=pr.post_count)
                if pr.platform == Platform.instagram:
                    ig_progress = prog
                else:
                    xhs_progress = prog

        return TodayResponse(
            date=today.isoformat(),
            status=run.status.value if run else RunStatus.pending.value,
            keyword=run.keyword if run else None,
            pending_count=pending_count,
            posts=[
                PostOut(
                    id=p.id,
                    platform=p.platform.value,
                    creator=p.creator,
                    engagement=p.engagement,
                    screenshot_url=_screenshot_url(p.screenshot),
                    status=p.status.value,
                )
                for p in posts
            ],
            instagram=ig_progress,
            xiaohongshu=xhs_progress,
        )


@router.post("/api/today/keyword", response_model=KeywordResponse)
async def set_today_keyword(body: KeywordBody) -> KeywordResponse:
    today = dt.date.today()
    with Session(engine) as session:
        run = session.exec(
            select(DailyRun).where(DailyRun.run_date == today)
        ).first()
        if run is None:
            run = DailyRun(
                run_date=today,
                status=RunStatus.pending,
                mode=RunMode.keyword,
                keyword=body.keyword,
            )
            session.add(run)
        else:
            run.keyword = body.keyword
            run.mode = RunMode.keyword
            session.add(run)
        session.commit()
    return KeywordResponse(keyword=body.keyword)


@router.post("/api/posts/{post_id}/like", response_model=ActionResponse)
async def like_post(post_id: str, background_tasks: BackgroundTasks) -> ActionResponse:
    from backend.curator.storage import save_liked_screenshot, trigger_vibe_analysis

    with Session(engine) as session:
        post = session.get(Post, post_id)
        if post is None:
            raise HTTPException(status_code=404, detail="Post not found")
        if post.status != PostStatus.pending:
            raise HTTPException(status_code=409, detail=f"Post already {post.status.value}")

        try:
            saved_path = save_liked_screenshot(post)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        post.status = PostStatus.liked
        post.screenshot = saved_path
        session.add(post)
        session.commit()

    background_tasks.add_task(trigger_vibe_analysis, post_id)
    return ActionResponse(status="liked", saved_path=saved_path)


@router.post("/api/posts/{post_id}/skip", response_model=ActionResponse)
async def skip_post(post_id: str) -> ActionResponse:
    from backend.curator.storage import delete_staging_screenshot

    with Session(engine) as session:
        post = session.get(Post, post_id)
        if post is None:
            raise HTTPException(status_code=404, detail="Post not found")
        if post.status != PostStatus.pending:
            raise HTTPException(status_code=409, detail=f"Post already {post.status.value}")

        delete_staging_screenshot(post)
        post.status = PostStatus.skipped
        session.add(post)
        session.commit()

    return ActionResponse(status="skipped")
