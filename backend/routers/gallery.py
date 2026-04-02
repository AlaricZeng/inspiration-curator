"""Gallery endpoint.

GET /api/gallery — past liked posts grouped by date, newest first.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter
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
    screenshot_url: str | None


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
            )
        )

    return [
        GalleryDay(date=date, posts=posts)
        for date, posts in sorted(by_date.items(), reverse=True)
    ]
