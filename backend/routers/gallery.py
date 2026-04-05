"""Gallery endpoint.

GET    /api/gallery        — past liked posts grouped by date, newest first.
DELETE /api/gallery/{id}   — permanently remove a liked post and its file.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.db.models import Post, PostStatus, engine

logger = logging.getLogger(__name__)
router = APIRouter()


class GalleryPostOut(BaseModel):
    id: str
    platform: str
    creator: str
    engagement: int
    screenshot_url: Optional[str]
    date: str
    keyword: Optional[str]
    run_mode: str  # "keyword" | "vibe"
    vibe_keywords: Optional[list[str]] = None


class GalleryDay(BaseModel):
    date: str
    posts: list[GalleryPostOut]


@router.get("/api/gallery", response_model=list[GalleryDay])
async def get_gallery() -> list[GalleryDay]:
    # Import here to avoid circular dependency at module load
    from backend.routers.today import _screenshot_url

    with Session(engine) as session:
        liked = list(
            session.exec(
                select(Post)
                .where(Post.status == PostStatus.liked)
                .order_by(Post.scraped_at.desc())
            ).all()
        )

    by_date: dict[str, list[GalleryPostOut]] = defaultdict(list)
    for post in liked:
        date_str = post.scraped_at.strftime("%Y-%m-%d")
        by_date[date_str].append(
            GalleryPostOut(
                id=post.id,
                platform=post.platform.value,
                creator=post.creator,
                engagement=post.engagement,
                screenshot_url=_screenshot_url(post.screenshot),
                date=date_str,
                keyword=post.keyword,
                run_mode="keyword" if post.keyword else "vibe",
                vibe_keywords=[kw.strip() for kw in post.vibe_keywords.split(",")] if post.vibe_keywords else None,
            )
        )

    return [
        GalleryDay(date=date, posts=posts)
        for date, posts in sorted(by_date.items(), reverse=True)
    ]


@router.delete("/api/gallery/{post_id}")
async def delete_gallery_post(post_id: str) -> Response:
    """Permanently delete a liked post: removes DB record and image file."""
    with Session(engine) as session:
        post = session.get(Post, post_id)
        if post is None:
            raise HTTPException(status_code=404, detail="Post not found")
        if post.status != PostStatus.liked:
            raise HTTPException(status_code=409, detail="Post is not in the gallery")

        # Delete the image file (best-effort)
        if post.screenshot:
            try:
                Path(post.screenshot).unlink(missing_ok=True)
                logger.info("Deleted gallery image: %s", post.screenshot)
            except Exception as exc:
                logger.warning("Could not delete image file %s: %s", post.screenshot, exc)

        session.delete(post)
        session.commit()
        logger.info("Deleted gallery post %s (@%s)", post_id, post.creator)
    return Response(status_code=204)
