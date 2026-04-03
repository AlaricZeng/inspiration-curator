"""Xiaohongshu (小红书 / RedNote) scraper using Playwright with a saved session.

Keyword mode:  searches xiaohongshu.com/search_result?keyword={keyword}
Creator mode:  visits each creator's profile page and collects their latest notes.

Engagement signal = 点赞数 (likes) + 收藏数 (saves/collects).

Returns up to *max_results* PostCandidates ranked by engagement (highest first).
Raises SessionExpiredError if a login wall is detected.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote, urlparse

from playwright.async_api import Page, async_playwright

from backend.scraper.browser import get_context
from backend.scraper.errors import PostCandidate, SessionExpiredError

logger = logging.getLogger(__name__)

_BASE = "https://www.xiaohongshu.com"
_LOGIN_PATH_FRAGMENTS = ("/login",)
_LOGIN_MODAL_SELECTORS = (
    'div[data-testid="login-modal"]',
    '.login-container',
    'div.login-popup',
    'input[placeholder*="手机号"]',
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scrape_xiaohongshu(
    keyword: str | None,
    creator_handles: list[str],
    max_results: int = 10,
) -> list[PostCandidate]:
    """Scrape Xiaohongshu and return up to *max_results* candidates ranked by engagement.

    Raises:
        SessionExpiredError: login wall detected during scraping.
        FileNotFoundError:   no session file — user must authenticate first.
    """
    candidates: list[PostCandidate] = []

    async with async_playwright() as pw:
        context = await get_context("xiaohongshu", pw)
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
# Keyword scrape
# ---------------------------------------------------------------------------


async def _scrape_keyword(
    page: Page, keyword: str, limit: int
) -> list[PostCandidate]:
    # type=51 filters to note/post results
    url = f"{_BASE}/search_result?keyword={quote(keyword)}&type=51"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # Give JS-rendered cards a moment to appear
        await page.wait_for_timeout(2_000)
    except Exception as exc:
        logger.warning("XHS keyword navigation failed: %s", exc)
        return []

    await _assert_not_login_wall(page)

    note_urls = await _collect_note_urls(page, limit * 3)
    return await _harvest_notes(page, note_urls, limit, from_creator=False)


# ---------------------------------------------------------------------------
# Creator profile scrape
# ---------------------------------------------------------------------------


async def _scrape_creator(
    page: Page, handle: str, limit: int
) -> list[PostCandidate]:
    # XHS creator profiles use a userId path; handle may be a userId or username.
    # Try the user/profile path first; many tools store the numeric userId as handle.
    url = f"{_BASE}/user/profile/{handle.lstrip('@')}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)
    except Exception as exc:
        logger.warning("XHS creator navigation failed for %s: %s", handle, exc)
        return []

    await _assert_not_login_wall(page)

    note_urls = await _collect_note_urls(page, limit * 2)
    return await _harvest_notes(page, note_urls, limit, from_creator=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _collect_note_urls(page: Page, limit: int) -> list[str]:
    """Return unique note/post URLs visible on the current page."""
    seen: set[str] = set()
    urls: list[str] = []

    # Note cards link to /explore/{noteId} or /discovery/item/{noteId}
    link_patterns = ['a[href*="/explore/"]', 'a[href*="/discovery/item/"]']
    for pattern in link_patterns:
        links = await page.query_selector_all(pattern)
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
                urls.append(full)
            if len(urls) >= limit:
                return urls

    return urls


async def _harvest_notes(
    page: Page, note_urls: list[str], limit: int, *, from_creator: bool
) -> list[PostCandidate]:
    candidates: list[PostCandidate] = []
    for url in note_urls:
        if len(candidates) >= limit:
            break
        candidate = await _scrape_single_note(page, url, from_creator=from_creator)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


async def _scrape_single_note(
    page: Page, note_url: str, *, from_creator: bool
) -> Optional[PostCandidate]:
    try:
        await page.goto(note_url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(1_500)
        await _assert_not_login_wall(page)

        creator = await _creator_from_page(page)
        engagement = await _engagement_from_page(page)

        screenshot_data = await _screenshot_note(page)

        return PostCandidate(
            source_url=note_url,
            creator=creator,
            engagement=engagement,
            screenshot_data=screenshot_data,
            from_creator=from_creator,
        )
    except SessionExpiredError:
        raise
    except Exception as exc:
        logger.debug("Failed to scrape XHS note %s: %s", note_url, exc)
        return None


async def _creator_from_page(page: Page) -> str:
    """Try several selectors to extract the creator's handle / display name."""
    selectors = [
        ".author-wrapper .username",
        ".author .name",
        'a[href*="/user/profile/"] span',
        ".user-name",
        ".nickname",
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
    """Return combined 点赞数 + 收藏数; fall back to 0."""
    total = 0

    # Selectors for like (点赞) count
    like_selectors = [
        ".like-wrapper .count",
        ".likes .count",
        'span[class*="like"] span',
        'div[class*="like"] span.count',
    ]
    # Selectors for collect/save (收藏) count
    collect_selectors = [
        ".collect-wrapper .count",
        ".collect .count",
        'span[class*="collect"] span',
        'div[class*="collect"] span.count',
    ]

    for group in (like_selectors, collect_selectors):
        for sel in group:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip().replace(",", "")
                    if text.isdigit():
                        total += int(text)
                        break
                    # Handle abbreviated counts like "1.2万" (12 000)
                    if text.endswith("万"):
                        try:
                            total += int(float(text[:-1]) * 10_000)
                        except ValueError:
                            pass
                        break
            except Exception:
                continue

    return total


async def _screenshot_note(page: Page) -> bytes:
    """Screenshot the note image; fall back to viewport screenshot."""
    img_selectors = [
        ".note-detail img",
        ".swiper-slide img",
        "div.media-container img",
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
