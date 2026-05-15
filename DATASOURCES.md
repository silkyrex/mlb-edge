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
