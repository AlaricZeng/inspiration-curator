"""Playwright session manager.

Manages browser contexts for scraping Instagram and Xiaohongshu.
Instagram uses username/password via instaloader; Xiaohongshu authenticates
via cookie import (POST /api/auth/xiaohongshu/cookies).
"""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import BrowserContext, Playwright, async_playwright

SESSIONS_DIR = Path(__file__).parents[2] / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

PLATFORM_CONFIG: dict[str, dict[str, str]] = {
    "instagram": {
        "session_file": str(SESSIONS_DIR / "instagram.json"),
        "login_url": "https://www.instagram.com/accounts/login/",
        # Instagram lands on https://www.instagram.com/ after login,
        # but the path must be exactly "/" or start with something other than
        # /accounts/ or /challenge/ to be considered truly logged in.
        "logged_in_url_pattern": "https://www.instagram.com/",
        "logged_in_exclude": "/accounts/",
    },
    "xiaohongshu": {
        "session_file": str(SESSIONS_DIR / "xiaohongshu.json"),
        "login_url": "https://www.xiaohongshu.com",
        "logged_in_url_pattern": "https://www.xiaohongshu.com",
        "logged_in_exclude": "/login",
    },
}


def session_exists(platform: str) -> bool:
    config = PLATFORM_CONFIG[platform]
    return Path(config["session_file"]).exists()


def import_cookies(platform: str, cookie_json: list | dict) -> None:
    """Convert a Cookie-Editor export to Playwright storage state and save it.

    Accepts either:
      - A list of cookie objects  (Cookie-Editor "Export as JSON")
      - {"cookies": [...]}        (same format used by sessions/instagram.json)

    Saves to the platform's session_file in Playwright storage_state format.
    """
    import json as _json

    # Normalise input to a flat list
    if isinstance(cookie_json, dict):
        raw_cookies = cookie_json.get("cookies", [])
    else:
        raw_cookies = cookie_json

    _SAME_SITE_MAP = {
        "no_restriction": "None",
        "unspecified": "None",
        "lax": "Lax",
        "strict": "Strict",
    }

    pw_cookies = []
    for c in raw_cookies:
        same_site_raw = str(c.get("sameSite", "no_restriction")).lower()
        pw_cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", f".{platform}.com"),
            "path": c.get("path", "/"),
            "expires": c.get("expirationDate", c.get("expires", -1)),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": _SAME_SITE_MAP.get(same_site_raw, "None"),
        })

    storage_state = {"cookies": pw_cookies, "origins": []}
    config = PLATFORM_CONFIG[platform]
    session_file = Path(config["session_file"])
    session_file.write_text(_json.dumps(storage_state))

    import logging
    logging.getLogger(__name__).info(
        "Imported %d cookies for %s → %s", len(pw_cookies), platform, session_file
    )


async def get_context(platform: str, pw: Playwright) -> BrowserContext:
    """Return a headless browser context loaded with the saved session.

    For Xiaohongshu, uses a persistent browser profile for longer-lived sessions.
    If a profile dir already exists it is used directly. If only a cookie JSON
    exists, a fresh persistent profile is created and seeded with those cookies.

    Raises FileNotFoundError if no session exists for the platform.
    """
    config = PLATFORM_CONFIG[platform]
    session_file = Path(config["session_file"])

    if not session_file.exists():
        raise FileNotFoundError(
            f"No session found for {platform}. "
            "Authenticate first via POST /api/auth/{platform}."
        )

    browser = await pw.chromium.launch(headless=True)
    return await browser.new_context(storage_state=str(session_file))
