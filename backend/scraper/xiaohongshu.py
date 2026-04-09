"""Xiaohongshu (小红书 / RedNote) scraper — headful Chrome, UI-driven.

Search flow per keyword
-----------------------
1. Open https://www.xiaohongshu.com once for the whole session.
2. Click the search input, type the keyword, press Enter.
3. Wait 25–40 s (human-like delay).
4. Find visible note cards, pick the first unprocessed one.
5. Click the card cover to open the detail overlay. Wait 25–40 s.
6. Extract hashtags from #detail-desc. Click the back arrow (left side). Wait 25–40 s.
7. Scroll down. Wait 25–40 s. Go to step 4.
8. Repeat steps 2–7 for each keyword in the same browser session.

Anti-detection
--------------
- Headful Chrome (headless=False)
- --disable-blink-features=AutomationControlled launch arg
- Stealth JS patches (navigator.webdriver, window.chrome, plugins, languages)
- webId cookie injection (suppresses XHS sliding CAPTCHA)
- 25–40 s waits between every major UI action

Returns up to *max_results* PostCandidates ranked by engagement (highest first).
Raises SessionExpiredError if a login wall is detected.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from playwright.async_api import Page, async_playwright

from backend.scraper.browser import SESSIONS_DIR
from backend.scraper.errors import PostCandidate, SessionExpiredError

logger = logging.getLogger(__name__)

_BASE = "https://www.xiaohongshu.com"
_LOGIN_PATH_FRAGMENTS = ("/login", "/website-login")
_LOGIN_MODAL_SELECTORS = ('div[data-testid="login-modal"]',)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Tried in order to locate the search input field
_SEARCH_INPUT_SELECTORS = [
    "input#search-input",
    "input.search-input",
    'input[placeholder*="搜索"]',
    'input[type="search"]',
]

# Tried in order to find the back/close button on a note detail page
_BACK_BUTTON_SELECTORS = [
    ".back",
    ".back-btn",
    '[class*="back-btn"]',
    '[class*="backBtn"]',
    'button[aria-label="返回"]',
    ".close",
    '[class*="closeBtn"]',
    '[class*="close-btn"]',
]

# Stop scrolling for a keyword after this many consecutive scroll rounds
# that yield no new unseen cards.
_NO_NEW_CARDS_LIMIT = 3

# Stealth JS — patches the properties XHS fingerprinting checks to detect headless/automated Chromium.
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

Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });

delete window.__playwright;
delete window.__pw_manual;
delete window.__PW_inspect;
"""


# ---------------------------------------------------------------------------
# Human-like timing
# ---------------------------------------------------------------------------


async def _human_wait(page: Page) -> None:
    """Wait 25–40 s to mimic human browsing pace."""
    ms = int(random.uniform(25_000, 40_000))
    logger.debug("XHS: waiting %.0fs", ms / 1000)
    await page.wait_for_timeout(ms)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scrape_xiaohongshu(
    keywords: list[str],
    max_results: int = 10,
    skip_urls: set[str] | None = None,
    keyword_limits: list[int] | None = None,
) -> list[PostCandidate]:
    """Scrape Xiaohongshu by hashtag keywords using a headful browser with UI interactions.

    Args:
        keywords: Hashtags/keywords to search (searched via the XHS search box).
        max_results: Global result cap when keyword_limits is not provided.
        skip_urls: Source URLs already in the DB — skipped during collection.
        keyword_limits: Per-keyword fetch budget. keyword_limits[i] overrides
                        max_results for keywords[i]; 0 skips that keyword.

    Raises:
        SessionExpiredError: Login wall detected during scraping.
        FileNotFoundError:   No session file — user must authenticate first.
    """
    session_file = SESSIONS_DIR / "xiaohongshu.json"
    if not session_file.exists():
        raise FileNotFoundError(
            "No XHS session. Authenticate first via POST /api/auth/xiaohongshu/cookies."
        )

    candidates: list[PostCandidate] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            storage_state=str(session_file),
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
        )
        await context.add_init_script(script=_STEALTH_JS)
        # webId suppresses XHS's sliding CAPTCHA — any non-empty value works.
        await context.add_cookies([{
            "name": "webId",
            "value": "xxx123",
            "domain": ".xiaohongshu.com",
            "path": "/",
        }])

        page = await context.new_page()
        page.set_default_timeout(30_000)

        try:
            # Open home page — the one goto allowed per session
            logger.info("XHS: opening home page")
            await page.goto(_BASE, wait_until="domcontentloaded", timeout=30_000)
            logger.info("XHS: home page loaded — URL=%s title=%r", page.url, await page.title())
            await _human_wait(page)

            for i, keyword in enumerate(keywords):
                if keyword_limits is not None:
                    limit = keyword_limits[i] if i < len(keyword_limits) else 0
                else:
                    if len(candidates) >= max_results:
                        break
                    limit = max_results - len(candidates)
                if limit <= 0:
                    continue

                found = await _search_and_harvest(page, keyword, limit, skip_urls=skip_urls)
                candidates.extend(found)

        except Exception as exc:
            # Save a screenshot so we can see what XHS showed before closing
            try:
                shot_path = SESSIONS_DIR / "xhs_debug.png"
                await page.screenshot(path=str(shot_path), full_page=False)
                logger.error(
                    "XHS: session aborted — %s | URL=%s title=%r | screenshot saved to %s",
                    exc, page.url, await page.title(), shot_path,
                )
            except Exception:
                logger.error("XHS: session aborted — %s", exc)
            raise
        finally:
            await browser.close()

    candidates.sort(key=lambda c: c.engagement, reverse=True)
    return candidates[:max_results]


# ---------------------------------------------------------------------------
# Search + harvest
# ---------------------------------------------------------------------------


async def _search_and_harvest(
    page: Page,
    keyword: str,
    limit: int,
    *,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Search for *keyword* via the search input box and harvest up to *limit* posts."""
    if not await _perform_search(page, keyword):
        return []
    await _human_wait(page)

    await _assert_not_login_wall(page)

    cookies = await page.context.cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    processed_urls: set[str] = set(skip_urls or [])
    candidates: list[PostCandidate] = []
    no_new_rounds = 0

    while len(candidates) < limit and no_new_rounds < _NO_NEW_CARDS_LIMIT:
        all_cards = await _collect_all_cards(page)
        new_cards = [c for c in all_cards if c.url not in processed_urls]

        if not new_cards:
            no_new_rounds += 1
            logger.debug("XHS %r: no new cards (streak %d), scrolling", keyword, no_new_rounds)
            await page.evaluate("window.scrollBy(0, 1200)")
            await _human_wait(page)
            continue

        no_new_rounds = 0
        card = new_cards[0]
        processed_urls.add(card.url)

        # Download cover image before navigating away
        cover_data = await _download_cover(card.cover_img_url, cookie_header)
        if not cover_data:
            logger.debug("XHS: no cover for %s, skipping", card.url)
            continue

        # Click the card cover to open detail overlay
        tags: list[str] = []
        try:
            note_id = _note_id(card.url)
            if note_id:
                # Target the cover <a> inside the note-item section specifically
                await page.click(
                    f'section.note-item a.cover[href*="{note_id}"]',
                    timeout=8_000,
                )
                await _human_wait(page)
                await _assert_not_login_wall(page)
                tags = await _extract_detail_tags(page)
            await _click_back(page)
            await _human_wait(page)
        except SessionExpiredError:
            raise
        except Exception as exc:
            logger.debug("XHS: detail interaction failed for %s: %s", card.url, exc)
            try:
                await _click_back(page)
                await page.wait_for_timeout(3_000)
            except Exception:
                pass

        candidates.append(PostCandidate(
            source_url=card.url,
            creator=card.creator,
            engagement=card.engagement,
            screenshot_data=cover_data,
            tags=tags,
        ))
        logger.info("XHS %r: harvested %d/%d", keyword, len(candidates), limit)

        if len(candidates) >= limit:
            break

        # Scroll to reveal more cards, then wait
        await page.evaluate("window.scrollBy(0, 800)")
        await _human_wait(page)

    return candidates


async def _perform_search(page: Page, keyword: str) -> bool:
    """Find the search input on the current page, type *keyword*, press Enter.

    Works on both the home page and the search-results page (both have a
    search input in the header). Returns True if search results loaded.
    """
    search_locator = None
    for sel in _SEARCH_INPUT_SELECTORS:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(timeout=6_000, state="visible")
            search_locator = loc
            break
        except Exception:
            continue

    if search_locator is None:
        try:
            shot_path = SESSIONS_DIR / "xhs_debug.png"
            await page.screenshot(path=str(shot_path), full_page=False)
            logger.warning(
                "XHS: search input not found. URL=%s title=%r | screenshot → %s",
                page.url, await page.title(), shot_path,
            )
        except Exception:
            logger.warning(
                "XHS: search input not found. URL=%s title=%r",
                page.url, await page.title(),
            )
        return False

    await search_locator.click()
    await page.wait_for_timeout(400)
    await search_locator.fill(keyword)  # fill() replaces any existing text
    await page.wait_for_timeout(300)
    await page.keyboard.press("Enter")

    try:
        await page.wait_for_selector("section.note-item", timeout=20_000)
        logger.info("XHS: search results loaded for %r", keyword)
        return True
    except Exception:
        logger.warning(
            "XHS: no results after searching %r. URL: %s title: %s",
            keyword, page.url, await page.title(),
        )
        return False


async def _extract_detail_tags(page: Page) -> list[str]:
    """Extract hashtags from the open note detail overlay (#detail-desc)."""
    tags: list[str] = []
    try:
        await page.wait_for_selector("#detail-desc", timeout=8_000)
        for el in await page.query_selector_all("#detail-desc a.tag"):
            text = (await el.inner_text()).strip().lstrip("#").strip()
            if text and text not in tags:
                tags.append(text)
    except Exception as exc:
        logger.debug("XHS: tag extraction failed: %s", exc)
    logger.debug("XHS: detail tags: %s", tags)
    return tags


async def _click_back(page: Page) -> None:
    """Click the back/close button on the note detail (left side arrow).

    Tries known selectors first; falls back to browser history go_back().
    """
    for sel in _BACK_BUTTON_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                # Wait for search results to be visible again
                try:
                    await page.wait_for_selector("section.note-item", timeout=8_000)
                except Exception:
                    pass
                return
        except Exception:
            continue

    logger.debug("XHS: no back button found — using page.go_back()")
    await page.go_back(wait_until="domcontentloaded", timeout=15_000)
    await page.wait_for_timeout(1_000)


# ---------------------------------------------------------------------------
# Login wall detection
# ---------------------------------------------------------------------------


def _is_login_url(url: str) -> bool:
    path = urlparse(url).path
    return any(path.startswith(frag) for frag in _LOGIN_PATH_FRAGMENTS)


async def _assert_not_login_wall(page: Page) -> None:
    if _is_login_url(page.url):
        raise SessionExpiredError("xiaohongshu")
    for sel in _LOGIN_MODAL_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                raise SessionExpiredError("xiaohongshu")
        except SessionExpiredError:
            raise
        except Exception:
            continue


# ---------------------------------------------------------------------------
# DOM helpers
# ---------------------------------------------------------------------------


@dataclass
class _NoteCard:
    url: str
    cover_img_url: str
    creator: str = ""
    engagement: int = 0


async def _collect_all_cards(page: Page) -> list[_NoteCard]:
    """Collect all currently-visible note cards from the search results page."""
    cards: list[_NoteCard] = []
    seen_urls: set[str] = set()

    sections = await page.query_selector_all("section.note-item")
    if not sections:
        logger.warning(
            "XHS: 0 note-item sections visible. URL: %s title: %s",
            page.url, await page.title(),
        )
        return cards

    for section in sections:
        try:
            # Note URL (strip query params from the /explore/ link)
            note_url = ""
            for link_el in await section.query_selector_all('a[href*="/explore/"]'):
                href = await link_el.get_attribute("href") or ""
                full = (href if href.startswith("http") else f"{_BASE}{href}").split("?")[0].rstrip("/")
                if full not in seen_urls:
                    note_url = full
                    break

            if not note_url or note_url in seen_urls:
                continue
            seen_urls.add(note_url)

            # Creator user ID
            creator = ""
            author_el = await section.query_selector('a.author[href*="/user/profile/"]')
            if author_el:
                href = (await author_el.get_attribute("href") or "").strip()
                m = re.search(r"/user/profile/([^/?#]+)", href)
                if m:
                    creator = m.group(1)

            # Cover image (exclude avatars)
            cover_src = ""
            for img_el in await section.query_selector_all("img[data-xhs-img]"):
                src = (await img_el.get_attribute("src") or "").strip()
                if src and not src.startswith("data:") and "avatar" not in src:
                    cover_src = src
                    break

            if not cover_src:
                continue

            cards.append(_NoteCard(url=note_url, cover_img_url=cover_src, creator=creator))
        except Exception:
            continue

    logger.debug("XHS DOM: %d sections → %d cards", len(sections), len(cards))
    return cards


def _note_id(url: str) -> str:
    """Extract the hex note ID from a URL like .../explore/NOTEID."""
    m = re.search(r"/explore/([a-f0-9]+)", url)
    return m.group(1) if m else ""


async def _download_cover(url: str, cookie_header: str) -> bytes:
    """Download the note cover image. Returns empty bytes on failure."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "Referer": "https://www.xiaohongshu.com/",
                "Cookie": cookie_header,
                "User-Agent": _USER_AGENT,
            })
            if resp.status_code == 200 and resp.content:
                return resp.content
            logger.debug("XHS cover fetch %s: HTTP %s", url, resp.status_code)
    except Exception as exc:
        logger.debug("XHS cover download error: %s", exc)
    return b""
