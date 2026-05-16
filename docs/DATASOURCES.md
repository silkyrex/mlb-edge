# Data Sources

## Active: MLB Stats API

- Base URL: `https://statsapi.mlb.com/api/v1`
- Free, no API key required
- Rate-limit friendly for daily ingestion
- Covers: schedules, boxscores, player stats, game logs, rosters
- Docs: https://github.com/toddrob99/MLB-StatsAPI (community wrapper + endpoint reference)

Key endpoints used:
- `/schedule` -- game list by date
- `/game/{game_pk}/boxscore` -- full player stats per game

## To Explore: Baseball Reference

- Site: https://www.baseball-reference.com
- Rich historical data, advanced stats (WAR, FIP, wOBA, etc.)
- No official API -- requires scraping (brittle, slow)
- Best use case: historical backfill, advanced metrics not in MLB API

## To Explore: Paid Services

- **The Odds API** (https://the-odds-api.com) -- betting lines, props, historical odds; free tier ~500 req/mo
- **Sportradar** -- enterprise-grade, full MLB data + lines; expensive
- **DraftKings / FanDuel APIs** -- unofficial, fragile; not recommended

When to add: after the DB has enough game history to run meaningful line comparisons.

## Active: Underdog Sports Pick'em Lines

- Site: https://app.underdogsports.com/pick-em
- Requires login (Playwright MCP via Claude skill `/playwright-underdog`)
- Scraping: `/underdog-mlb [game]` Claude skill iterates all 23 stat tabs and writes to `mlb_game_lines`
- Analysis: `/underdog-mlb-analyze [game] [lean]` Claude skill reads DB + runs external research

### What it covers (23 stat tabs per game)
Pitcher: Strikeouts, Pitching Outs, Hits Allowed, Earned Runs Allowed, Walks Allowed, Fantasy Points, 1st Inn. Strikeouts, 1st Inn. Runs Allowed, 1st Inn. Pitch Count, 1st Inn. Batters Faced, 1st Inn. Hits Allowed

Batter: Hits + Runs + RBIs, Home Runs, Total Bases, Fantasy Points, Hits, Runs, RBIs, Singles, Batter Strikeouts, Batter Walks, Stolen Bases, Doubles

Team: Moneyline, Spread, Total Runs

### Lean Signal Source
Daily Discord at 12:00 PT via mlb-lean pipeline (format: OVER/UNDER/AWAY/HOME LEAN, pitcher grades ELITE/mid/FADE, offense grades). Cleanest game = most aligned pitcher + offense grades.

### Playwright Navigation Notes
- Navigate to `/pick-em/higher-lower/all/home` (not the sport-specific URL -- geo check blocks direct MLB URL)
- Click MLB button via JS, then click target game button
- Game URL gains `?match_id=XXXXX&match_type=Game`
- Click each stat tab button, wait 1.5s, extract `document.body.innerText` sliced between "Doubles\n" and "\nAdd picks"
- Full scrape: ~23 tabs x 3s = ~70s per game
