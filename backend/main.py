from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.curator.storage import INSPIRATION_DIR
from backend.db.models import create_db_and_tables
from backend.routers.auth import router as auth_router
from backend.routers.gallery import router as gallery_router
from backend.routers.schedule import router as schedule_router
from backend.routers.taste import router as taste_router
from backend.routers.today import router as today_router
from backend.scheduler import start_scheduler, stop_scheduler

load_dotenv()

# Directories that must exist before we mount them as static file trees
_STAGING_DIR = Path(__file__).parents[1] / "staging"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    create_db_and_tables()
    _STAGING_DIR.mkdir(parents=True, exist_ok=True)
    INSPIRATION_DIR.mkdir(parents=True, exist_ok=True)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Inspiration Curator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(schedule_router)
app.include_router(today_router)
app.include_router(gallery_router)
app.include_router(taste_router)

# Serve screenshots so the frontend can display images
app.mount("/screenshots/staging", StaticFiles(directory=str(_STAGING_DIR)), name="staging")
app.mount(
    "/screenshots/inspiration",
    StaticFiles(directory=str(INSPIRATION_DIR)),
    name="inspiration",
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
