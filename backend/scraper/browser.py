"""Playwright session manager.

Handles saving and loading authenticated browser sessions for Instagram and
Xiaohongshu. Headful mode is used for initial authentication; headless mode
is used for all scraping runs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncGenerator

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
    session_file = Path(PLATFORM_CONFIG[platform]["session_file"])
    session_file.write_text(_json.dumps(storage_state))
    import logging
    logging.getLogger(__name__).info(
        "Imported %d cookies for %s → %s", len(pw_cookies), platform, session_file
    )


async def create_session(platform: str) -> None:
    """Open a visible browser, wait for the user to log in, then save the session.

    The browser stays open until the platform detects a successful login
    (URL leaves the login page) or the 5-minute timeout is reached.
    """
    config = PLATFORM_CONFIG[platform]
    session_file = config["session_file"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(config["login_url"])

        # Wait until the URL indicates the user is logged in.
        # For Instagram: URL is instagram.com but not /accounts/...
        # For Xiaohongshu: URL is xiaohongshu.com but not /login...
        exclude = config["logged_in_exclude"]
        logged_in_pattern = config["logged_in_url_pattern"]

        # For Instagram, wait for the actual home feed to be visible rather than
        # just checking the URL — Instagram has several intermediate pages
        # (/challenge/, "Save login info?", etc.) that briefly show instagram.com/
        # before redirecting back to /accounts/. We wait for a stable logged-in
        # state by polling every 2 seconds and requiring the URL to stay clean
        # for at least 5 consecutive seconds.
        try:
            stable_count = 0
            deadline = 300  # 5 minutes total
            elapsed = 0
            while elapsed < deadline:
                await asyncio.sleep(2)
                elapsed += 2
                current_url = page.url
                is_clean = (
                    logged_in_pattern in current_url
                    and exclude not in current_url
                    and "/challenge/" not in current_url
                    and "/two_factor" not in current_url
                )
                if is_clean:
                    stable_count += 1
                else:
                    stable_count = 0
                # Require URL to be clean for 3 consecutive checks (6 seconds)
                if stable_count >= 3:
                    break
            else:
                await browser.close()
                raise TimeoutError(
                    f"Login timed out after 5 minutes for {platform}. Please try again."
                )
        except TimeoutError:
            raise
        except Exception:
            await browser.close()
            raise TimeoutError(
                f"Login timed out after 5 minutes for {platform}. Please try again."
            )

        await context.storage_state(path=session_file)
        await browser.close()


async def get_context(platform: str, pw: Playwright) -> BrowserContext:
    """Return a headless browser context loaded with the saved session.

    Raises FileNotFoundError if no session exists for the platform.
    """
    config = PLATFORM_CONFIG[platform]
    session_file = config["session_file"]

    if not Path(session_file).exists():
        raise FileNotFoundError(
            f"No session found for {platform}. "
            "Authenticate first via POST /api/auth/{platform}."
        )

    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(storage_state=session_file)
    return context


async def get_browser(platform: str) -> AsyncGenerator[BrowserContext, None]:
    """Async context manager yielding a headless context with saved session.

    Usage:
        async with async_playwright() as pw:
            ctx = await get_context(platform, pw)
            page = await ctx.new_page()
            ...
            await ctx.browser.close()
    """
    async with async_playwright() as pw:
        context = await get_context(platform, pw)
        try:
            yield context
        finally:
            await context.browser.close()
