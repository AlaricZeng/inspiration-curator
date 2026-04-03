"""Instagram auth + session management via instaloader.

Replaces the Playwright-based browser.py flow for Instagram only.
Xiaohongshu still uses browser.py / Playwright.

Session files are stored in  sessions/instagram_loader/
so they don't conflict with the old Playwright session.json.
"""

from __future__ import annotations

import logging
from pathlib import Path

import instaloader

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path(__file__).parents[2] / "sessions" / "instagram_loader"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(username: str) -> Path:
    return SESSIONS_DIR / f"{username}.session"


def get_loader(username: str) -> instaloader.Instaloader:
    """Return an Instaloader instance with the saved session loaded.

    Raises FileNotFoundError if no session exists for *username*.
    """
    session_file = _session_path(username)
    if not session_file.exists():
        raise FileNotFoundError(
            f"No instaloader session for '{username}'. "
            "Authenticate via POST /api/auth/instagram."
        )
    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
    )
    L.load_session_from_file(username, str(session_file))
    return L


def create_session(username: str, password: str) -> None:
    """Log in with username/password and persist the session to disk."""
    L = instaloader.Instaloader(quiet=True)
    L.login(username, password)
    session_file = _session_path(username)
    L.save_session_to_file(str(session_file))
    logger.info("Instagram session saved for user '%s'.", username)


def get_active_username() -> str | None:
    """Return the username of the first saved session, or None."""
    files = list(SESSIONS_DIR.glob("*.session"))
    if not files:
        return None
    return files[0].stem


def session_exists() -> bool:
    return get_active_username() is not None


def delete_session() -> None:
    for f in SESSIONS_DIR.glob("*.session"):
        f.unlink(missing_ok=True)
