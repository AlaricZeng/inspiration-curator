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
        "user_data_dir": str(SESSIONS_DIR / "xiaohongshu_profile"),
        "login_url": "https://www.xiaohongshu.com",
        "logged_in_url_pattern": "https://www.xiaohongshu.com",
        "logged_in_exclude": "/login",
        "auth_cookie": "web_session",
    },
}


def session_exists(platform: str) -> bool:
    config = PLATFORM_CONFIG[platform]
    if "user_data_dir" in config and Path(config["user_data_dir"]).exists():
        return True
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

    # If a persistent profile dir exists, remove it so the next get_context()
    # call rebuilds it with the freshly imported cookies.
    if "user_data_dir" in config:
        import shutil
        user_data_dir = Path(config["user_data_dir"])
        if user_data_dir.exists():
            shutil.rmtree(user_data_dir)

    import logging
    logging.getLogger(__name__).info(
        "Imported %d cookies for %s → %s", len(pw_cookies), platform, session_file
    )


async def create_session(platform: str) -> None:
    """Open a visible browser, wait for the user to log in, then save the session.

    The browser stays open until the platform detects a successful login
    (URL leaves the login page) or the 5-minute timeout is reached.

    For Xiaohongshu, uses a persistent browser profile so the session lasts longer.
    """
    config = PLATFORM_CONFIG[platform]
    session_file = config["session_file"]
    use_persistent = "user_data_dir" in config

    async with async_playwright() as pw:
        if use_persistent:
            import shutil
            user_data_dir = Path(config["user_data_dir"])
            if user_data_dir.exists():
                shutil.rmtree(user_data_dir)
            context = await pw.chromium.launch_persistent_context(
                str(user_data_dir), headless=False
            )
        else:
            browser = await pw.chromium.launch(headless=False)
            context = await browser.new_context()

        page = await context.new_page()
        await page.goto(config["login_url"])

        exclude = config["logged_in_exclude"]
        logged_in_pattern = config["logged_in_url_pattern"]

        auth_cookie = config.get("auth_cookie")  # optional cookie name that must be present

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
                if is_clean and auth_cookie:
                    # For XHS: web_session for a logged-in user is much longer (>100 chars).
                    # Guest sessions also have web_session but it's short/empty.
                    cookies = await context.cookies()
                    session_val = next(
                        (c["value"] for c in cookies if c["name"] == auth_cookie), ""
                    )
                    is_clean = len(session_val) > 50
                if is_clean:
                    stable_count += 1
                else:
                    stable_count = 0
                if stable_count >= 3:
                    break
            else:
                await context.close()
                raise TimeoutError(
                    f"Login timed out after 5 minutes for {platform}. Please try again."
                )
        except TimeoutError:
            raise
        except Exception:
            await context.close()
            raise TimeoutError(
                f"Login timed out after 5 minutes for {platform}. Please try again."
            )

        if use_persistent:
            # Profile is auto-saved; also write a JSON marker so session_exists() works
            # even before the first persistent-context scrape run.
            await context.storage_state(path=session_file)
            await context.close()
        else:
            await context.storage_state(path=session_file)
            await context.browser.close()


async def get_context(platform: str, pw: Playwright) -> BrowserContext:
    """Return a headless browser context loaded with the saved session.

    For Xiaohongshu, uses a persistent browser profile for longer-lived sessions.
    If a profile dir already exists it is used directly. If only a cookie JSON
    exists, a fresh persistent profile is created and seeded with those cookies.

    Raises FileNotFoundError if no session exists for the platform.
    """
    import json as _json

    config = PLATFORM_CONFIG[platform]
    session_file = Path(config["session_file"])

    if "user_data_dir" not in config:
        # Instagram: plain headless context with storage_state
        if not session_file.exists():
            raise FileNotFoundError(
                f"No session found for {platform}. "
                "Authenticate first via POST /api/auth/{platform}."
            )
        browser = await pw.chromium.launch(headless=True)
        return await browser.new_context(storage_state=str(session_file))

    # Xiaohongshu: persistent profile
    user_data_dir = Path(config["user_data_dir"])

    if not user_data_dir.exists() and not session_file.exists():
        raise FileNotFoundError(
            f"No session found for {platform}. "
            "Authenticate first via POST /api/auth/{platform}."
        )

    # Check before launch whether this is a first-time seed situation
    needs_cookie_seed = not user_data_dir.exists() and session_file.exists()

    context = await pw.chromium.launch_persistent_context(
        str(user_data_dir), headless=True
    )

    # First-time seed: inject cookies from JSON into the fresh profile
    if needs_cookie_seed:
        state = _json.loads(session_file.read_text())
        await context.add_cookies(state.get("cookies", []))

    return context


async def get_browser(platform: str) -> AsyncGenerator[BrowserContext, None]:
    """Async context manager yielding a headless context with saved session."""
    async with async_playwright() as pw:
        context = await get_context(platform, pw)
        try:
            yield context
        finally:
            await context.close()
