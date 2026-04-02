# Epic 2: Daily Scraper

## Goal
Automated daily scraping of Instagram and Xiaohongshu — both keyword
mode and vibe mode — producing 10 screenshots in staging/.

## Stories

- [ ] 2.1: APScheduler Setup
  Integrate APScheduler into FastAPI. Configurable run time (default
  8:00 AM). /api/schedule endpoint to get/set time. Manual trigger
  via /api/run/now. DailyRun record created on each run.
  Deliverable: job fires on schedule, manual trigger works.

- [ ] 2.2: Instagram Scraper
  Playwright script to search Instagram by keyword or browse creator
  profiles. Scrolls feed, collects up to 10 candidate posts, extracts:
  URL, creator handle, engagement count, screenshots post.
  Handles login wall → marks session expired.
  Deliverable: returns 5 ranked screenshots + metadata.

- [ ] 2.3: Xiaohongshu Scraper
  Same as 2.2 but for 小红书. Search by keyword or creator profile.
  Extract 点赞 + 收藏 as engagement signal.
  Deliverable: returns 5 ranked screenshots + metadata.

- [ ] 2.4: Scrape Orchestrator
  Runs both scrapers in sequence. Applies discovery mode logic:
  - Keyword mode: pass today's keyword to both scrapers
  - Vibe mode: pass top 3 VibeKeywords + creator list to both
  Merges results (5 per platform = 10 total), saves to staging/,
  writes Post records to DB, marks DailyRun as done.
  Deliverable: end-to-end scrape produces 10 staged screenshots.

## Acceptance Criteria
- Daily job fires automatically at configured time
- Manual trigger via UI works
- 10 screenshots appear in staging/ after a run
- Both keyword mode and vibe mode produce results
- Session expiry is detected and surfaced to UI
