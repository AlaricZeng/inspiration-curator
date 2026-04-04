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
    PlatformRun,
    PlatformStatus,
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
# How many candidates to fetch per platform before dedup + sampling
# Large pool ensures we can still fill 5 slots even after heavy dedup
_FETCH_LIMIT = 50


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

    # Create PlatformRun records so the UI can track per-platform progress
    ig_run_id = _init_platform_run(run_id, Platform.instagram)
    xhs_run_id = _init_platform_run(run_id, Platform.xiaohongshu)

    # Pre-load already-seen URLs so scrapers can avoid them
    seen_urls = _get_seen_urls()

    # --- Instagram ---
    ig_candidates: list[PostCandidate] = []
    try:
        logger.info("Starting Instagram scrape (keyword=%r, handles=%s)", ig_keyword, ig_handles)
        results = await _scrape_instagram(
            keyword=ig_keyword,
            creator_handles=ig_handles,
            max_results=_FETCH_LIMIT,
            skip_urls=seen_urls,
        )
        ig_candidates = _weighted_sample(results, _PER_PLATFORM)
        logger.info("Instagram returned %d fresh candidates (%d sampled).", len(results), len(ig_candidates))
    except SessionExpiredError:
        logger.warning("Instagram session expired — marking for re-auth.")
        _invalidate_instagram_session()
    except FileNotFoundError:
        logger.warning("No Instagram session — skipping platform.")
    except Exception as exc:
        logger.error("Instagram scrape failed: %s", exc, exc_info=True)

    # Persist Instagram results immediately so the user can curate without waiting for Red
    ig_count = _persist_platform_results(run_id, Platform.instagram, ig_candidates, staging_dir)
    _finish_platform_run(ig_run_id, ig_count, skipped=not ig_candidates)

    # Refresh seen_urls to include the Instagram posts we just saved
    seen_urls = _get_seen_urls()

    # --- Xiaohongshu ---
    xhs_candidates: list[PostCandidate] = []
    try:
        logger.info("Starting Xiaohongshu scrape (keyword=%r, handles=%s)", xhs_keyword, xhs_handles)
        results = await scrape_xiaohongshu(
            keyword=xhs_keyword,
            creator_handles=xhs_handles,
            max_results=_PER_PLATFORM,
            skip_urls=seen_urls,
        )
        xhs_candidates = _weighted_sample(results, _PER_PLATFORM)
        logger.info("Xiaohongshu returned %d fresh candidates (%d sampled).", len(results), len(xhs_candidates))
    except SessionExpiredError:
        logger.warning("Xiaohongshu session expired — marking for re-auth.")
        _invalidate_session("xiaohongshu")
    except FileNotFoundError:
        logger.warning("No Xiaohongshu session — skipping platform.")
    except Exception as exc:
        logger.error("Xiaohongshu scrape failed: %s", exc, exc_info=True)

    xhs_count = _persist_platform_results(run_id, Platform.xiaohongshu, xhs_candidates, staging_dir)
    _finish_platform_run(xhs_run_id, xhs_count, skipped=not xhs_candidates)

    # Mark overall run done/failed
    _finish_daily_run(run_id, mode, ig_count + xhs_count)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _weighted_sample(candidates: list[PostCandidate], k: int) -> list[PostCandidate]:
    """Return up to *k* unique candidates sampled without replacement,
    with probability proportional to log(engagement + 2).

    High-engagement posts are favoured but not guaranteed — each run draws
    a different mix. Falls back to taking all candidates if fewer than k exist.
    """
    if not candidates:
        return []
    if len(candidates) <= k:
        # Not enough to sample — shuffle so order varies across runs
        shuffled = candidates.copy()
        random.shuffle(shuffled)
        return shuffled

    weights = [math.log(max(c.engagement, 0) + 2) for c in candidates]
    selected: list[PostCandidate] = []
    pool = list(zip(weights, candidates))

    while len(selected) < k and pool:
        total = sum(w for w, _ in pool)
        r = random.uniform(0, total)
        cumulative = 0.0
        chosen_idx = 0
        for i, (w, _) in enumerate(pool):
            cumulative += w
            if cumulative >= r:
                chosen_idx = i
                break
        _, chosen = pool.pop(chosen_idx)
        selected.append(chosen)

    return selected


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


def _init_platform_run(run_id: str, platform: Platform) -> str:
    """Create a PlatformRun row for *platform* in *running* state and return its id."""
    with Session(engine) as session:
        pr = PlatformRun(run_id=run_id, platform=platform, status=PlatformStatus.running)
        session.add(pr)
        session.commit()
        session.refresh(pr)
        return pr.id


def _finish_platform_run(platform_run_id: str, post_count: int, *, skipped: bool) -> None:
    """Mark a PlatformRun as done (or skipped) and record how many posts were saved."""
    with Session(engine) as session:
        pr = session.get(PlatformRun, platform_run_id)
        if pr is None:
            return
        pr.status = PlatformStatus.skipped if skipped else PlatformStatus.done
        pr.post_count = post_count
        session.add(pr)
        session.commit()


def _persist_platform_results(
    run_id: str,
    platform: Platform,
    candidates: list[PostCandidate],
    staging_dir: Path,
) -> int:
    """Write *candidates* for one platform to DB. Returns count of new posts saved."""
    with Session(engine) as session:
        existing_urls: set[str] = set(session.exec(select(Post.source_url)).all())

        new_count = 0
        for candidate in candidates:
            if candidate.source_url in existing_urls:
                logger.debug("Skipping already-seen post: %s", candidate.source_url)
                continue

            screenshot_path: str | None = None
            if candidate.screenshot_data:
                ext = "jpg" if candidate.screenshot_data[:2] == b"\xff\xd8" else "png"
                filename = f"{platform.value}_{uuid.uuid4().hex[:8]}.{ext}"
                screenshot_file = staging_dir / filename
                try:
                    screenshot_file.write_bytes(candidate.screenshot_data)
                    screenshot_path = str(screenshot_file)
                except Exception as exc:
                    logger.warning("Failed to save screenshot %s: %s", filename, exc)

            if screenshot_path is None:
                logger.debug("Skipping post %s — no screenshot saved.", candidate.source_url)
                continue

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

        session.commit()
        logger.info("Persisted %d new %s posts for run %s.", new_count, platform.value, run_id)
        return new_count


def _finish_daily_run(run_id: str, mode: RunMode, total_posts: int) -> None:
    with Session(engine) as session:
        daily_run = session.get(DailyRun, run_id)
        if daily_run is not None:
            daily_run.mode = mode
            daily_run.status = RunStatus.done if total_posts > 0 else RunStatus.failed
            session.add(daily_run)
            session.commit()
        logger.info(
            "Daily run %s finished — %d total posts, status=%s.",
            run_id,
            total_posts,
            RunStatus.done if total_posts > 0 else RunStatus.failed,
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
