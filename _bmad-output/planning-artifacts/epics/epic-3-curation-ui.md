# Epic 3: Curation UI

## Goal
Local web UI to review today's 10 screenshots one-by-one, like or
skip, save liked shots to organized folder, browse past saves.

## Stories

- [ ] 3.1: Today Page — Keyword Input
  Home page shows:
  - Today's date + run status (pending / running / done)
  - Optional keyword input field (clears at midnight)
  - "Run now" button
  - Link to start curation when run is done
  Deliverable: keyword saved via POST /api/today/keyword.

- [ ] 3.2: Curation Flow — One by One
  Post-scrape: show 10 posts one at a time.
  Each card: full-size screenshot, platform badge, creator handle,
  engagement count, ✅ Like / ❌ Skip buttons.
  Keyboard shortcuts: → / L to like, ← / S to skip.
  Progress indicator: "4 / 10".
  Deliverable: full 10-post curation flow works end-to-end.

- [ ] 3.3: Save Liked Shots to Disk
  On like: copy screenshot from staging/ to
  ~/inspiration/YYYY-MM-DD/instagram_01.png (platform + sequence).
  POST /api/posts/{id}/like triggers save + AI vibe analysis (async).
  Deliverable: liked shots appear in dated folder on disk.

- [ ] 3.4: Gallery Page
  Browse past sessions by date. Grid of saved shots per day.
  Click image → full size view with metadata (platform, creator).
  Deliverable: all past liked shots browsable within the app.

## Acceptance Criteria
- Keyword input persists until midnight then resets
- All 10 posts shown one-by-one with like/skip working
- Keyboard shortcuts work
- Liked shots saved to correct ~/inspiration/YYYY-MM-DD/ folder
- Gallery shows all past days with correct images
