"""Liked image storage and folder organisation — Epic 3."""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from pathlib import Path

from sqlmodel import Session, select

from backend.db.models import Creator, Platform, Post, engine

logger = logging.getLogger(__name__)

INSPIRATION_DIR = Path.home() / "inspiration"


def _platform_prefix(platform: Platform) -> str:
    return "red" if platform == Platform.xiaohongshu else platform.value


def _dest_path(post: Post, day_dir: Path) -> Path:
    """Return next available destination path for *post* inside *day_dir*."""
    prefix = _platform_prefix(post.platform)
    existing = sorted(day_dir.glob(f"{prefix}_*.png"))
    seq = len(existing) + 1
    return day_dir / f"{prefix}_{seq:02d}.png"


def save_liked_screenshot(post: Post) -> str:
    """Copy screenshot from staging/ to ~/inspiration/YYYY-MM-DD/.

    Returns the destination path string.
    Raises FileNotFoundError if the source screenshot is absent.
    """
    if not post.screenshot:
        raise FileNotFoundError(f"Post {post.id} has no screenshot path recorded.")

    src = Path(post.screenshot)
    if not src.exists():
        raise FileNotFoundError(f"Screenshot not found: {src}")

    date_str = post.scraped_at.strftime("%Y-%m-%d")
    day_dir = INSPIRATION_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    dest = _dest_path(post, day_dir)
    shutil.copy2(src, dest)
    logger.info("Saved liked screenshot: %s → %s", src, dest)
    return str(dest)


def delete_staging_screenshot(post: Post) -> None:
    """Delete the staging screenshot for a skipped post (best-effort)."""
    if not post.screenshot:
        return
    src = Path(post.screenshot)
    try:
        src.unlink(missing_ok=True)
        logger.info("Deleted staging screenshot: %s", src)
    except Exception as exc:
        logger.warning("Could not delete staging screenshot %s: %s", src, exc)


def record_liked_metadata(post_id: str) -> None:
    """Immediately record creator and scraped tags when a post is liked.

    This runs synchronously in the like endpoint (no LLM needed) so the data
    is available for the next vibe-mode scrape even if the LLM analysis fails.
    """
    with Session(engine) as session:
        post = session.get(Post, post_id)
        if post is None:
            return

        # Upsert creator
        if post.creator:
            existing = session.exec(
                select(Creator).where(
                    Creator.platform == post.platform,
                    Creator.handle == post.creator,
                )
            ).first()
            if existing:
                existing.liked_count += 1
                session.add(existing)
            else:
                session.add(Creator(platform=post.platform, handle=post.creator, liked_count=1))

        session.commit()
        logger.info(
            "Recorded liked metadata for post %s (creator=%r, tags=%r)",
            post_id, post.creator, post.tags,
        )


async def trigger_vibe_analysis(post_id: str) -> None:
    """Trigger AI vibe analysis for a liked post (Epic 4)."""
    from backend.ai.vibe_engine import analyze_vibe

    await analyze_vibe(post_id)
