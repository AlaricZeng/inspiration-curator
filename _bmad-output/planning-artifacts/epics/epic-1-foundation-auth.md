# Epic 1: Foundation & Authentication

## Goal
Project scaffolding, local environment, and one-time browser login
flow for both platforms. Nothing else works without this.

## Stories

- [ ] 1.1: Project Bootstrap
  Set up repo structure, Python backend (FastAPI + SQLModel + APScheduler),
  React frontend (Vite + TypeScript), SQLite DB init, .env config file.
  Deliverable: `uvicorn` and `npm run dev` both start without errors.

- [ ] 1.2: Database Models
  Implement SQLite models: Post, Creator, VibeKeyword, DailyRun.
  Migrations via SQLModel. Seed script for dev.
  Deliverable: DB initializes on startup, tables created.

- [ ] 1.3: Playwright Session Manager
  Build browser.py — launches Chromium, saves/loads session state
  from sessions/instagram.json and sessions/xiaohongshu.json.
  Headful mode for auth, headless for scraping.
  Deliverable: session save/load works, headless reuse confirmed.

- [ ] 1.4: First-Run Authentication Wizard
  On startup, if no session file exists → open visible browser window
  → user logs in manually → session auto-saved → wizard marks
  platform as authenticated.
  Frontend shows auth status per platform with re-auth button.
  Deliverable: user can authenticate both platforms via UI.

## Acceptance Criteria
- Both backend and frontend start without errors
- SQLite DB initializes with all tables
- User can authenticate Instagram and Xiaohongshu via the UI
- Session files persist across restarts
