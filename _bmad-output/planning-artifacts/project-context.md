# Project Context: Daily Visual Inspiration Curator

## What This Is
A locally-hosted web app that scrapes Instagram and Xiaohongshu daily
using an authenticated Playwright browser, screenshots 10 posts, presents
them one-by-one for curation, and learns the user's aesthetic taste over
time using a vision LLM.

## Stack
- **Backend:** Python 3.11+, FastAPI, SQLModel, APScheduler, Playwright
- **Frontend:** React 18, Vite, TypeScript
- **Database:** SQLite (single file, local)
- **AI:** OpenAI GPT-4o or Anthropic Claude claude-sonnet-4-6 (vision) via API
- **Browser automation:** Playwright with Chromium

## Key Constraints
- Personal use only — no auth, no multi-user
- All data stays local (~/inspiration/ for saved images, SQLite for metadata)
- No platform API keys — uses authenticated browser sessions only
- Must run on macOS (Linux nice-to-have)
- Open source / GitHub-publishable — no secrets in code

## Coding Conventions
- Python: type hints everywhere, Pydantic models for API I/O
- Async FastAPI routes (async def)
- SQLModel for DB models (combines SQLAlchemy + Pydantic)
- React: functional components only, no class components
- TypeScript: strict mode, no `any`
- File naming: snake_case for Python, PascalCase for React components

## Non-Goals (do not implement)
- No cloud storage or sync
- No posting/liking/following on platforms
- No mobile UI
- No video playback (screenshot thumbnail only)
- No multi-user support

## Key Files to Know
- backend/main.py — FastAPI app, mounts all routers
- backend/scheduler.py — APScheduler daily job setup
- backend/scraper/browser.py — Playwright session manager (critical)
- backend/ai/vibe_engine.py — Vision LLM calls + keyword aggregation
- backend/db/models.py — All SQLite models
- frontend/src/pages/Today.tsx — Main curation UI
- frontend/src/pages/TasteProfile.tsx — Taste keyword management

## Planning Artifacts
- PRD: _bmad-output/planning-artifacts/PRD.md
- Architecture: _bmad-output/planning-artifacts/architecture.md
- Epics: _bmad-output/planning-artifacts/epics/

## Build Order
Epic 1 (Foundation) → Epic 2 (Scraper) → Epic 3 (UI) → Epic 4 (Taste Engine)
Do not skip ahead — each epic depends on the previous.
