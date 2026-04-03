from __future__ import annotations

import logging
import logging.config
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

# ---------------------------------------------------------------------------
# Logging — write INFO+ to console and to backend/app.log
# ---------------------------------------------------------------------------
_LOG_FILE = Path(__file__).parent / "app.log"

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "level": "INFO",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": str(_LOG_FILE),
            "formatter": "default",
            "level": "DEBUG",
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "DEBUG",
    },
    # Keep noisy third-party loggers quieter
    "loggers": {
        "uvicorn": {"level": "INFO", "propagate": True},
        "uvicorn.access": {"level": "WARNING", "propagate": True},
        "httpx": {"level": "WARNING", "propagate": True},
        "playwright": {"level": "WARNING", "propagate": True},
    },
})

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
