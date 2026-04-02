"""Vision LLM vibe keyword extraction — Epic 4."""

from __future__ import annotations

import base64
import datetime
import logging
import os
from pathlib import Path

from sqlmodel import Session, select

from backend.db.models import Creator, Platform, Post, VibeKeyword, engine

logger = logging.getLogger(__name__)

VIBE_PROMPT = (
    "Describe the visual style, mood, color palette, and composition of this image "
    "in 5 to 8 short keywords. Focus on aesthetic vibe, not content. "
    "Examples: moody, film grain, muted tones, golden hour, minimalist, high contrast, "
    "pastel, cinematic. Return only a comma-separated list of keywords, nothing else."
)


# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------


def _encode_image(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


async def _call_openai(image_path: str) -> str:
    import openai  # type: ignore

    client = openai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    b64 = _encode_image(image_path)
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": VIBE_PROMPT},
                ],
            }
        ],
        max_tokens=100,
    )
    return response.choices[0].message.content or ""


async def _call_anthropic(image_path: str) -> str:
    import anthropic  # type: ignore

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    b64 = _encode_image(image_path)
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": VIBE_PROMPT},
                ],
            }
        ],
    )
    return message.content[0].text if message.content else ""


# ---------------------------------------------------------------------------
# Parsing + DB upserts
# ---------------------------------------------------------------------------


def _parse_keywords(raw: str) -> list[str]:
    return [kw.strip().lower() for kw in raw.split(",") if kw.strip()]


def _upsert_keywords(keywords: list[str]) -> None:
    now = datetime.datetime.utcnow()
    with Session(engine) as session:
        for kw in keywords:
            existing = session.get(VibeKeyword, kw)
            if existing:
                existing.frequency += 1
                existing.last_seen = now
                session.add(existing)
            else:
                session.add(VibeKeyword(keyword=kw, frequency=1, last_seen=now))
        session.commit()


def _upsert_creator(platform: Platform, handle: str) -> None:
    with Session(engine) as session:
        existing = session.exec(
            select(Creator).where(
                Creator.platform == platform,
                Creator.handle == handle,
            )
        ).first()
        if existing:
            existing.liked_count += 1
            session.add(existing)
        else:
            session.add(Creator(platform=platform, handle=handle, liked_count=1))
        session.commit()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def analyze_vibe(post_id: str) -> None:
    """Analyze aesthetic vibe of a liked post and upsert keywords + creator.

    Called as a FastAPI background task immediately after a post is liked.
    """
    with Session(engine) as session:
        post = session.get(Post, post_id)
        if post is None:
            logger.warning("analyze_vibe: post %s not found.", post_id)
            return
        screenshot = post.screenshot
        platform = post.platform
        handle = post.creator

    if not screenshot or not Path(screenshot).exists():
        logger.warning("analyze_vibe: screenshot missing for post %s.", post_id)
        return

    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    try:
        if provider == "anthropic":
            raw = await _call_anthropic(screenshot)
        else:
            raw = await _call_openai(screenshot)
    except Exception as exc:
        logger.error("analyze_vibe: LLM call failed for post %s: %s", post_id, exc)
        return

    keywords = _parse_keywords(raw)
    if not keywords:
        logger.warning("analyze_vibe: no keywords extracted for post %s.", post_id)
        return

    logger.info("analyze_vibe: post %s → %s", post_id, keywords)
    _upsert_keywords(keywords)
    _upsert_creator(platform, handle)
