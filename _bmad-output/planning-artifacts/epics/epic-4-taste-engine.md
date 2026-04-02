# Epic 4: Taste Engine

## Goal
AI-powered vibe analysis that learns your aesthetic from liked photos
and drives tomorrow's discovery. Transparent and user-editable.

## Stories

- [ ] 4.1: AI Vibe Analysis
  On every like, send screenshot to vision LLM (GPT-4o or Claude).
  Prompt: extract 5–8 aesthetic keywords (mood, color, composition).
  Parse response → upsert into VibeKeyword table (increment frequency).
  Save creator handle → upsert into Creator table.
  Deliverable: liking a photo updates VibeKeyword + Creator tables.

- [ ] 4.2: Taste Profile Page
  /taste-profile page shows:
  - Top keywords as editable tag cloud (sorted by frequency)
  - Pin button (always include in searches)
  - Block button (never include)
  - Manual add keyword input
  - Tracked creators list with remove button
  PATCH /api/taste/keywords + DELETE /api/creators/{id}.
  Deliverable: user can view and edit full taste profile.

- [ ] 4.3: First-Run Seed
  If no liked history exists (day 1):
  - Show style preset picker: Street Photography / Architecture /
    Portrait / Nature / Minimal Design / Film / Fashion
  - Selected preset seeds initial VibeKeywords
  - Bypassed once 3+ real liked images exist
  Deliverable: first-run always has something to search with.

- [ ] 4.4: Vibe Mode Search Integration
  Wire taste profile into scrape orchestrator (Story 2.4):
  - Pass top 3 non-blocked VibeKeywords as search terms
  - Pass Creator list → scrape their latest posts first
  - Merge + rank results: creator posts > engagement > recency
  Deliverable: vibe mode produces relevant results on day 2+.

## Acceptance Criteria
- Liking a photo generates and stores vibe keywords within 5 seconds
- Taste profile page reflects liked images accurately
- User can pin, block, and manually add keywords
- Vibe mode uses taste profile keywords in next scrape
- First-run preset seeds the system correctly
