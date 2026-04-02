"""Taste profile endpoints — Epic 4.

GET    /api/taste               — current taste profile (keywords + creators)
PATCH  /api/taste/keywords      — pin / block / add keywords
GET    /api/taste/seed-needed   — true if seed UI should be shown
POST   /api/taste/seed          — seed keywords for a style preset
GET    /api/creators            — tracked creators list
DELETE /api/creators/{id}       — remove a creator
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.ai.seed import PRESET_KEYWORDS, liked_post_count, seed_needed, seed_preset
from backend.db.models import Creator, VibeKeyword, engine

router = APIRouter()

_SEED_BYPASS_LIKED_COUNT = 3  # hide seed UI once this many posts have been liked


# ---------------------------------------------------------------------------
# Pydantic I/O models
# ---------------------------------------------------------------------------


class VibeKeywordOut(BaseModel):
    keyword: str
    frequency: int
    user_pinned: bool
    user_blocked: bool


class CreatorOut(BaseModel):
    id: str
    platform: str
    handle: str
    liked_count: int


class TasteResponse(BaseModel):
    keywords: list[VibeKeywordOut]
    creators: list[CreatorOut]


class KeywordPatchBody(BaseModel):
    keyword: str
    pinned: bool | None = None
    blocked: bool | None = None
    add: bool | None = None


class SeedNeededResponse(BaseModel):
    seed_needed: bool


class SeedBody(BaseModel):
    preset: str


class SeedResponse(BaseModel):
    preset: str
    seeded: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/taste", response_model=TasteResponse)
async def get_taste() -> TasteResponse:
    with Session(engine) as session:
        keywords = session.exec(
            select(VibeKeyword).order_by(VibeKeyword.frequency.desc())
        ).all()
        creators = session.exec(select(Creator)).all()

    return TasteResponse(
        keywords=[
            VibeKeywordOut(
                keyword=kw.keyword,
                frequency=kw.frequency,
                user_pinned=kw.user_pinned,
                user_blocked=kw.user_blocked,
            )
            for kw in keywords
        ],
        creators=[
            CreatorOut(
                id=c.id,
                platform=c.platform.value,
                handle=c.handle,
                liked_count=c.liked_count,
            )
            for c in creators
        ],
    )


@router.patch("/api/taste/keywords", response_model=VibeKeywordOut)
async def patch_keyword(body: KeywordPatchBody) -> VibeKeywordOut:
    with Session(engine) as session:
        if body.add:
            normalized = body.keyword.strip().lower()
            kw = session.get(VibeKeyword, normalized)
            if kw is None:
                kw = VibeKeyword(
                    keyword=normalized,
                    frequency=1,
                    last_seen=datetime.datetime.utcnow(),
                )
                session.add(kw)
                session.commit()
                session.refresh(kw)
        else:
            kw = session.get(VibeKeyword, body.keyword)
            if kw is None:
                raise HTTPException(status_code=404, detail="Keyword not found")

        if body.pinned is not None:
            kw.user_pinned = body.pinned
        if body.blocked is not None:
            kw.user_blocked = body.blocked
            if body.blocked:
                kw.user_pinned = False  # can't be both pinned and blocked

        session.add(kw)
        session.commit()
        session.refresh(kw)
        return VibeKeywordOut(
            keyword=kw.keyword,
            frequency=kw.frequency,
            user_pinned=kw.user_pinned,
            user_blocked=kw.user_blocked,
        )


@router.get("/api/taste/seed-needed", response_model=SeedNeededResponse)
async def get_seed_needed() -> SeedNeededResponse:
    if liked_post_count() >= _SEED_BYPASS_LIKED_COUNT:
        return SeedNeededResponse(seed_needed=False)
    return SeedNeededResponse(seed_needed=seed_needed())


@router.post("/api/taste/seed", response_model=SeedResponse)
async def post_seed(body: SeedBody) -> SeedResponse:
    if body.preset not in PRESET_KEYWORDS:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {body.preset!r}")
    seed_preset(body.preset)
    return SeedResponse(preset=body.preset, seeded=len(PRESET_KEYWORDS[body.preset]))


@router.get("/api/creators", response_model=list[CreatorOut])
async def get_creators() -> list[CreatorOut]:
    with Session(engine) as session:
        creators = session.exec(select(Creator)).all()
    return [
        CreatorOut(
            id=c.id,
            platform=c.platform.value,
            handle=c.handle,
            liked_count=c.liked_count,
        )
        for c in creators
    ]


@router.delete("/api/creators/{creator_id}")
async def delete_creator(creator_id: str) -> dict[str, str]:
    with Session(engine) as session:
        creator = session.get(Creator, creator_id)
        if creator is None:
            raise HTTPException(status_code=404, detail="Creator not found")
        session.delete(creator)
        session.commit()
    return {"status": "deleted"}
