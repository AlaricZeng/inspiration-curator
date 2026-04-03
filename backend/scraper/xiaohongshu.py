"""Xiaohongshu (小红书 / RedNote) scraper using Playwright with a saved session.

Keyword mode:  searches xiaohongshu.com/search_result?keyword={keyword}
Creator mode:  visits each creator's profile page and collects their latest notes.

Engagement signal = 点赞数 (likes) + 收藏数 (saves/collects).

Returns up to *max_results* PostCandidates ranked by engagement (highest first).
Raises SessionExpiredError if a login wall is detected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
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

    cards = await _collect_note_cards(page, limit * 3)
    return await _harvest_notes(page, cards, limit, from_creator=False)


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

    cards = await _collect_note_cards(page, limit * 2)
    return await _harvest_notes(page, cards, limit, from_creator=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass
class _NoteCard:
    url: str
    cover_img_url: str
    creator: str = ""
    engagement: int = 0


async def _collect_note_cards(page: Page, limit: int) -> list[_NoteCard]:
    """Return note cards (url + cover image URL) from the current listing page.

    XHS renders a grid of cards; each card is an <a href="/explore/{id}"> that
    contains an <img class=""> inside a parent with class "cover mask".
    We grab both the note URL and the CDN image URL in one pass — no navigation
    needed and no login wall to hit.
    """
    seen: set[str] = set()
    cards: list[_NoteCard] = []

    link_patterns = ['a[href*="/explore/"]', 'a[href*="/discovery/item/"]']
    for pattern in link_patterns:
        links = await page.query_selector_all(pattern)
        for link in links:
            try:
                href = await link.get_attribute("href")
                if not href:
                    continue
                full_url = (href if href.startswith("http") else f"{_BASE}{href}").split("?")[0].rstrip("/")
                if full_url in seen:
                    continue

                # Find the cover image inside this card link
                img = await link.query_selector("img")
                cover_src = ""
                if img:
                    cover_src = (await img.get_attribute("src") or "").strip()

                # Skip if no cover image (probably a non-post link)
                if not cover_src or cover_src.startswith("data:"):
                    continue

                seen.add(full_url)

                # Try to read engagement count from the card
                engagement = 0
                like_el = await link.query_selector(".count, .like-count, span[class*='count']")
                if like_el:
                    try:
                        txt = (await like_el.inner_text()).strip().replace(",", "")
                        if txt.isdigit():
                            engagement = int(txt)
                        elif txt.endswith("万"):
                            engagement = int(float(txt[:-1]) * 10_000)
                    except Exception:
                        pass

                # Creator name from card if available
                creator = ""
                creator_el = await link.query_selector(".author-wrapper .name, .username, .nickname, .author-name")
                if creator_el:
                    try:
                        creator = (await creator_el.inner_text()).strip()
                    except Exception:
                        pass

                cards.append(_NoteCard(url=full_url, cover_img_url=cover_src, creator=creator, engagement=engagement))
                if len(cards) >= limit:
                    return cards
            except Exception:
                continue

    return cards


async def _harvest_notes(
    page: Page, cards: list[_NoteCard], limit: int, *, from_creator: bool
) -> list[PostCandidate]:
    """Download cover images via HTTP and build PostCandidates — no page navigation needed."""
    candidates: list[PostCandidate] = []

    # Grab cookies from the browser context to authenticate CDN image requests
    cookies = await page.context.cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for card in cards:
            if len(candidates) >= limit:
                break
            try:
                resp = await client.get(
                    card.cover_img_url,
                    headers={
                        "Referer": "https://www.xiaohongshu.com/",
                        "Cookie": cookie_header,
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                    },
                )
                if resp.status_code == 200 and resp.content:
                    screenshot_data = resp.content
                else:
                    logger.debug("XHS cover image fetch failed %s: HTTP %s", card.cover_img_url, resp.status_code)
                    screenshot_data = b""
            except Exception as exc:
                logger.debug("XHS cover image download error for %s: %s", card.url, exc)
                screenshot_data = b""

            if not screenshot_data:
                continue

            candidates.append(PostCandidate(
                source_url=card.url,
                creator=card.creator,
                engagement=card.engagement,
                screenshot_data=screenshot_data,
                from_creator=from_creator,
            ))

    return candidates


