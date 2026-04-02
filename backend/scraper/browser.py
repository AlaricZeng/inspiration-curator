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

        try:
            await page.wait_for_function(
                f"""() => {{
                    const url = window.location.href;
                    return url.includes('{config["logged_in_url_pattern"]}')
                        && !url.includes('{exclude}');
                }}""",
                timeout=300_000,  # 5 minutes
            )
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
