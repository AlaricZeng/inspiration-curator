"""Instagram scraper using instaloader (no browser required).

Keyword mode:  fetches posts from instagram.com/explore/tags/{keyword}
Creator mode:  fetches recent posts from each creator's profile

Returns up to *max_results* PostCandidates ranked by engagement (highest first).
Raises SessionExpiredError if instaloader reports the session is invalid.
Raises FileNotFoundError if no session file exists yet.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

import instaloader

from backend.scraper.errors import PostCandidate, SessionExpiredError
from backend.scraper.instagram_loader import get_any_loader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrape_instagram(
    keyword: str | None,
    creator_handles: list[str],
    max_results: int = 10,
) -> list[PostCandidate]:
    """Scrape Instagram and return up to *max_results* candidates ranked by engagement.

    Raises:
        SessionExpiredError: session is invalid / expired.
        FileNotFoundError:   no session file — user must authenticate first.
    """
    try:
        L = get_any_loader()
    except instaloader.exceptions.BadCredentialsException:
        raise SessionExpiredError("instagram")

    candidates: list[PostCandidate] = []

    if keyword:
        # Hashtags can't have spaces — use only the first word, or skip if empty
        tag = keyword.replace(" ", "").strip()
        if tag:
            try:
                found = _scrape_hashtag(L, tag, max_results * 2)
                candidates.extend(found)
            except instaloader.exceptions.LoginRequiredException:
                raise SessionExpiredError("instagram")
            except Exception as exc:
                logger.warning("Instagram hashtag scrape failed for #%s: %s", tag, exc)

    for handle in creator_handles:
        if len(candidates) >= max_results:
            break
        try:
            found = _scrape_profile(L, handle, max_results - len(candidates))
            candidates.extend(found)
        except instaloader.exceptions.LoginRequiredException:
            raise SessionExpiredError("instagram")
        except Exception as exc:
            logger.warning("Instagram profile scrape failed for %s: %s", handle, exc)

    candidates.sort(key=lambda c: c.engagement, reverse=True)
    return candidates[:max_results]


# ---------------------------------------------------------------------------
# Synchronous scraping helpers (instaloader is synchronous)
# ---------------------------------------------------------------------------

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


def _scrape_hashtag(
    L: instaloader.Instaloader, hashtag: str, limit: int
) -> list[PostCandidate]:
    """Fetch top posts from a hashtag via Instagram's web API v1."""
    tag = hashtag.lstrip("#")
    session = L.context._session
    r = session.get(
        f"https://www.instagram.com/api/v1/tags/web_info/?tag_name={tag}",
        headers=_IG_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()

    candidates: list[PostCandidate] = []
    sections = data.get("data", {}).get("top", {}).get("sections", [])
    for section in sections:
        for layout in section.get("layout_content", {}).get("medias", []):
            if len(candidates) >= limit:
                break
            media = layout.get("media", {})
            shortcode = media.get("code") or media.get("shortcode")
            if not shortcode:
                continue
            source_url = f"https://www.instagram.com/p/{shortcode}/"
            creator = media.get("user", {}).get("username", "")
            engagement = media.get("like_count", 0)
            # Grab thumbnail URL
            thumb_url = None
            img_versions = media.get("image_versions2", {}).get("candidates", [])
            if img_versions:
                thumb_url = img_versions[-1].get("url")  # smallest thumbnail
            screenshot_data = _fetch_url(session, thumb_url) if thumb_url else b""
            candidates.append(PostCandidate(
                source_url=source_url,
                creator=creator,
                engagement=engagement,
                screenshot_data=screenshot_data,
                from_creator=False,
            ))

    return candidates


def _fetch_url(session: object, url: str) -> bytes:
    try:
        r = session.get(url, timeout=10)  # type: ignore[union-attr]
        r.raise_for_status()
        return r.content
    except Exception as exc:
        logger.debug("Failed to fetch image %s: %s", url, exc)
        return b""


def _scrape_profile(
    L: instaloader.Instaloader, handle: str, limit: int
) -> list[PostCandidate]:
    """Fetch recent posts from a creator profile."""
    profile = instaloader.Profile.from_username(L.context, handle.lstrip("@"))
    candidates: list[PostCandidate] = []

    for post in profile.get_posts():
        if len(candidates) >= limit:
            break
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

        # Download thumbnail image into memory
        screenshot_data = _fetch_thumbnail(L, post)

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


def _fetch_thumbnail(L: instaloader.Instaloader, post: instaloader.Post) -> bytes:
    """Download the post thumbnail into memory and return raw bytes."""
    try:
        url = post.url  # direct image/video thumbnail URL
        with L.context.get_json(url, params={}) as _:
            pass  # just a probe; use httpx below
    except Exception:
        pass

    # Use instaloader's underlying session to fetch the image bytes
    try:
        resp = L.context._session.get(post.url, timeout=10)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.debug("Could not fetch thumbnail for %s: %s", post.shortcode, exc)
        return b""
