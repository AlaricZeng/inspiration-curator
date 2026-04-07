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
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from playwright.async_api import Page, async_playwright

from backend.scraper.browser import get_context
from backend.scraper.errors import PostCandidate, SessionExpiredError

logger = logging.getLogger(__name__)

_BASE = "https://www.xiaohongshu.com"
_LOGIN_PATH_FRAGMENTS = ("/login", "/website-login")
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
    keywords: list[str],
    creator_handles: list[str],
    max_results: int = 10,
    skip_urls: set[str] | None = None,
    keyword_limits: list[int] | None = None,
    creator_limits: list[int] | None = None,
) -> list[PostCandidate]:
    """Scrape Xiaohongshu and return up to *max_results* fresh candidates.

    Runs all keyword searches and creator profile scrapes in a single browser
    session to avoid the overhead of launching multiple browsers.

    Args:
        keywords: List of hashtags/keywords to search.
        creator_handles: List of creator handles to scrape.
        max_results: Global cap when keyword_limits/creator_limits are not set.
        skip_urls: Source URLs already seen in the DB — skipped during collection.
        keyword_limits: Per-keyword fetch budget. When provided, each keyword[i]
                        fetches exactly keyword_limits[i] posts (ignores max_results
                        cap for that keyword). Skips the keyword when limit is 0.
        creator_limits: Per-creator fetch budget, same semantics as keyword_limits.

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

            for i, keyword in enumerate(keywords):
                if keyword_limits is not None:
                    limit = keyword_limits[i] if i < len(keyword_limits) else 0
                else:
                    if len(candidates) >= max_results:
                        break
                    limit = max_results - len(candidates)
                if limit <= 0:
                    continue
                found = await _scrape_keyword(page, keyword, limit, skip_urls=skip_urls)
                candidates.extend(found)

            for i, handle in enumerate(creator_handles):
                if creator_limits is not None:
                    limit = creator_limits[i] if i < len(creator_limits) else 0
                else:
                    if len(candidates) >= max_results:
                        break
                    limit = max_results - len(candidates)
                if limit <= 0:
                    continue
                found = await _scrape_creator(page, handle, limit, skip_urls=skip_urls)
                candidates.extend(found)

        finally:
            await context.close()

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
    """Scrape XHS for *keyword*, returning up to *limit* fresh candidates.

    Scrolls through the feed harvesting cards not in skip_urls.
    XHS virtualizes the DOM (~18 cards visible at a time), so deduplication
    relies entirely on skip_urls rather than positional offsets.
    """
    url = f"{_BASE}/search_result?keyword={quote('#' + keyword.lstrip('#'))}&type=51"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(5_000)
    except Exception as exc:
        logger.warning("XHS keyword navigation failed: %s", exc)
        return []

    await _assert_not_login_wall(page)

    cookies = await page.context.cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    seen_urls: set[str] = set(skip_urls or [])
    candidates: list[PostCandidate] = []
    prev_card_count = 0

    all_seen_card_urls: set[str] = set()

    for scroll_round in range(1, _MAX_SCROLL_ROUNDS + 1):
        await _scroll_until_new_cards(page, all_seen_card_urls)

        all_cards = await _collect_all_cards(page)
        new_cards = [c for c in all_cards if c.url not in all_seen_card_urls]
        all_seen_card_urls.update(c.url for c in all_cards)

        if new_cards and len(candidates) < limit:
            batch_candidates = await _harvest_cards(
                new_cards, page, url, cookie_header, limit - len(candidates),
                from_creator=False, skip_urls=seen_urls,
            )
            for c in batch_candidates:
                seen_urls.add(c.source_url)
            candidates.extend(batch_candidates)

        logger.info(
            "XHS keyword %r round %d: %d new cards, %d harvested total.",
            keyword, scroll_round, len(new_cards), len(candidates),
        )

        if len(candidates) >= limit:
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
    seen_urls: set[str] = set(skip_urls or [])
    all_seen_card_urls: set[str] = set()

    all_cards = await _collect_all_cards(page)
    new_cards = [c for c in all_cards if c.url not in all_seen_card_urls]
    all_seen_card_urls.update(c.url for c in all_cards)

    if new_cards:
        batch_candidates = await _harvest_cards(
            new_cards, page, url, cookie_header, limit,
            from_creator=True, skip_urls=seen_urls,
        )
        candidates.extend(batch_candidates)

    logger.info(
        "XHS creator %r: %d new cards, %d harvested.",
        handle, len(new_cards), len(candidates),
    )

    return candidates


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------



async def _scroll_until_new_cards(page: Page, known_urls: set[str], max_attempts: int = 20) -> None:
    """Scroll down until at least one card URL appears that isn't in *known_urls*, or give up."""
    for _ in range(max_attempts):
        await page.evaluate("window.scrollBy(0, 800)")
        await page.wait_for_timeout(500)
        current_urls = {
            c.url for c in await _collect_all_cards(page)
        }
        if current_urls - known_urls:
            return
    await page.wait_for_timeout(300)


@dataclass
class _NoteCard:
    url: str
    cover_img_url: str
    creator: str = ""
    engagement: int = 0
    detail_url: str = ""  # full URL with xsec_token, opens the note modal when navigated to


async def _collect_all_cards(page: Page) -> list[_NoteCard]:
    """Collect ALL currently-visible note cards from the page (no limit).

    Iterates section.note-item elements to pair each note URL with its cover
    image and creator handle in one pass. Falls back to the old URL+image
    zip approach if no note-item sections are found.

    Returns cards in DOM order. The caller slices by offset to get only
    the cards that are new since the previous round.
    """
    cards: list[_NoteCard] = []
    seen_urls: set[str] = set()

    sections = await page.query_selector_all("section.note-item")
    if sections:
        for section in sections:
            try:
                # Note URL — strip query params from the hidden plain /explore/ link
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

                # Detail URL — full tokenised href from a.cover, needed to open the note modal
                detail_url = ""
                cover_link = await section.query_selector("a.cover")
                if cover_link:
                    href = await cover_link.get_attribute("href") or ""
                    detail_url = href if href.startswith("http") else f"{_BASE}{href}"

                # Creator display name from the author link text (first line, before the date)
                creator = ""
                author_el = await section.query_selector('a.author[href*="/user/profile/"]')
                if author_el:
                    text = (await author_el.inner_text()).strip()
                    creator = text.split("\n")[0].strip()

                # Cover image (exclude avatars)
                cover_src = ""
                for img_el in await section.query_selector_all("img[data-xhs-img]"):
                    src = (await img_el.get_attribute("src") or "").strip()
                    if src and not src.startswith("data:") and "avatar" not in src:
                        cover_src = src
                        break

                if not cover_src:
                    logger.debug("XHS: no cover image for %s, skipping", note_url)
                    continue

                cards.append(_NoteCard(url=note_url, cover_img_url=cover_src, creator=creator, detail_url=detail_url))
            except Exception:
                continue

        logger.debug("XHS DOM: %d note-item sections → %d cards", len(sections), len(cards))
        return cards

    # Fallback: zip note URLs with cover images (no creator info)
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
    for sel in ['img[data-xhs-img][elementtiming="card-exposed"]', 'img[data-xhs-img]']:
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

    logger.debug("XHS DOM fallback: %d note URLs, %d cover images", len(note_urls), len(cover_srcs))
    for i, url in enumerate(note_urls):
        cover_src = cover_srcs[i] if i < len(cover_srcs) else ""
        if not cover_src:
            continue
        cards.append(_NoteCard(url=url, cover_img_url=cover_src))

    return cards


async def _fetch_note_metadata(page: Page, note_url: str) -> tuple[list[str], str]:
    """Navigate to a note page and extract hashtags and the creator's handle.

    Hashtags: <a> elements whose text starts with '#' in the note description.
    Creator:  the author profile link href contains '/user/profile/<handle>'.

    Returns (tags, creator_handle). Either may be empty/blank if not found.
    """
    try:
        await page.goto(note_url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(1_500)
        await _assert_not_login_wall(page)
    except SessionExpiredError:
        raise
    except Exception as exc:
        logger.debug("XHS: failed to load note page %s: %s", note_url, exc)
        return [], ""

    tags: list[str] = []
    creator: str = ""

    try:
        # --- Creator handle ---
        for selector in [
            "a[href*='/user/profile/']",
            ".author-wrapper a",
            ".user-info a",
        ]:
            el = await page.query_selector(selector)
            if el:
                href = (await el.get_attribute("href") or "").strip()
                match = re.search(r"/user/profile/([^/?#]+)", href)
                if match:
                    creator = match.group(1)
                    break

        # --- Hashtags ---
        for selector in [
            "#detail-desc a",
            ".note-content a",
            ".desc a",
            "a[href*='search']",
        ]:
            els = await page.query_selector_all(selector)
            for el in els:
                text = (await el.inner_text()).strip()
                if text.startswith("#"):
                    tag = text.lstrip("#").strip()
                    if tag and tag not in tags:
                        tags.append(tag)
            if tags:
                break

        # Broad fallback: scan all <a> text for '#...' tokens
        if not tags:
            for el in await page.query_selector_all("a"):
                text = (await el.inner_text()).strip()
                if text.startswith("#"):
                    tag = text.lstrip("#").strip()
                    if tag and tag not in tags:
                        tags.append(tag)
    except Exception as exc:
        logger.debug("XHS: metadata extraction failed for %s: %s", note_url, exc)

    logger.debug("XHS: %s → creator=%r tags=%s", note_url, creator, tags)
    return tags, creator


async def _fetch_note_tags(page: Page, listing_url: str, detail_url: str) -> list[str]:
    """Navigate to a note's detail URL, extract hashtags from the modal, then return to listing.

    XHS notes only render inside a modal when navigated to via the tokenised
    cover URL (e.g. /search_result/<id>?xsec_token=...). Plain /explore/<id>
    URLs redirect to the feed. After extraction we navigate back to listing_url.

    Returns a list of tag strings (without the leading '#').
    """
    if not detail_url:
        return []
    try:
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_selector("#detail-desc", timeout=8_000)
    except Exception as exc:
        logger.debug("XHS: could not load note detail %s: %s", detail_url, exc)
        try:
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass
        return []

    tags: list[str] = []
    try:
        els = await page.query_selector_all("#detail-desc a.tag")
        for el in els:
            text = (await el.inner_text()).strip().lstrip("#").strip()
            if text and text not in tags:
                tags.append(text)
    except Exception as exc:
        logger.debug("XHS: tag extraction failed: %s", exc)

    logger.debug("XHS: %s → tags %s", detail_url, tags)

    try:
        await page.goto(listing_url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(1_000)
    except Exception as exc:
        logger.debug("XHS: failed to return to listing %s: %s", listing_url, exc)

    return tags


async def _harvest_cards(
    cards: list[_NoteCard],
    page: Page,
    listing_url: str,
    cookie_header: str,
    limit: int,
    *,
    from_creator: bool,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Download cover images and extract hashtags for *cards*, returning PostCandidates.

    For each fresh card: fetches the cover image via httpx, then navigates to
    the note's tokenised detail URL to extract hashtags from the modal, then
    returns to listing_url for the next scroll round.
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

            tags = await _fetch_note_tags(page, listing_url, card.detail_url)

            candidates.append(PostCandidate(
                source_url=card.url,
                creator=card.creator,
                engagement=card.engagement,
                screenshot_data=screenshot_data,
                from_creator=from_creator,
                tags=tags,
            ))

    return candidates
