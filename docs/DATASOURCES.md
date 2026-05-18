# Data Sources

## MLB Stats API (active)

Free, no account needed. Base URL: `https://statsapi.mlb.com/api/v1`

Used for:
- Game schedules, box scores, linescore
- Player stats: pitcher game logs (L5 starts), batter game logs (L15), L/R/home/away splits
- Expected stats: xAVG, xwOBA (how hard the ball was hit, regardless of result)
- Injury transactions: IL placements, returns, active roster
- Team rosters (for IL night-before mode)
- Ballpark info: roof, field dimensions
- **Statcast (live, via closer.py):** xFIP, xBA, xSLG, xwOBA, wRC+, WAR, ERA-, pitch arsenal (velocity + usage %)
- **Lineup card + HP umpire (live, via closer.py):** one `schedule?hydrate=lineups,officials` call per run

Community docs: https://github.com/toddrob99/MLB-StatsAPI

---

## ESPN API (active)

Unofficial JSON endpoint. No auth required.

Used for:
- Pitcher WAR, FIP (computed: `(13*HR + 3*BB - 2*K) / IP + 3.1`), K/BB
- Covers ~75 qualified starters; updates after each outing
- Run via `cache_espn.py` after `cache_stats.py`

---

## UmpScorecards (active)

Free API. Base URL: `https://umpscorecards.com/api/umpires`

Used for:
- HP umpire season accuracy %, error rate %, weighted score
- Fetched live in `closer.py` (one call per run, in-memory)
- Tight zone (high error rate) = fewer called strikeouts for pitchers

---

## Underdog Sports (active)

Where the bets are placed. Requires login + 2FA.

Site: `https://app.underdogsports.com/pick-em`

Scraped via Playwright browser automation (`/playwright-underdog` → `/underdog-mlb`). About 170 props per game across 23 categories:

**Pitcher:** Strikeouts, Pitching Outs, Hits Allowed, Runs Allowed, Walks, Fantasy Points, first-inning versions of most.

**Batter:** H+R+RBI, Home Runs, Total Bases, Hits, Runs, RBIs, Singles, Strikeouts, Walks, Stolen Bases, Doubles.

**Game:** Moneyline, Spread, Total Runs.

Each prop shows a multiplier. Below 1.00x = market disagrees with that side. Navigation: go to general pick-em page first, then click to MLB -- direct MLB URL triggers a location block.

---

## Discord (active)

Two uses:
1. **Lean signal** -- arrives daily ~noon PT. Pitcher and offense grades for today's games.
2. **Alerts** -- 9am morning brief and settlement notifications post here.

Webhook URL in `.env` (not in repo).

---

## OB1 Semantic Memory (active)

REST MCP at the URL in `~/.config/credentials/ob1.env`. Every settled slip is captured with full context (lean, picks, FIP flags, result). Queryable: "which stat types win under UNDER lean."

Shared helper: `from ob1 import ob1_push` (used in sliplog.py, pick_lessons.py).
