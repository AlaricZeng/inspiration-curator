import datetime as dt
import uuid
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel, create_engine


class Platform(str, Enum):
    instagram = "instagram"
    xiaohongshu = "xiaohongshu"


class PostStatus(str, Enum):
    pending = "pending"
    liked = "liked"
    skipped = "skipped"


class RunMode(str, Enum):
    keyword = "keyword"
    vibe = "vibe"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class PlatformStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    skipped = "skipped"


class Post(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    platform: Platform
    source_url: str
    creator: str
    screenshot: Optional[str] = None
    scraped_at: dt.datetime = Field(default_factory=dt.datetime.now)
    status: PostStatus = Field(default=PostStatus.pending)
    engagement: int = Field(default=0)
    keyword: Optional[str] = None
    vibe_keywords: Optional[str] = None  # comma-separated keywords extracted by LLM after liking


class Creator(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    platform: Platform
    handle: str
    liked_count: int = Field(default=0)
    added_at: dt.datetime = Field(default_factory=dt.datetime.utcnow)


class VibeKeyword(SQLModel, table=True):
    keyword: str = Field(primary_key=True)
    frequency: int = Field(default=1)
    last_seen: dt.datetime = Field(default_factory=dt.datetime.utcnow)
    user_pinned: bool = Field(default=False)
    user_blocked: bool = Field(default=False)


class PlatformRun(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str = Field(foreign_key="dailyrun.id", index=True)
    platform: Platform
    status: PlatformStatus = Field(default=PlatformStatus.pending)
    post_count: int = Field(default=0)


class DailyRun(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_date: dt.date = Field(default_factory=dt.date.today)
    keyword: Optional[str] = None
    mode: RunMode = Field(default=RunMode.vibe)
    status: RunStatus = Field(default=RunStatus.pending)


DATABASE_URL = "sqlite:///./inspiration.db"
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
