"""Scrape orchestrator — called by APScheduler and POST /api/run/now.

Flow
----
1. Get or create today's DailyRun record; mark it *running*.
2. Determine mode:
     - keyword mode: today's DailyRun has a keyword set (by the user via the UI)
     - discovery mode: no keyword — fetch from the most-liked creator (1-2 posts)
                       then fill remaining slots from the top 3 hashtags shared
                       across liked posts.
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

    # Determine mode: keyword (user set one) vs vibe (smart discovery)
    with Session(engine) as session:
        daily_run = session.exec(select(DailyRun).where(DailyRun.run_date == today)).first()
        mode = RunMode.keyword if (daily_run and daily_run.keyword) else RunMode.vibe
        keyword = daily_run.keyword if daily_run else None

    # Create PlatformRun records so the UI can track per-platform progress
    ig_run_id = _init_platform_run(run_id, Platform.instagram)
    xhs_run_id = _init_platform_run(run_id, Platform.xiaohongshu)

    # Pre-load already-seen URLs so scrapers can avoid them
    seen_urls = _get_seen_urls()

    # --- Instagram ---
    ig_candidates: list[PostCandidate] = []
    try:
        if mode == RunMode.keyword:
            logger.info("Starting Instagram scrape (keyword=%r)", keyword)
            results = await _scrape_instagram(
                keyword=keyword,
                creator_handles=[],
                max_results=_FETCH_LIMIT,
                skip_urls=seen_urls,
            )
            ig_candidates = _weighted_sample(results, _PER_PLATFORM)
        else:
            logger.info("Starting Instagram discovery scrape")
            ig_candidates = await _discover_instagram(seen_urls)
        logger.info("Instagram returned %d candidates.", len(ig_candidates))
    except SessionExpiredError:
        logger.warning("Instagram session expired — marking for re-auth.")
        _invalidate_instagram_session()
    except FileNotFoundError:
        logger.warning("No Instagram session — skipping platform.")
    except Exception as exc:
        logger.error("Instagram scrape failed: %s", exc, exc_info=True)

    persist_keyword = keyword if mode == RunMode.keyword else None

    # Persist Instagram results immediately so the user can curate without waiting for XHS
    ig_count = _persist_platform_results(run_id, Platform.instagram, ig_candidates, staging_dir, keyword=persist_keyword)
    _finish_platform_run(ig_run_id, ig_count, skipped=not ig_candidates)

    # Refresh seen_urls to include the Instagram posts we just saved
    seen_urls = _get_seen_urls()

    # --- Xiaohongshu ---
    xhs_candidates: list[PostCandidate] = []
    try:
        if mode == RunMode.keyword:
            logger.info("Starting Xiaohongshu scrape (keyword=%r)", keyword)
            results = await scrape_xiaohongshu(
                keywords=[keyword] if keyword else [],
                creator_handles=[],
                max_results=_FETCH_LIMIT,
                skip_urls=seen_urls,
            )
            xhs_candidates = _weighted_sample(results, _PER_PLATFORM)
        else:
            logger.info("Starting Xiaohongshu discovery scrape")
            xhs_candidates = await _discover_xhs(seen_urls)
        logger.info("Xiaohongshu returned %d candidates.", len(xhs_candidates))
    except SessionExpiredError:
        logger.warning("Xiaohongshu session expired — marking for re-auth.")
        _invalidate_session("xiaohongshu")
    except FileNotFoundError:
        logger.warning("No Xiaohongshu session — skipping platform.")
    except Exception as exc:
        logger.error("Xiaohongshu scrape failed: %s", exc, exc_info=True)

    xhs_count = _persist_platform_results(run_id, Platform.xiaohongshu, xhs_candidates, staging_dir, keyword=persist_keyword)
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


def _get_top_creator(platform: Platform) -> str | None:
    """Return the handle of the most-liked creator for the given platform, or None."""
    creators = _get_top_creators(1, platform)
    return creators[0] if creators else None


def _get_top_creators(n: int, platform: Platform) -> list[str]:
    """Return the top-n creator handles for the given platform, ranked by liked_count."""
    with Session(engine) as session:
        creators = session.exec(
            select(Creator)
            .where(Creator.platform == platform)
            .order_by(Creator.liked_count.desc())
            .limit(n)
        ).all()
        return [c.handle for c in creators]


def _get_top_tags(n: int, platform: Platform) -> list[str]:
    """Return the top-n hashtags from liked posts for a specific platform,
    ranked by how many liked posts share them."""
    with Session(engine) as session:
        liked_posts = session.exec(
            select(Post).where(Post.status == PostStatus.liked, Post.platform == platform)
        ).all()

    tag_counts: dict[str, int] = {}
    for post in liked_posts:
        if not post.tags:
            continue
        for tag in post.tags.split(","):
            tag = tag.strip().lower()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return sorted(tag_counts, key=lambda t: -tag_counts[t])[:n]



def _discovery_slots() -> tuple[int, int, int, int, int]:
    """Return (tag1, tag2, tag3, creator, random_tag) slot counts for discovery mode.

    Budget: 40% tag1 · 20% tag2 · 10% tag3 · 10% creator · 20% random tag.
    With _PER_PLATFORM=5 → (2, 1, 0, 1, 1).
    """
    tag1       = math.floor(0.40 * _PER_PLATFORM)  # 2
    tag2       = math.floor(0.20 * _PER_PLATFORM)  # 1
    tag3       = math.floor(0.10 * _PER_PLATFORM)  # 0
    creator    = math.ceil(0.10 * _PER_PLATFORM)   # 1
    random_tag = _PER_PLATFORM - tag1 - tag2 - tag3 - creator  # 1
    return tag1, tag2, tag3, creator, random_tag


async def _discover_instagram(seen_urls: set[str]) -> list[PostCandidate]:
    """Discovery-mode Instagram scrape with fixed per-source budgets.

    Slots (40/20/10/10/20 split): tag1, tag2, tag3, top creator, random tag.
    Falls back to extra creators if still short after all sources are tried.
    """
    all_creators = _get_top_creators(5, Platform.instagram)
    all_tags = _get_top_tags(10, Platform.instagram)

    if not all_creators and not all_tags:
        logger.info("IG discovery: no creators or tags yet — skipping.")
        return []

    s_tag1, s_tag2, s_tag3, s_creator, s_random = _discovery_slots()
    random_tag = random.choice(all_tags[3:]) if len(all_tags) > 3 else None

    candidates: list[PostCandidate] = []
    local_seen = set(seen_urls)

    primary: list[tuple[str, str | None, int]] = [
        ("tag",     all_tags[0]     if len(all_tags) > 0 else None, s_tag1),
        ("tag",     all_tags[1]     if len(all_tags) > 1 else None, s_tag2),
        ("tag",     all_tags[2]     if len(all_tags) > 2 else None, s_tag3),
        ("creator", all_creators[0] if all_creators       else None, s_creator),
        ("tag",     random_tag,                                       s_random),
    ]
    for kind, source, budget in primary:
        if not source or budget <= 0:
            continue
        if kind == "tag":
            results = await _scrape_instagram(
                keyword=source, creator_handles=[], max_results=_FETCH_LIMIT, skip_urls=local_seen,
            )
        else:
            results = await _scrape_instagram(
                keyword=None, creator_handles=[source], max_results=_FETCH_LIMIT, skip_urls=local_seen,
            )
        picks = _weighted_sample([c for c in results if c.source_url not in local_seen], budget)
        candidates.extend(picks)
        local_seen.update(c.source_url for c in picks)
        logger.info("IG discovery: %d/%d post(s) from %s %r", len(picks), budget, kind, source)

    # Fallback: fill any remaining slots from extra creators
    for handle in all_creators[1:]:
        if len(candidates) >= _PER_PLATFORM:
            break
        slots_left = _PER_PLATFORM - len(candidates)
        results = await _scrape_instagram(
            keyword=None, creator_handles=[handle], max_results=_FETCH_LIMIT, skip_urls=local_seen,
        )
        picks = _weighted_sample([c for c in results if c.source_url not in local_seen], slots_left)
        candidates.extend(picks)
        local_seen.update(c.source_url for c in picks)
        if picks:
            logger.info("IG fallback: %d post(s) from creator %r", len(picks), handle)

    return candidates


async def _discover_xhs(seen_urls: set[str]) -> list[PostCandidate]:
    """Discovery-mode Xiaohongshu scrape.

    Assembles a _FETCH_LIMIT-post pool with fixed percentage allocation:
      60% tag #1 · 20% tag #2 · 10% tag #3 · 10% top creator
    e.g. with _FETCH_LIMIT=50: 30 + 10 + 5 + 5 posts.

    Each source is served from its per-keyword pickle cache when possible;
    the browser only runs for cache misses.  Then _weighted_sample picks
    _PER_PLATFORM posts from the combined pool.
    """
    all_creators = _get_top_creators(3, Platform.xiaohongshu)
    all_tags = _get_top_tags(6, Platform.xiaohongshu)

    if not all_creators and not all_tags:
        logger.info("XHS discovery: no creators or tags yet — skipping.")
        return []

    pool_tag1    = round(_PER_PLATFORM * 0.40)  # 2
    pool_tag2    = round(_PER_PLATFORM * 0.20)  # 1
    pool_tag3    = round(_PER_PLATFORM * 0.10)  # 0
    pool_random  = round(_PER_PLATFORM * 0.20)  # 1
    pool_creator = _PER_PLATFORM - pool_tag1 - pool_tag2 - pool_tag3 - pool_random  # 1

    tags        = all_tags[:3]
    random_tag  = random.choice(all_tags[3:]) if len(all_tags) > 3 else None
    creator     = next((c for c in all_creators if c), None)
    creators    = [creator] if creator else []

    keywords       = tags + ([random_tag] if random_tag else [])
    keyword_limits = [pool_tag1, pool_tag2, pool_tag3] + ([pool_random] if random_tag else [])

    logger.info(
        "XHS discovery: tags=%s random_tag=%s creator=%s budget=(tag1=%d tag2=%d tag3=%d random=%d creator=%d)",
        tags, random_tag, creators, pool_tag1, pool_tag2, pool_tag3, pool_random, pool_creator,
    )

    return await scrape_xiaohongshu(
        keywords=keywords,
        creator_handles=creators,
        keyword_limits=keyword_limits,
        creator_limits=[pool_creator][: len(creators)],
        max_results=_PER_PLATFORM,
        skip_urls=seen_urls,
    )


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
    keyword: str | None = None,
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
                keyword=keyword,
                tags=",".join(candidate.tags) if candidate.tags else None,
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
