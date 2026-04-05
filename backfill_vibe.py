"""One-off script: backfill vibe_keywords for all liked posts that have none."""
import asyncio
import logging

from sqlmodel import Session, select

from backend.db.models import Post, PostStatus, engine
from backend.ai.vibe_engine import analyze_vibe

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    with Session(engine) as session:
        posts = list(
            session.exec(
                select(Post).where(
                    Post.status == PostStatus.liked,
                    Post.vibe_keywords == None,  # noqa: E711
                )
            ).all()
        )

    logger.info("Found %d liked posts without vibe keywords.", len(posts))

    for i, post in enumerate(posts, 1):
        logger.info("[%d/%d] Analyzing post %s (@%s)…", i, len(posts), post.id, post.creator)
        await analyze_vibe(post.id)

    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
