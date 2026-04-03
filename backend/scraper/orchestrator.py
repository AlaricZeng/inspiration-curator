"""Scrape orchestrator — called by APScheduler and POST /api/run/now.

Flow
----
1. Get or create today's DailyRun record; mark it *running*.
2. Determine mode:
     - keyword mode: today's DailyRun has a keyword set (by the user via the UI)
     - vibe mode:    no keyword — use top-3 non-blocked VibeKeywords + all tracked creators
3. Run Instagram scraper  → pick top 5 results.
4. Run Xiaohongshu scraper → pick top 5 results.
5. Save each screenshot to staging/YYYY-MM-DD/.
6. Write Post records to the DB (status=pending).
7. Update DailyRun to *done* (or *failed* if both scrapers returned nothing).

On SessionExpiredError the session file for that platform is deleted so the
auth-status endpoint reflects "no session" → the UI can prompt re-authentication.
The other platform's scrape continues normally.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import random
import uuid
from pathlib import Path

from sqlmodel import Session, select

from backend.db.models import (
    Creator,
    DailyRun,
    Platform,
    Post,
    PostStatus,
    RunMode,
    RunStatus,
    VibeKeyword,
    engine,
)
from backend.scraper.browser import PLATFORM_CONFIG
from backend.scraper.errors import PostCandidate, SessionExpiredError
from backend.scraper.instagram import scrape_instagram as _scrape_instagram
from backend.scraper.xiaohongshu import scrape_xiaohongshu

logger = logging.getLogger(__name__)

STAGING_DIR = Path(__file__).parents[2] / "staging"

# Maximum candidates saved per platform per run; 5 per platform = 10 total
_PER_PLATFORM = 5
# How many extra candidates to fetch to account for duplicates seen in prior runs
_FETCH_MULTIPLIER = 4


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_scrape(force: bool = False) -> None:
    """Execute a full scrape cycle.

    Args:
        force: When True (manual run), reset a completed/failed run so new posts
               are always fetched.  When False (scheduler), skip if already done.
    """
    today = dt.date.today()
    run_id = _init_daily_run(today, force=force)
    if run_id is None:
        logger.info("Scrape already running or completed for %s — skipping.", today)
        return

    staging_dir = STAGING_DIR / str(today)
    staging_dir.mkdir(parents=True, exist_ok=True)

    ig_keyword, ig_handles, xhs_keyword, xhs_handles, mode = _resolve_scrape_params(today)

    # Pre-load already-seen URLs so scrapers can avoid them
    seen_urls = _get_seen_urls()
    fetch_limit = _PER_PLATFORM * _FETCH_MULTIPLIER

    # --- Instagram ---
    ig_candidates: list[PostCandidate] = []
    try:
        logger.info("Starting Instagram scrape (keyword=%r, handles=%s)", ig_keyword, ig_handles)
        results = await _scrape_instagram(
            keyword=ig_keyword,
            creator_handles=ig_handles,
            max_results=fetch_limit,
        )
        fresh = [c for c in results if c.source_url not in seen_urls]
        ig_candidates = _weighted_sample(fresh, _PER_PLATFORM)
        logger.info("Instagram returned %d candidates (%d after dedup, %d sampled).", len(results), len(fresh), len(ig_candidates))
    except SessionExpiredError:
        logger.warning("Instagram session expired — marking for re-auth.")
        _invalidate_instagram_session()
    except FileNotFoundError:
        logger.warning("No Instagram session — skipping platform.")
    except Exception as exc:
        logger.error("Instagram scrape failed: %s", exc, exc_info=True)

    # --- Xiaohongshu ---
    xhs_candidates: list[PostCandidate] = []
    try:
        logger.info("Starting Xiaohongshu scrape (keyword=%r, handles=%s)", xhs_keyword, xhs_handles)
        results = await scrape_xiaohongshu(
            keyword=xhs_keyword,
            creator_handles=xhs_handles,
            max_results=fetch_limit,
        )
        fresh = [c for c in results if c.source_url not in seen_urls]
        xhs_candidates = _weighted_sample(fresh, _PER_PLATFORM)
        logger.info("Xiaohongshu returned %d candidates (%d after dedup, %d sampled).", len(results), len(fresh), len(xhs_candidates))
    except SessionExpiredError:
        logger.warning("Xiaohongshu session expired — marking for re-auth.")
        _invalidate_session("xiaohongshu")

    except FileNotFoundError:
        logger.warning("No Xiaohongshu session — skipping platform.")
    except Exception as exc:
        logger.error("Xiaohongshu scrape failed: %s", exc, exc_info=True)

    # --- Persist results ---
    all_results: list[tuple[Platform, PostCandidate]] = [
        (Platform.instagram, c) for c in ig_candidates
    ] + [
        (Platform.xiaohongshu, c) for c in xhs_candidates
    ]

    _persist_results(run_id, all_results, staging_dir, mode)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _weighted_sample(candidates: list[PostCandidate], k: int) -> list[PostCandidate]:
    """Return up to *k* candidates sampled with probability proportional to engagement.

    Uses log(engagement + 2) as the weight so that high-engagement posts are
    favoured but low-engagement posts still have a real chance of appearing.
    Avoids pure top-N determinism — each run draws a different mix.
    """
    if len(candidates) <= k:
        return candidates
    weights = [math.log(max(c.engagement, 0) + 2) for c in candidates]
    return random.choices(candidates, weights=weights, k=k)


def _get_seen_urls() -> set[str]:
    """Return the set of all source_urls already persisted in the DB."""
    with Session(engine) as session:
        return set(session.exec(select(Post.source_url)).all())


def _init_daily_run(today: dt.date, force: bool = False) -> str | None:
    """Create or reset today's DailyRun and return its id.

    - force=False (scheduler): skip if already running or done.
    - force=True  (manual):    reset done/failed so a fresh run always proceeds.
      A run already *running* is still skipped to avoid concurrent scrapes.
    """
    with Session(engine) as session:
        existing = session.exec(
            select(DailyRun).where(DailyRun.run_date == today)
        ).first()

        if existing is not None:
            if existing.status == RunStatus.running:
                return None  # never run concurrently
            if existing.status == RunStatus.done and not force:
                return None  # scheduler: skip
            # force=True, or previously failed: reset and re-run
            existing.status = RunStatus.running
            session.add(existing)
            session.commit()
            return existing.id

        daily_run = DailyRun(
            run_date=today,
            status=RunStatus.running,
            mode=RunMode.vibe,
        )
        session.add(daily_run)
        session.commit()
        session.refresh(daily_run)
        return daily_run.id


def _resolve_scrape_params(
    today: dt.date,
) -> tuple[str | None, list[str], str | None, list[str], RunMode]:
    """Return (ig_keyword, ig_handles, xhs_keyword, xhs_handles, mode)."""
    with Session(engine) as session:
        daily_run = session.exec(
            select(DailyRun).where(DailyRun.run_date == today)
        ).first()

        if daily_run and daily_run.keyword:
            # Keyword mode: both platforms search the same keyword, no creator profiles
            kw = daily_run.keyword
            return kw, [], kw, [], RunMode.keyword

        # Vibe mode: top-3 non-blocked VibeKeywords — pinned first, then by frequency
        vibe_kws = session.exec(
            select(VibeKeyword)
            .where(VibeKeyword.user_blocked == False)  # noqa: E712
            .order_by(VibeKeyword.user_pinned.desc(), VibeKeyword.frequency.desc())
            .limit(3)
        ).all()
        keyword = vibe_kws[0].keyword if vibe_kws else None

        creators = session.exec(select(Creator)).all()
        ig_handles = [c.handle for c in creators if c.platform == Platform.instagram]
        xhs_handles = [c.handle for c in creators if c.platform == Platform.xiaohongshu]

        return keyword, ig_handles, keyword, xhs_handles, RunMode.vibe


def _persist_results(
    run_id: str,
    results: list[tuple[Platform, PostCandidate]],
    staging_dir: Path,
    mode: RunMode,
) -> None:
    with Session(engine) as session:
        # Collect all source_urls already stored so we don't show duplicates
        existing_urls: set[str] = set(
            session.exec(select(Post.source_url)).all()
        )

        new_count = 0
        for platform, candidate in results:
            if candidate.source_url in existing_urls:
                logger.debug("Skipping already-seen post: %s", candidate.source_url)
                continue

            screenshot_path: str | None = None
            if candidate.screenshot_data:
                # Detect actual format: JPEG starts with FF D8, PNG with 89 50 4E 47
                ext = "jpg" if candidate.screenshot_data[:2] == b"\xff\xd8" else "png"
                filename = f"{platform.value}_{uuid.uuid4().hex[:8]}.{ext}"
                screenshot_file = staging_dir / filename
                try:
                    screenshot_file.write_bytes(candidate.screenshot_data)
                    screenshot_path = str(screenshot_file)
                except Exception as exc:
                    logger.warning("Failed to save screenshot %s: %s", filename, exc)

            post = Post(
                platform=platform,
                source_url=candidate.source_url,
                creator=candidate.creator,
                screenshot=screenshot_path,
                engagement=candidate.engagement,
                status=PostStatus.pending,
            )
            session.add(post)
            existing_urls.add(candidate.source_url)
            new_count += 1

        # Update DailyRun
        daily_run = session.get(DailyRun, run_id)
        if daily_run is not None:
            daily_run.mode = mode
            daily_run.status = RunStatus.done if results else RunStatus.failed
            session.add(daily_run)

        session.commit()
        logger.info(
            "Persisted %d new posts for run %s (%d dupes skipped, status=%s).",
            new_count,
            run_id,
            len(results) - new_count,
            RunStatus.done if results else RunStatus.failed,
        )


def _invalidate_instagram_session() -> None:
    """Delete the instaloader session so auth status shows 'not authenticated'."""
    from backend.scraper.instagram_loader import delete_session
    try:
        delete_session()
        logger.info("Deleted Instagram (instaloader) session.")
    except Exception as exc:
        logger.warning("Could not delete Instagram session: %s", exc)


def _invalidate_session(platform: str) -> None:
    """Delete the saved Playwright session file (used for Xiaohongshu)."""
    session_file = Path(PLATFORM_CONFIG[platform]["session_file"])
    try:
        session_file.unlink(missing_ok=True)
        logger.info("Deleted session file for %s.", platform)
    except Exception as exc:
        logger.warning("Could not delete session file for %s: %s", platform, exc)
