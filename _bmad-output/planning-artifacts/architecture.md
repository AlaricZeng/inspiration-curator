# Architecture: Daily Visual Inspiration Curator

## Stack
- **Backend:** Python + FastAPI
- **Frontend:** React (Vite + TypeScript) — single page, minimal
- **Browser Automation:** Playwright (Python) — session persistence,
  headless/headful toggle
- **AI Vibe Analysis:** Vision LLM (GPT-4o or Claude claude-sonnet-4-6) —
  converts liked images into aesthetic keywords
- **Scheduler:** APScheduler (in-process, runs inside FastAPI)
- **Database:** SQLite (via SQLModel) — metadata, liked history,
  creator list, taste profile
- **Image Storage:** Local filesystem ~/inspiration/YYYY-MM-DD/

## Why Python over Node
- Vision LLM + Playwright both have excellent Python support
- PIL/Pillow for image processing
- APScheduler is mature and simple
- FastAPI is fast enough for a local-only app

## Project Structure
```
inspiration-curator/
├── backend/
│   ├── main.py                  # FastAPI entry point
│   ├── scheduler.py             # APScheduler + daily job
│   ├── scraper/
│   │   ├── browser.py           # Playwright session manager
│   │   ├── instagram.py         # Instagram scrape logic
│   │   └── xiaohongshu.py       # Xiaohongshu scrape logic
│   ├── ai/
│   │   └── vibe_engine.py       # Vision LLM → keyword extraction
│   │                            # + taste profile aggregation
│   ├── curator/
│   │   └── storage.py           # Save liked shots, organize folders
│   └── db/
│       └── models.py            # SQLite models
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Today.tsx        # Keyword input + curation UI
│       │   ├── Gallery.tsx      # Browse past saved shots
│       │   └── TasteProfile.tsx # View/edit current vibe keywords
│       └── components/
│           └── PostCard.tsx     # Single image review card
├── sessions/                    # Playwright saved browser sessions
├── staging/                     # Raw screenshots before curation
├── .env.example                 # OPENAI_API_KEY or ANTHROPIC_API_KEY
└── docker-compose.yml           # Optional: background autostart
```

## Key Data Models

### Post
| Field        | Type     | Notes                              |
|--------------|----------|------------------------------------|
| id           | UUID     |                                    |
| platform     | enum     | instagram / xiaohongshu            |
| source_url   | str      | original post URL                  |
| creator      | str      | handle / username                  |
| screenshot   | str      | path to staging screenshot         |
| scraped_at   | datetime |                                    |
| status       | enum     | pending / liked / skipped          |
| engagement   | int      | likes + saves at scrape time       |

### Creator
| Field        | Type     | Notes                              |
|--------------|----------|------------------------------------|
| id           | UUID     |                                    |
| platform     | enum     | instagram / xiaohongshu            |
| handle       | str      |                                    |
| liked_count  | int      | how many of their posts you liked  |
| added_at     | datetime |                                    |

### VibeKeyword
| Field        | Type     | Notes                              |
|--------------|----------|------------------------------------|
| keyword      | str      | e.g. "moody", "film grain"         |
| frequency    | int      | times seen across liked images     |
| last_seen    | datetime |                                    |
| user_pinned  | bool     | manually pinned by user            |
| user_blocked | bool     | manually removed by user           |

### DailyRun
| Field        | Type     | Notes                              |
|--------------|----------|------------------------------------|
| id           | UUID     |                                    |
| date         | date     |                                    |
| keyword      | str      | null if vibe mode                  |
| mode         | enum     | keyword / vibe                     |
| status       | enum     | pending / running / done / failed  |

## Daily Scrape Flow

```
APScheduler fires at configured time (default 8:00 AM)
  → Check if user entered keyword for today
  │
  ├── KEYWORD MODE
  │   → Search Instagram + Red by keyword (5 each)
  │   → Filter by engagement threshold
  │
  └── VIBE MODE
      → Load top 3 VibeKeywords from taste profile
      → Load saved creators list
      → Scrape creators' latest posts first
      → Search platforms by vibe keywords
      → Filter candidates by engagement threshold
      → Rank: creator posts first → then by engagement
      → Pick top 5 per platform

  → Screenshot each post → save to staging/
  → Store metadata in DB
  → Mark DailyRun done → UI updates
```

## AI Vibe Engine

Triggered every time user likes a post.

1. Send screenshot to vision LLM with prompt:
   "Describe the visual style, mood, color palette, and composition
    of this image in 5–8 short keywords. Focus on aesthetic vibe,
    not content. Examples: moody, film grain, muted tones, golden
    hour, minimalist, high contrast, pastel, cinematic."

2. Parse returned keywords → upsert into VibeKeyword table
   (increment frequency if exists, insert if new)

3. Save creator handle → upsert into Creator table

4. Taste profile = top 10 VibeKeywords by frequency
   (excluding user_blocked, always including user_pinned)

First run (no liked history):
  → Show style preset picker: Street Photography / Architecture /
    Portrait / Nature / Minimal Design / Film / Fashion
  → Selected preset seeds initial VibeKeywords
  → Bypassed once 3+ real liked images exist

## Taste Profile UI
- /taste-profile page: editable tag cloud sorted by frequency
- Pin (always used), Block (never used), manual add
- Tracked creators list with remove button

## Browser Session Management
- Sessions saved to sessions/instagram.json + sessions/xiaohongshu.json
- First run: visible browser → user logs in → state auto-saved
- Daily runs: fully headless
- If scrape hits login wall → mark session expired →
  show re-auth alert in UI → user clicks re-auth button

## API Surface

| Method | Endpoint                  | Purpose                        |
|--------|---------------------------|--------------------------------|
| GET    | /api/today                | Today's run status + posts     |
| POST   | /api/today/keyword        | Set today's keyword            |
| POST   | /api/posts/{id}/like      | Like → save + trigger AI vibe  |
| POST   | /api/posts/{id}/skip      | Skip post                      |
| GET    | /api/gallery              | Past saved sessions            |
| GET    | /api/taste                | Current taste profile          |
| PATCH  | /api/taste/keywords       | Pin / block / add keywords     |
| GET    | /api/creators             | Tracked creators list          |
| DELETE | /api/creators/{id}        | Remove a creator               |
| POST   | /api/run/now              | Trigger manual scrape          |
| POST   | /api/schedule             | Set daily run time             |
| POST   | /api/auth/{platform}      | Trigger re-authentication      |

## Deployment (local)

```bash
# Install
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # add OPENAI_API_KEY or ANTHROPIC_API_KEY

# Run
uvicorn backend.main:app --reload   # :8000
npm run dev --prefix frontend        # :3000
```

Optional: docker-compose for background autostart on login.

## Key Decisions & Trade-offs

- **Vision LLM over CLIP:** LLM understands vibe, mood, style the
  way a human does. Transparent keywords user can read and edit.
  Small API cost (~$0.001/image liked).
- **Creator tracking compounds over time:** The longer you use it,
  the better it gets — your creator list becomes a curated feed.
- **Engagement filter:** Quality floor without manual filtering.
  Platform-specific thresholds tunable in config.
- **SQLite:** Zero infra, fast for personal use, easy to back up.
- **No platform API keys:** Authenticated browser = no developer
  accounts needed. Trade-off: selectors may break when platforms
  update their UI — expect occasional maintenance.
