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
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Scrape Xiaohongshu and return up to *max_results* candidates ranked by engagement.

    Args:
        skip_urls: Set of source URLs already seen in the DB. The scraper will
                   scroll deeper to find fresh posts when candidates are exhausted.

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
                found = await _scrape_keyword(page, keyword, max_results, skip_urls=skip_urls)
                candidates.extend(found)

            for handle in creator_handles:
                if len(candidates) >= max_results:
                    break
                found = await _scrape_creator(
                    page, handle, max_results - len(candidates), skip_urls=skip_urls
                )
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
    page: Page, keyword: str, limit: int, *, skip_urls: set[str] | None = None
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

    # Scroll progressively, retrying until we collect enough fresh candidates
    # or hit a maximum scroll-round cap.
    _MAX_SCROLL_ROUNDS = 8
    for scroll_round in range(1, _MAX_SCROLL_ROUNDS + 1):
        await _scroll_to_load_cards(page, steps=scroll_round * 4)
        cards = await _collect_note_cards(page, limit * (scroll_round + 2))
        candidates = await _harvest_notes(page, cards, limit, from_creator=False, skip_urls=skip_urls)
        if len(candidates) >= limit:
            logger.debug("XHS keyword: filled %d slots after %d scroll round(s).", limit, scroll_round)
            return candidates
        logger.debug(
            "XHS keyword scroll round %d: %d/%d fresh candidates; scrolling deeper.",
            scroll_round, len(candidates), limit,
        )

    # Best-effort: return whatever we managed to find
    cards = await _collect_note_cards(page, limit * 10)
    return await _harvest_notes(page, cards, limit, from_creator=False, skip_urls=skip_urls)


# ---------------------------------------------------------------------------
# Creator profile scrape
# ---------------------------------------------------------------------------


async def _scrape_creator(
    page: Page, handle: str, limit: int, *, skip_urls: set[str] | None = None
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

    # Scroll progressively until we collect enough fresh candidates for this creator
    _MAX_SCROLL_ROUNDS = 6
    for scroll_round in range(1, _MAX_SCROLL_ROUNDS + 1):
        await _scroll_to_load_cards(page, steps=scroll_round * 4)
        cards = await _collect_note_cards(page, limit * (scroll_round + 1))
        candidates = await _harvest_notes(page, cards, limit, from_creator=True, skip_urls=skip_urls)
        if len(candidates) >= limit:
            logger.debug("XHS creator %s: filled %d slots after %d scroll round(s).", handle, limit, scroll_round)
            return candidates
        logger.debug(
            "XHS creator %s scroll round %d: %d/%d fresh candidates; scrolling deeper.",
            handle, scroll_round, len(candidates), limit,
        )

    cards = await _collect_note_cards(page, limit * 8)
    return await _harvest_notes(page, cards, limit, from_creator=True, skip_urls=skip_urls)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _scroll_to_load_cards(page: Page, steps: int = 4) -> None:
    """Scroll down incrementally to trigger lazy-loading of card images."""
    for _ in range(steps):
        await page.evaluate("window.scrollBy(0, 800)")
        await page.wait_for_timeout(500)
    # Scroll back to top so card order matches link order
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(300)


@dataclass
class _NoteCard:
    url: str
    cover_img_url: str
    creator: str = ""
    engagement: int = 0


async def _collect_note_cards(page: Page, limit: int) -> list[_NoteCard]:
    """Return note cards (url + cover image URL) from the current listing page.

    XHS uses a Vue component tree where the <a href="/explore/{id}"> and the
    <img data-xhs-img elementtiming="card-exposed"> are siblings, not nested.
    We collect them separately in DOM order and pair by index.
    """
    seen: set[str] = set()
    cards: list[_NoteCard] = []

    # Collect all explore/discovery links in DOM order
    note_urls: list[str] = []
    for pattern in ['a[href*="/explore/"]', 'a[href*="/discovery/item/"]']:
        links = await page.query_selector_all(pattern)
        for link in links:
            try:
                href = await link.get_attribute("href") or ""
                full_url = (href if href.startswith("http") else f"{_BASE}{href}").split("?")[0].rstrip("/")
                if full_url not in seen:
                    seen.add(full_url)
                    note_urls.append(full_url)
            except Exception:
                continue

    # Collect all post cover images (identified by XHS-specific attributes)
    # These are the grid thumbnail images — NOT avatars
    cover_srcs: list[str] = []
    img_selectors = [
        'img[data-xhs-img][elementtiming="card-exposed"]',  # primary: XHS card images
        'img[data-xhs-img]',                                  # fallback: any xhs img
    ]
    for sel in img_selectors:
        imgs = await page.query_selector_all(sel)
        if imgs:
            for img in imgs:
                try:
                    src = (await img.get_attribute("src") or "").strip()
                    if src and not src.startswith("data:") and "avatar" not in src:
                        cover_srcs.append(src)
                except Exception:
                    continue
            break  # use whichever selector matched first

    logger.debug("XHS: found %d note URLs, %d cover images", len(note_urls), len(cover_srcs))

    # Pair by index — they appear in the same order in the DOM
    for i, url in enumerate(note_urls):
        if len(cards) >= limit:
            break
        cover_src = cover_srcs[i] if i < len(cover_srcs) else ""
        if not cover_src:
            logger.debug("XHS: no cover image for card %d (%s), skipping", i, url)
            continue
        cards.append(_NoteCard(url=url, cover_img_url=cover_src))

    return cards


async def _harvest_notes(
    page: Page,
    cards: list[_NoteCard],
    limit: int,
    *,
    from_creator: bool,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Download cover images via HTTP and build PostCandidates — no page navigation needed.

    Cards whose source_url is in *skip_urls* are skipped so we only return
    posts that haven't been seen before.
    """
    candidates: list[PostCandidate] = []

    # Grab cookies from the browser context to authenticate CDN image requests
    cookies = await page.context.cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for card in cards:
            if len(candidates) >= limit:
                break
            # Skip posts already present in the DB
            if skip_urls and card.url in skip_urls:
                logger.debug("XHS: skipping already-seen URL %s", card.url)
                continue
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


