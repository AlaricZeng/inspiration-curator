"""First-run keyword seeding — Epic 4."""

from __future__ import annotations

import datetime

from sqlmodel import Session, select

from backend.db.models import Post, PostStatus, VibeKeyword, engine

PRESET_KEYWORDS: dict[str, list[str]] = {
    "Street Photography": [
        "gritty", "urban", "candid", "high contrast", "monochrome", "film grain", "raw",
    ],
    "Architecture": [
        "minimalist", "geometric", "clean lines", "concrete", "symmetry", "modern", "structured",
    ],
    "Portrait": [
        "intimate", "soft light", "bokeh", "warm tones", "expressive", "editorial", "natural light",
    ],
    "Nature": [
        "earthy", "golden hour", "muted greens", "atmospheric", "organic", "serene", "textured",
    ],
    "Minimal Design": [
        "white space", "clean", "typographic", "sparse", "monochromatic", "precise", "flat",
    ],
    "Film": [
        "cinematic", "film grain", "muted tones", "nostalgic", "letterbox", "moody", "vintage",
    ],
    "Fashion": [
        "editorial", "bold colors", "high contrast", "glossy", "structured", "avant-garde", "sleek",
    ],
}


def seed_needed() -> bool:
    """Return True if VibeKeyword table is empty."""
    with Session(engine) as session:
        kw = session.exec(select(VibeKeyword).limit(1)).first()
        return kw is None


def liked_post_count() -> int:
    """Return the total number of liked posts."""
    with Session(engine) as session:
        return len(
            session.exec(select(Post).where(Post.status == PostStatus.liked)).all()
        )


def seed_preset(preset: str) -> None:
    """Insert preset keywords into VibeKeyword table (skip if already present)."""
    keywords = PRESET_KEYWORDS.get(preset)
    if not keywords:
        raise ValueError(f"Unknown preset: {preset!r}")

    now = datetime.datetime.utcnow()
    with Session(engine) as session:
        for kw in keywords:
            if session.get(VibeKeyword, kw) is None:
                session.add(VibeKeyword(keyword=kw, frequency=1, last_seen=now))
        session.commit()
