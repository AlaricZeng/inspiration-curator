# Daily Visual Inspiration Curator

A locally-hosted web app that scrapes Instagram and Xiaohongshu daily using an
authenticated Playwright browser, screenshots posts, and learns your aesthetic
taste over time using a vision LLM.

## Requirements

- Python 3.11+
- Node.js 18+

## Setup

### 1. Backend

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Edit .env — set LLM_PROVIDER and the corresponding API key
```

### 2. Frontend

```bash
cd frontend
npm install
```

## Running

Start both services (two terminals):

```bash
# Terminal 1 — backend (port 8000)
uvicorn backend.main:app --reload

# Terminal 2 — frontend (port 5173)
cd frontend && npm run dev
```

Then open http://localhost:5173 in your browser.

## First Run

On first launch, visit http://localhost:5173/setup to authenticate with Instagram
and Xiaohongshu. A visible browser window will open — log in normally, then
return to the setup page. The session is saved locally and reused for all
future scrapes.

## Configuration

| Variable          | Default    | Description                         |
|-------------------|------------|-------------------------------------|
| `LLM_PROVIDER`    | `openai`   | `openai` or `anthropic`             |
| `OPENAI_API_KEY`  | —          | Required if `LLM_PROVIDER=openai`   |
| `ANTHROPIC_API_KEY` | —        | Required if `LLM_PROVIDER=anthropic`|
| `SCRAPE_TIME`     | `08:00`    | Daily scrape time (24h, local time) |

## Project Structure

```
backend/          Python FastAPI backend
  db/models.py    SQLite models (Post, Creator, VibeKeyword, DailyRun)
  scraper/        Playwright browser + platform scrapers
  ai/             Vision LLM vibe analysis
  curator/        Image storage
  routers/        FastAPI route handlers
frontend/         React + Vite + TypeScript UI
sessions/         Playwright saved browser sessions (gitignored)
staging/          Raw screenshots before curation (gitignored)
```

## Notes

- All data stays local — no cloud storage, no platform API keys
- Sessions may expire after a few weeks — re-authenticate via /setup
- Tested on macOS; Linux should work with minor path adjustments
