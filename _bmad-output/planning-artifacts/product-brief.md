# Product Brief: Daily Visual Inspiration Curator

## Problem
Finding visual inspiration on Instagram and Xiaohongshu requires
manual scrolling through algorithm-driven feeds. There's no way to
systematically discover content matching your aesthetic taste, and
no organized archive of what you've loved.

## Solution
A daily-run local web app that automatically browses Instagram and
Xiaohongshu as you (authenticated browser), screenshots 10 posts
matching your taste, presents them one-by-one for curation, and
saves liked shots to an organized local folder.

## Core Loop
Tool runs each morning → scrapes 10 posts (5 per platform) → shows
them one-by-one in localhost UI → user picks likes → saved to dated
folder → liked shots + creators inform tomorrow's search

## How Discovery Works
- If user enters keyword(s) today → search both platforms by keyword
- If no keyword → AI analyses recently liked images for vibe keywords
  + tracks saved creators → finds similar content
- Keywords reset each day (not carried over)

## Non-Goals
- No social features (no posting, liking, following on the platforms)
- No multi-user support
- No cloud storage — local only
- No mobile app — desktop/local web only

## Success Looks Like
- Tool runs daily automatically at configured time
- 10 relevant screenshots presented cleanly, one at a time
- Saved images organized and easy to browse by date
- Over time, vibe mode gets more accurate to your taste
