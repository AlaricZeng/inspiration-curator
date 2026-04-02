"""Instagram scraper using Playwright with a saved authenticated session.

Keyword mode:  visits instagram.com/explore/tags/{keyword} and collects posts
               from the explore grid.
Creator mode:  visits each creator's profile and collects their latest posts.

Returns up to *max_results* PostCandidates ranked by engagement (highest first).
Raises SessionExpiredError if a login wall is detected.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

from backend.scraper.browser import get_context
from backend.scraper.errors import PostCandidate, SessionExpiredError

logger = logging.getLogger(__name__)

_BASE = "https://www.instagram.com"
_LOGIN_FRAGMENTS = ("/accounts/login", "/accounts/emailsignup")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scrape_instagram(
    keyword: str | None,
    creator_handles: list[str],
    max_results: int = 10,
) -> list[PostCandidate]:
    """Scrape Instagram and return up to *max_results* candidates ranked by engagement.

    Raises:
        SessionExpiredError: login wall detected during scraping.
        FileNotFoundError:   no session file — user must authenticate first.
    """
    candidates: list[PostCandidate] = []

    async with async_playwright() as pw:
        context = await get_context("instagram", pw)
        try:
            page = await context.new_page()
            page.set_default_timeout(20_000)

            if keyword:
                found = await _scrape_keyword(page, keyword, max_results)
                candidates.extend(found)

            for handle in creator_handles:
                if len(candidates) >= max_results:
                    break
                found = await _scrape_creator(page, handle, max_results - len(candidates))
                candidates.extend(found)

        finally:
            await context.browser.close()

    candidates.sort(key=lambda c: c.engagement, reverse=True)
    return candidates[:max_results]


# ---------------------------------------------------------------------------
# Login wall detection
# ---------------------------------------------------------------------------


def _is_login_url(url: str) -> bool:
    path = urlparse(url).path
    return any(path.startswith(frag) for frag in _LOGIN_FRAGMENTS)


async def _assert_not_login_wall(page: Page) -> None:
    if _is_login_url(page.url):
        raise SessionExpiredError("instagram")
    # Secondary check: login form present
    try:
        el = await page.query_selector('input[name="username"]')
        if el is not None:
            raise SessionExpiredError("instagram")
    except SessionExpiredError:
        raise
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Keyword scrape
# ---------------------------------------------------------------------------


async def _scrape_keyword(
    page: Page, keyword: str, limit: int
) -> list[PostCandidate]:
    url = f"{_BASE}/explore/tags/{keyword}/"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:
        logger.warning("Instagram keyword navigation failed: %s", exc)
        return []

    await _assert_not_login_wall(page)

    hrefs = await _collect_post_hrefs(page, limit * 3)
    return await _harvest_posts(page, hrefs, limit, from_creator=False)


# ---------------------------------------------------------------------------
# Creator profile scrape
# ---------------------------------------------------------------------------


async def _scrape_creator(
    page: Page, handle: str, limit: int
) -> list[PostCandidate]:
    url = f"{_BASE}/{handle.lstrip('@')}/"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:
        logger.warning("Instagram creator navigation failed for %s: %s", handle, exc)
        return []

    await _assert_not_login_wall(page)

    hrefs = await _collect_post_hrefs(page, limit * 2)
    return await _harvest_posts(page, hrefs, limit, from_creator=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _collect_post_hrefs(page: Page, limit: int) -> list[str]:
    """Return unique post URLs found on the current page."""
    seen: set[str] = set()
    hrefs: list[str] = []

    links = await page.query_selector_all('a[href*="/p/"]')
    for link in links:
        try:
            href = await link.get_attribute("href")
        except Exception:
            continue
        if not href:
            continue
        full = href if href.startswith("http") else f"{_BASE}{href}"
        full = full.split("?")[0].rstrip("/")
        if full not in seen:
            seen.add(full)
            hrefs.append(full)
        if len(hrefs) >= limit:
            break

    return hrefs


async def _harvest_posts(
    page: Page, hrefs: list[str], limit: int, *, from_creator: bool
) -> list[PostCandidate]:
    candidates: list[PostCandidate] = []
    for post_url in hrefs:
        if len(candidates) >= limit:
            break
        candidate = await _scrape_single_post(page, post_url, from_creator=from_creator)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


async def _scrape_single_post(
    page: Page, post_url: str, *, from_creator: bool
) -> Optional[PostCandidate]:
    try:
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20_000)
        await _assert_not_login_wall(page)

        creator = await _creator_from_page(page)
        engagement = await _engagement_from_page(page)
        screenshot_data = await _screenshot_post(page)

        return PostCandidate(
            source_url=post_url,
            creator=creator,
            engagement=engagement,
            screenshot_data=screenshot_data,
            from_creator=from_creator,
        )
    except SessionExpiredError:
        raise
    except Exception as exc:
        logger.debug("Failed to scrape Instagram post %s: %s", post_url, exc)
        return None


async def _creator_from_page(page: Page) -> str:
    """Try several selectors to extract the posting account handle."""
    selectors = [
        "article header a[role='link']",
        "header a[role='link']",
        "article header a",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _engagement_from_page(page: Page) -> int:
    """Return like count if visible, else 0."""
    selectors = [
        "button[type='button'] span",
        "section span",
    ]
    for sel in selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                text = (await el.inner_text()).strip().replace(",", "").replace(".", "")
                if text.endswith(" likes"):
                    num = text.replace(" likes", "").strip()
                    if num.isdigit():
                        return int(num)
        except Exception:
            continue
    return 0


async def _screenshot_post(page: Page) -> bytes:
    """Screenshot the post image; fall back to viewport screenshot."""
    img_selectors = [
        "article div[role='presentation'] img",
        "article img",
        "main img",
    ]
    for sel in img_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                return await el.screenshot()
        except Exception:
            continue
    try:
        return await page.screenshot(full_page=False)
    except Exception:
        return b""
