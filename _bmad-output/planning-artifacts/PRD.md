# PRD: Daily Visual Inspiration Curator

## Overview
A locally-hosted web app that runs on your machine, automatically
scrapes Instagram and Xiaohongshu each morning using an authenticated
browser, screenshots 10 posts matching your taste, and presents them
one-by-one for curation. Liked shots are saved to an organized local
folder and used to drive tomorrow's discovery.

## Users
- **Primary:** Yujun — solo personal use, visual researcher/creative

## MVP Features

### 1. Daily Keyword Input
- Home page shows a text input field for today's keyword(s)
- Keyword is optional — if empty by scrape time, system uses vibe mode
- Keyword resets each day (not persisted to next day)
- Input available any time before the scheduled daily run

### 2. Scheduled Daily Scrape
- Runs automatically at a user-configured time each morning
  (default: 8:00 AM)
- Scrapes both Instagram and Xiaohongshu (5 posts each = 10 total)
- Uses Playwright with your saved browser session
  (you log in once, session is reused)
- Screenshots each post (photo or video thumbnail)
- Stores raw screenshots in a staging/ folder

### 3. Discovery Mode
- **Keyword mode:** if keyword provided, search both platforms by that keyword/hashtag
- **Vibe mode:** if no keyword, use AI-generated aesthetic keywords from
  recently liked images + pull latest posts from saved creators

### 4. One-by-One Curation UI
- After scrape completes, app shows posts one at a time
- Each post: full-size screenshot, platform badge, creator handle,
  engagement count, ✅ Like / ❌ Skip buttons
- Keyboard shortcuts: → or L to like, ← or S to skip
- Progress indicator: "4 / 10"

### 5. Organized Save & Archive
- Liked screenshots saved to: ~/inspiration/YYYY-MM-DD/
- Filename includes platform + sequence: instagram_01.png, red_02.png
- Skipped shots are discarded
- Gallery view: browse past days' saved shots within the app

### 6. Taste Engine
- On every like: send image to vision LLM → extract aesthetic keywords
- Keywords aggregated into a taste profile (tag cloud)
- Creator handles saved and tracked
- User can pin/block keywords and remove creators via Taste Profile page
- First-run: style preset picker seeds initial keywords

### 7. Session Management (one-time setup)
- First-run wizard: open browser windows for both platforms,
  user logs in manually, session cookies saved
- Sessions persist — user does not log in again unless session expires
- Re-auth button available in UI if session expires

## Non-Goals (MVP)
- No AI keyword suggestions (user types manually for keyword mode)
- No cloud sync or backup
- No video playback — video posts screenshotted as thumbnail only
- No posting, liking, or any interaction with the platforms
- No mobile support
- No multi-platform beyond Instagram + Xiaohongshu

## Success Metrics
- Tool runs every morning without manual intervention
- 10 screenshots presented cleanly with no broken images
- Curation takes under 2 minutes
- Saved folder is browsable and organized by date
- Vibe mode improves noticeably after 1 week of use

## Constraints
- macOS desktop (primary), Linux nice-to-have
- Must use authenticated browser session (no platform API keys required)
- Requires OpenAI or Anthropic API key for vibe analysis (~$0.001/like)
- All data stored locally — no external services except platforms + LLM
- Open source, publishable to GitHub
