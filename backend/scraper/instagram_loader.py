"""Instagram auth + session management via instaloader.

Supports two session sources (checked in order):
  1. sessions/instagram_loader/<username>.session  — saved via username/password login
  2. sessions/instagram.json                       — Playwright/Cookie-Editor cookie export

Xiaohongshu still uses browser.py / Playwright.
"""

from __future__ import annotations

import json
import logging
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import instaloader
import requests

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path(__file__).parents[2] / "sessions" / "instagram_loader"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

COOKIE_FILE = Path(__file__).parents[2] / "sessions" / "instagram.json"


# ---------------------------------------------------------------------------
# Cookie-file based loader (Playwright / Cookie-Editor export)
# ---------------------------------------------------------------------------

def _load_from_cookie_file() -> Optional[instaloader.Instaloader]:
    """Build an Instaloader from sessions/instagram.json if it exists."""
    if not COOKIE_FILE.exists():
        return None

    try:
        data = json.loads(COOKIE_FILE.read_text())
        cookies = data.get("cookies", [])

        # Extract username from ds_user_id cookie
        username = None
        for c in cookies:
            if c["name"] == "ds_user_id":
                username = c["value"]
                break
        if not username:
            logger.warning("instagram.json has no ds_user_id cookie — cannot load session.")
            return None

        L = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True,
        )

        # Inject cookies directly into instaloader's requests session
        session: requests.Session = L.context._session
        for c in cookies:
            session.cookies.set(
                c["name"],
                unquote(c["value"]),
                domain=c.get("domain", ".instagram.com"),
                path=c.get("path", "/"),
            )

        # Tell instaloader who we are
        L.context.username = username
        logger.info("Loaded Instagram session from cookie file (user_id=%s).", username)
        return L

    except Exception as exc:
        logger.warning("Failed to load instagram.json: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _session_path(username: str) -> Path:
    return SESSIONS_DIR / f"{username}.session"


def get_loader(username: str) -> instaloader.Instaloader:
    """Return an Instaloader with saved session.  Raises FileNotFoundError if missing."""
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


def get_any_loader() -> instaloader.Instaloader:
    """Return an authenticated Instaloader from any available source.

    Priority:
      1. instaloader .session files  (username/password login)
      2. sessions/instagram.json     (cookie export)

    Raises FileNotFoundError if no session of any kind exists.
    """
    username = get_active_username()
    if username:
        return get_loader(username)

    L = _load_from_cookie_file()
    if L:
        return L

    raise FileNotFoundError(
        "No Instagram session found. "
        "Authenticate via POST /api/auth/instagram or export cookies."
    )


def create_session(username: str, password: str) -> None:
    """Log in with username/password and persist the session to disk."""
    L = instaloader.Instaloader(quiet=True)
    L.login(username, password)
    session_file = _session_path(username)
    L.save_session_to_file(str(session_file))
    logger.info("Instagram session saved for user '%s'.", username)


def get_active_username() -> Optional[str]:
    """Return the username of the first saved .session file, or None."""
    files = list(SESSIONS_DIR.glob("*.session"))
    if not files:
        return None
    return files[0].stem


def session_exists() -> bool:
    return get_active_username() is not None or COOKIE_FILE.exists()


def delete_session() -> None:
    for f in SESSIONS_DIR.glob("*.session"):
        f.unlink(missing_ok=True)
    COOKIE_FILE.unlink(missing_ok=True)
