"""Instagram scraper using instaloader + Instagram's web API.

Keyword mode:  fetches top posts for a hashtag via /api/v1/tags/web_info/
               Paginates using next_max_id so runs 1-50, 51-100, … until
               enough fresh posts are collected or the feed is exhausted.
Creator mode:  fetches recent posts from each creator's profile

Images are fetched at full resolution using image_versions2.candidates[0]
(largest available, typically 1080px+).

Returns up to *max_results* PostCandidates ranked by engagement (highest first).
Raises SessionExpiredError if instaloader reports the session is invalid.
Raises FileNotFoundError if no session file exists yet.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import instaloader

from backend.scraper.errors import PostCandidate, SessionExpiredError
from backend.scraper.instagram_loader import get_any_loader

logger = logging.getLogger(__name__)

_IG_HEADERS = {
    "x-ig-app-id": "936619743392459",
    "x-requested-with": "XMLHttpRequest",
    "referer": "https://www.instagram.com/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

# Max pages to paginate through for hashtag "top" results (50 posts per page)
_HASHTAG_MAX_PAGES = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scrape_instagram(
    keyword: str | None,
    creator_handles: list[str],
    max_results: int = 10,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Scrape Instagram and return up to *max_results* fresh candidates ranked by engagement.

    Args:
        skip_urls: Source URLs already seen in the DB — skipped during collection
                   so the caller always receives only new posts.

    Raises:
        SessionExpiredError: session is invalid / expired.
        FileNotFoundError:   no session file — user must authenticate first.
    """
    try:
        L = get_any_loader()
    except instaloader.exceptions.BadCredentialsException:
        raise SessionExpiredError("instagram")

    loop = asyncio.get_event_loop()

    def _discover() -> list[PostCandidate]:
        candidates: list[PostCandidate] = []

        if keyword:
            tag = keyword.replace(" ", "").strip()
            if tag:
                try:
                    found = _scrape_hashtag(L, tag, max_results, skip_urls=skip_urls)
                    candidates.extend(found)
                except instaloader.exceptions.LoginRequiredException:
                    raise SessionExpiredError("instagram")
                except Exception as exc:
                    logger.warning("Instagram hashtag scrape failed for #%s: %s", tag, exc)

        for handle in creator_handles:
            if len(candidates) >= max_results:
                break
            try:
                found = _scrape_profile(
                    L, handle, max_results - len(candidates), skip_urls=skip_urls
                )
                candidates.extend(found)
            except instaloader.exceptions.LoginRequiredException:
                raise SessionExpiredError("instagram")
            except Exception as exc:
                logger.warning("Instagram profile scrape failed for %s: %s", handle, exc)

        candidates.sort(key=lambda c: c.engagement, reverse=True)
        return candidates[:max_results]

    return await loop.run_in_executor(None, _discover)


# ---------------------------------------------------------------------------
# Discovery helpers (instaloader, synchronous)
# ---------------------------------------------------------------------------


def _scrape_hashtag(
    L: instaloader.Instaloader,
    hashtag: str,
    limit: int,
    *,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Fetch top posts for a hashtag, paginating through batches of ~50.

    Stays exclusively within the "top" ranked feed. If page 1 (posts 1–50)
    doesn't yield enough fresh posts, fetches page 2 (51–100), page 3, etc.,
    up to _HASHTAG_MAX_PAGES. Never falls back to "recent".
    """
    tag = hashtag.lstrip("#")
    session = L.context._session

    seen_shortcodes: set[str] = set()
    candidates: list[PostCandidate] = []
    next_max_id: str | None = None  # pagination cursor; None = first page

    for page_num in range(1, _HASHTAG_MAX_PAGES + 1):
        url = f"https://www.instagram.com/api/v1/tags/web_info/?tag_name={tag}"
        if next_max_id:
            url += f"&next_max_id={next_max_id}"

        try:
            r = session.get(url, headers=_IG_HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("IG hashtag API error (page %d): %s", page_num, exc)
            break

        top = data.get("data", {}).get("top", {})
        sections = top.get("sections", [])

        page_new = 0
        for section in sections:
            for layout in section.get("layout_content", {}).get("medias", []):
                media = layout.get("media", {})
                shortcode = media.get("code") or media.get("shortcode")
                if not shortcode or shortcode in seen_shortcodes:
                    continue
                seen_shortcodes.add(shortcode)

                source_url = f"https://www.instagram.com/p/{shortcode}/"
                if skip_urls and source_url in skip_urls:
                    logger.debug("IG: skipping already-seen %s", source_url)
                    continue

                creator = media.get("user", {}).get("username", "")
                engagement = media.get("like_count", 0)
                img_versions = media.get("image_versions2", {}).get("candidates", [])
                thumb_url = img_versions[0].get("url") if img_versions else None
                screenshot_data = _fetch_url(session, thumb_url) if thumb_url else b""
                if not screenshot_data:
                    continue

                candidates.append(PostCandidate(
                    source_url=source_url,
                    creator=creator,
                    engagement=engagement,
                    screenshot_data=screenshot_data,
                    from_creator=False,
                ))
                page_new += 1

                if len(candidates) >= limit:
                    logger.debug("IG hashtag #%s: filled %d slots on page %d.", tag, limit, page_num)
                    return candidates

        logger.debug(
            "IG hashtag #%s page %d: %d new fresh posts; total %d/%d.",
            tag, page_num, page_new, len(candidates), limit,
        )

        # Advance cursor.  Log all top-level keys the first time so we can
        # confirm the real cursor field name from the API response.
        if page_num == 1:
            logger.info("IG hashtag top-object keys: %s", list(top.keys()))

        next_max_id = (
            top.get("next_max_id")
            or top.get("more_available_cursor")
            or top.get("next_cursor")
            or top.get("end_cursor")
        )
        if not next_max_id:
            logger.debug("IG hashtag #%s: no pagination cursor after page %d (keys: %s).",
                         tag, page_num, list(top.keys()))
            break
        logger.debug("IG hashtag #%s: advancing to page %d with cursor %r.", tag, page_num + 1, next_max_id[:20])

    return candidates


def _fetch_url(session: object, url: str) -> bytes:
    try:
        r = session.get(url, timeout=15)  # type: ignore[union-attr]
        r.raise_for_status()
        return r.content
    except Exception as exc:
        logger.debug("Failed to fetch image %s: %s", url, exc)
        return b""


def _scrape_profile(
    L: instaloader.Instaloader,
    handle: str,
    limit: int,
    *,
    skip_urls: set[str] | None = None,
) -> list[PostCandidate]:
    """Fetch recent posts from a creator profile, skipping already-seen URLs."""
    profile = instaloader.Profile.from_username(L.context, handle.lstrip("@"))
    candidates: list[PostCandidate] = []

    for post in profile.get_posts():
        if len(candidates) >= limit:
            break
        source_url = f"https://www.instagram.com/p/{post.shortcode}/"
        if skip_urls and source_url in skip_urls:
            logger.debug("IG: skipping already-seen profile post %s", source_url)
            continue
        c = _post_to_candidate(L, post, from_creator=True)
        if c:
            candidates.append(c)

    return candidates


def _post_to_candidate(
    L: instaloader.Instaloader,
    post: instaloader.Post,
    *,
    from_creator: bool,
) -> Optional[PostCandidate]:
    try:
        source_url = f"https://www.instagram.com/p/{post.shortcode}/"
        creator = post.owner_username
        engagement = post.likes
        # post.url is already the full-resolution image URL
        try:
            resp = L.context._session.get(post.url, timeout=15)
            resp.raise_for_status()
            screenshot_data = resp.content
        except Exception:
            screenshot_data = b""
        return PostCandidate(
            source_url=source_url,
            creator=creator,
            engagement=engagement,
            screenshot_data=screenshot_data,
            from_creator=from_creator,
        )
    except Exception as exc:
        logger.debug("Skipping post due to error: %s", exc)
        return None
