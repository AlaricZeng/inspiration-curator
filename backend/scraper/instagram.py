"""Instagram scraper — headful Chrome, UI-driven.

Keyword mode:
  Navigates directly to https://www.instagram.com/explore/search/keyword/?q={keyword},
  waits for the post grid, downloads visible post thumbnails, scrolls for more.

Creator mode:
  Navigates to https://www.instagram.com/{username}/ and harvests the post grid.

Session bootstrap
-----------------
Loads sessions/instagram.json (Playwright storage state).  If only an instaloader
*.session file exists, cookies are auto-converted on first use and saved as
instagram.json so subsequent runs skip the conversion.

Anti-detection
--------------
- Headless Chrome (headless=True)
- --disable-blink-features=AutomationControlled launch arg
- Stealth JS patches (navigator.webdriver, window.chrome, plugins, languages)
- 10–18 s human-like waits between major UI actions

Returns up to *max_results* PostCandidates.
Raises SessionExpiredError if a login wall is detected.
Raises FileNotFoundError if no session file exists.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx
from playwright.async_api import Page, async_playwright

from backend.scraper.browser import SESSIONS_DIR
from backend.scraper.errors import PostCandidate, SessionExpiredError

logger = logging.getLogger(__name__)

_IG_BASE = "https://www.instagram.com"
_IG_SESSION_FILE = SESSIONS_DIR / "instagram.json"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_LOGIN_PATH_FRAGMENTS = ("/accounts/login/", "/challenge/")
_NO_NEW_CARDS_LIMIT = 3

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

if (!window.chrome) {
    window.chrome = {
        app: { isInstalled: false },
        webstore: { onInstallStageChanged: {}, onDownloadProgress: {} },
        runtime: {
            PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
            PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
        },
    };
}

const _origPermQuery = window.navigator.permissions.query.bind(navigator.permissions);
window.navigator.permissions.__proto__.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : _origPermQuery(p);

Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        arr.__proto__ = PluginArray.prototype;
        return arr;
    },
});

Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

delete window.__playwright;
delete window.__pw_manual;
delete window.__PW_inspect;
"""


# ---------------------------------------------------------------------------
# Human-like timing
# ---------------------------------------------------------------------------


async def _human_wait(page: Page) -> None:
    """Wait 10–18 s to mimic human browsing pace."""
    ms = int(random.uniform(10_000, 18_000))
    logger.debug("IG: waiting %.0fs", ms / 1000)
    await page.wait_for_timeout(ms)


# ---------------------------------------------------------------------------
# Session bootstrap
# ---------------------------------------------------------------------------


def _bootstrap_session_file() -> None:
    """Ensure instagram.json (Playwright storage state) exists.

    If it already exists, returns immediately. Otherwise, attempts to convert
    cookies from a saved instaloader *.session file. This lets username/password
    logins (instaloader) work transparently with the new browser-based scraper.

    Raises FileNotFoundError if no session of any kind is available.
    """
    if _IG_SESSION_FILE.exists():
        return

    try:
        from backend.scraper.instagram_loader import get_any_loader
        L = get_any_loader()
        cookies = []
        for c in L.context._session.cookies:
            cookies.append({
                "name": c.name,
                "value": c.value,
                "domain": c.domain if c.domain else ".instagram.com",
                "path": c.path or "/",
                "expires": int(c.expires) if c.expires else -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "None",
            })
        _IG_SESSION_FILE.write_text(json.dumps({"cookies": cookies, "origins": []}))
        logger.info("IG: created instagram.json from instaloader session (%d cookies).", len(cookies))
    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.warning("IG: could not create instagram.json from instaloader session: %s", exc)
        raise FileNotFoundError(
            "No Instagram session. Authenticate via POST /api/auth/instagram or import cookies."
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scrape_instagram(
    keyword: str | None,
    creator_handles: list[str],
    max_results: int = 10,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Scrape Instagram using a headful browser and return up to *max_results* fresh candidates.

    Raises:
        SessionExpiredError: login wall detected during scraping.
        FileNotFoundError:   no session file — user must authenticate first.
    """
    _bootstrap_session_file()

    candidates: list[PostCandidate] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            storage_state=str(_IG_SESSION_FILE),
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
        )
        await context.add_init_script(script=_STEALTH_JS)

        page = await context.new_page()
        page.set_default_timeout(30_000)

        try:
            if keyword:
                found = await _scrape_keyword(page, keyword, max_results, skip_urls)
                candidates.extend(found)

            for handle in creator_handles:
                if len(candidates) >= max_results:
                    break
                found = await _scrape_creator(
                    page, handle, max_results - len(candidates), skip_urls
                )
                candidates.extend(found)

        except SessionExpiredError:
            raise
        except Exception as exc:
            try:
                shot_path = SESSIONS_DIR / "ig_debug.png"
                await page.screenshot(path=str(shot_path), full_page=False)
                logger.error(
                    "IG: session aborted — %s | URL=%s title=%r | screenshot → %s",
                    exc, page.url, await page.title(), shot_path,
                )
            except Exception:
                logger.error("IG: session aborted — %s", exc)
            raise
        finally:
            await browser.close()

    candidates.sort(key=lambda c: c.engagement, reverse=True)
    return candidates[:max_results]


# ---------------------------------------------------------------------------
# Keyword + creator entry points
# ---------------------------------------------------------------------------


async def _scrape_keyword(
    page: Page,
    keyword: str,
    limit: int,
    skip_urls: set[str] | None,
) -> list[PostCandidate]:
    url = f"{_IG_BASE}/explore/search/keyword/?q={quote(keyword)}"
    logger.info("IG: keyword search %r → %s", keyword, url)
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await _dismiss_dialogs(page)
    await _human_wait(page)
    await _assert_not_login_wall(page)
    return await _harvest_grid(page, keyword, limit, skip_urls, from_creator=False)


async def _scrape_creator(
    page: Page,
    handle: str,
    limit: int,
    skip_urls: set[str] | None,
) -> list[PostCandidate]:
    handle = handle.lstrip("@")
    url = f"{_IG_BASE}/{handle}/"
    logger.info("IG: creator profile %r → %s", handle, url)
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await _dismiss_dialogs(page)
    await _human_wait(page)
    await _assert_not_login_wall(page)
    return await _harvest_grid(page, handle, limit, skip_urls, from_creator=True)


# ---------------------------------------------------------------------------
# Grid harvesting
# ---------------------------------------------------------------------------


async def _harvest_grid(
    page: Page,
    label: str,
    limit: int,
    skip_urls: set[str] | None,
    *,
    from_creator: bool,
) -> list[PostCandidate]:
    """Collect up to *limit* post candidates from the current page's post grid."""
    processed_urls: set[str] = set(skip_urls or [])
    candidates: list[PostCandidate] = []
    no_new_rounds = 0

    try:
        await page.wait_for_selector('a[href^="/p/"]', timeout=15_000)
    except Exception:
        logger.warning("IG %r: no post links visible on %s", label, page.url)
        return candidates

    cookies = await page.context.cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    while len(candidates) < limit and no_new_rounds < _NO_NEW_CARDS_LIMIT:
        all_cards = await _collect_post_cards(page)
        new_cards = [c for c in all_cards if c.url not in processed_urls]

        if not new_cards:
            no_new_rounds += 1
            logger.debug("IG %r: no new cards (streak %d), scrolling", label, no_new_rounds)
            await page.evaluate("window.scrollBy(0, 1200)")
            await _human_wait(page)
            continue

        no_new_rounds = 0
        for card in new_cards:
            if len(candidates) >= limit:
                break
            processed_urls.add(card.url)

            cover_data = await _download_cover(card.cover_img_url, cookie_header)
            if not cover_data:
                logger.debug("IG: no cover for %s, skipping", card.url)
                continue

            candidates.append(PostCandidate(
                source_url=card.url,
                creator=card.creator,
                engagement=0,
                screenshot_data=cover_data,
                from_creator=from_creator,
                tags=[],
            ))
            logger.info("IG %r: harvested %d/%d", label, len(candidates), limit)

        if len(candidates) < limit:
            await page.evaluate("window.scrollBy(0, 1200)")
            await _human_wait(page)

    return candidates


# ---------------------------------------------------------------------------
# DOM helpers
# ---------------------------------------------------------------------------


@dataclass
class _PostCard:
    url: str
    cover_img_url: str
    creator: str = ""


async def _collect_post_cards(page: Page) -> list[_PostCard]:
    """Collect all currently-visible post cards from the grid."""
    cards: list[_PostCard] = []
    seen_urls: set[str] = set()

    links = await page.query_selector_all('a[href^="/p/"]')
    for link in links:
        try:
            href = await link.get_attribute("href") or ""
            url = (f"{_IG_BASE}{href}" if href.startswith("/") else href).split("?")[0].rstrip("/")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            img = await link.query_selector("img")
            if not img:
                continue
            src = (await img.get_attribute("src") or "").strip()
            if not src or src.startswith("data:"):
                continue

            # Instagram alt text is often "Photo by @username on [date] …"
            alt = await img.get_attribute("alt") or ""
            m = re.search(r"@([\w.]+)", alt)
            creator = m.group(1) if m else ""

            cards.append(_PostCard(url=url, cover_img_url=src, creator=creator))
        except Exception:
            continue

    logger.debug("IG DOM: %d post links → %d cards", len(links), len(cards))
    return cards


# ---------------------------------------------------------------------------
# Dialog dismissal + login wall detection
# ---------------------------------------------------------------------------


async def _dismiss_dialogs(page: Page) -> None:
    """Dismiss common Instagram interstitial prompts (notifications, etc.)."""
    for selector in [
        'button:has-text("Not Now")',
        'button:has-text("Dismiss")',
        '[aria-label="Close"]:visible',
    ]:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2_000):
                await el.click()
                await page.wait_for_timeout(500)
        except Exception:
            pass


def _is_login_url(url: str) -> bool:
    path = urlparse(url).path
    return any(path.startswith(frag) for frag in _LOGIN_PATH_FRAGMENTS)


async def _assert_not_login_wall(page: Page) -> None:
    if _is_login_url(page.url):
        raise SessionExpiredError("instagram")
    for sel in ['form:has(input[name="username"])', '[data-testid="login-modal"]']:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                raise SessionExpiredError("instagram")
        except SessionExpiredError:
            raise
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------


async def _download_cover(url: str, cookie_header: str) -> bytes:
    """Download a post cover image. Returns empty bytes on failure."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "Referer": "https://www.instagram.com/",
                "Cookie": cookie_header,
                "User-Agent": _USER_AGENT,
            })
            if resp.status_code == 200 and resp.content:
                return resp.content
            logger.debug("IG cover fetch %s: HTTP %s", url, resp.status_code)
    except Exception as exc:
        logger.debug("IG cover download error: %s", exc)
    return b""
