"""Xiaohongshu (小红书 / RedNote) scraper using Playwright with a saved session.

Keyword mode:  searches xiaohongshu.com/search_result?keyword={keyword}
Creator mode:  visits each creator's profile page and collects their latest notes.

Engagement signal = 点赞数 (likes) + 收藏数 (saves/collects).

Pagination strategy
-------------------
XHS is an infinite-scroll SPA. We scroll the page in rounds to reveal new
cards, tracking a *processed_offset* so each round only harvests cards that
were not visible in the previous round (like paging through results 1-50,
51-100, etc.). We keep scrolling until we have *limit* fresh candidates or
we hit _MAX_SCROLL_ROUNDS with no new cards appearing.

Returns up to *max_results* PostCandidates ranked by engagement (highest first).
Raises SessionExpiredError if a login wall is detected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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

# Each scroll round adds this many scroll steps (each step = 800 px)
_SCROLL_STEPS_PER_ROUND = 4
# Maximum number of scroll rounds before giving up
_MAX_SCROLL_ROUNDS = 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scrape_xiaohongshu(
    keyword: str | None,
    creator_handles: list[str],
    max_results: int = 10,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Scrape Xiaohongshu and return up to *max_results* fresh candidates.

    Args:
        skip_urls: Source URLs already seen in the DB. The scraper scrolls to
                   later batches of posts when earlier ones are all duplicates,
                   mirroring a paginated "next page" approach.

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
    """Scrape a keyword search, paging through batches of cards by scrolling.

    Each round scrolls deeper and harvests only the *new* cards that have
    appeared since the previous round (offset-based batching).
    """
    url = f"{_BASE}/search_result?keyword={quote(keyword)}&type=51"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)
    except Exception as exc:
        logger.warning("XHS keyword navigation failed: %s", exc)
        return []

    await _assert_not_login_wall(page)

    # Grab cookies once for the whole harvest session
    cookies = await page.context.cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    candidates: list[PostCandidate] = []
    processed_offset = 0  # how many cards we've already attempted to harvest
    seen_urls: set[str] = set(skip_urls or [])

    for scroll_round in range(1, _MAX_SCROLL_ROUNDS + 1):
        # Scroll down to reveal the next batch of cards
        await _scroll_down(page, steps=_SCROLL_STEPS_PER_ROUND)

        all_cards = await _collect_all_cards(page)
        new_cards = all_cards[processed_offset:]

        if not new_cards:
            logger.debug("XHS keyword round %d: no new cards appeared; stopping.", scroll_round)
            break

        logger.debug(
            "XHS keyword round %d: %d total cards, %d new (offset %d).",
            scroll_round, len(all_cards), len(new_cards), processed_offset,
        )
        processed_offset = len(all_cards)

        # Harvest the new batch
        batch_candidates = await _harvest_cards(
            new_cards, cookie_header, limit - len(candidates),
            from_creator=False, skip_urls=seen_urls,
        )
        for c in batch_candidates:
            seen_urls.add(c.source_url)
        candidates.extend(batch_candidates)

        logger.debug(
            "XHS keyword round %d: +%d fresh posts; total %d/%d.",
            scroll_round, len(batch_candidates), len(candidates), limit,
        )

        if len(candidates) >= limit:
            logger.debug("XHS keyword: filled %d slots after %d round(s).", limit, scroll_round)
            break

    return candidates


# ---------------------------------------------------------------------------
# Creator profile scrape
# ---------------------------------------------------------------------------


async def _scrape_creator(
    page: Page, handle: str, limit: int, *, skip_urls: set[str] | None = None
) -> list[PostCandidate]:
    """Scrape a creator profile, paging through batches of cards by scrolling."""
    url = f"{_BASE}/user/profile/{handle.lstrip('@')}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)
    except Exception as exc:
        logger.warning("XHS creator navigation failed for %s: %s", handle, exc)
        return []

    await _assert_not_login_wall(page)

    cookies = await page.context.cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    candidates: list[PostCandidate] = []
    processed_offset = 0
    seen_urls: set[str] = set(skip_urls or [])

    for scroll_round in range(1, _MAX_SCROLL_ROUNDS + 1):
        await _scroll_down(page, steps=_SCROLL_STEPS_PER_ROUND)

        all_cards = await _collect_all_cards(page)
        new_cards = all_cards[processed_offset:]

        if not new_cards:
            logger.debug("XHS creator %s round %d: no new cards; stopping.", handle, scroll_round)
            break

        logger.debug(
            "XHS creator %s round %d: %d total cards, %d new (offset %d).",
            handle, scroll_round, len(all_cards), len(new_cards), processed_offset,
        )
        processed_offset = len(all_cards)

        batch_candidates = await _harvest_cards(
            new_cards, cookie_header, limit - len(candidates),
            from_creator=True, skip_urls=seen_urls,
        )
        for c in batch_candidates:
            seen_urls.add(c.source_url)
        candidates.extend(batch_candidates)

        logger.debug(
            "XHS creator %s round %d: +%d fresh posts; total %d/%d.",
            handle, scroll_round, len(batch_candidates), len(candidates), limit,
        )

        if len(candidates) >= limit:
            logger.debug("XHS creator %s: filled %d slots after %d round(s).", handle, limit, scroll_round)
            break

    return candidates


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _scroll_down(page: Page, steps: int = _SCROLL_STEPS_PER_ROUND) -> None:
    """Scroll down by *steps* increments to trigger lazy-loading of new cards."""
    for _ in range(steps):
        await page.evaluate("window.scrollBy(0, 800)")
        await page.wait_for_timeout(500)
    # Brief pause for JS rendering after the final scroll
    await page.wait_for_timeout(300)


@dataclass
class _NoteCard:
    url: str
    cover_img_url: str
    creator: str = ""
    engagement: int = 0


async def _collect_all_cards(page: Page) -> list[_NoteCard]:
    """Collect ALL currently-visible note cards from the page (no limit).

    Returns them in DOM order. The caller slices by offset to get only
    the cards that are new since the previous round.
    """
    seen_urls: set[str] = set()
    note_urls: list[str] = []

    for pattern in ['a[href*="/explore/"]', 'a[href*="/discovery/item/"]']:
        links = await page.query_selector_all(pattern)
        for link in links:
            try:
                href = await link.get_attribute("href") or ""
                full_url = (href if href.startswith("http") else f"{_BASE}{href}").split("?")[0].rstrip("/")
                if full_url not in seen_urls:
                    seen_urls.add(full_url)
                    note_urls.append(full_url)
            except Exception:
                continue

    cover_srcs: list[str] = []
    for sel in [
        'img[data-xhs-img][elementtiming="card-exposed"]',
        'img[data-xhs-img]',
    ]:
        imgs = await page.query_selector_all(sel)
        if imgs:
            for img in imgs:
                try:
                    src = (await img.get_attribute("src") or "").strip()
                    if src and not src.startswith("data:") and "avatar" not in src:
                        cover_srcs.append(src)
                except Exception:
                    continue
            break

    logger.debug("XHS DOM: %d note URLs, %d cover images", len(note_urls), len(cover_srcs))

    cards: list[_NoteCard] = []
    for i, url in enumerate(note_urls):
        cover_src = cover_srcs[i] if i < len(cover_srcs) else ""
        if not cover_src:
            logger.debug("XHS: no cover image for card %d (%s), skipping", i, url)
            continue
        cards.append(_NoteCard(url=url, cover_img_url=cover_src))

    return cards


async def _harvest_cards(
    cards: list[_NoteCard],
    cookie_header: str,
    limit: int,
    *,
    from_creator: bool,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Download cover images for *cards* and return PostCandidates.

    Only processes up to *limit* fresh cards (those not in *skip_urls*).
    """
    candidates: list[PostCandidate] = []

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for card in cards:
            if len(candidates) >= limit:
                break
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
                    logger.debug(
                        "XHS cover fetch failed %s: HTTP %s",
                        card.cover_img_url, resp.status_code,
                    )
                    screenshot_data = b""
            except Exception as exc:
                logger.debug("XHS cover download error for %s: %s", card.url, exc)
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
