# mlb-edge

MLB betting edge system. Scrapes Underdog pick'em lines, cross-references MLB Stats API data, and scores picks against a daily lean signal (OVER/UNDER/AWAY/HOME + pitcher/offense grades).

**Phase 3 is active.** Daily workflow: Discord lean → Underdog scrape → player stat + injury cache → ranked pick list → bet log.

See `FLOW.md` for the step-by-step game day runbook.

---

## How It Works

Two inputs meet at analysis time:

1. **Lean signal** -- arrives daily at 12pm PT via Discord. Grades each pitcher (ELITE/mid/FADE) and each offense. Drives pick direction.
2. **Underdog lines** -- scraped via Playwright for the target game. 23 stat tabs per game: pitcher K/ERA/WHIP props, batter H+R+RBI/Hits/Ks props, team totals.

The analyzer reads both, applies lean logic, checks cached player stats and IL status, and ranks every pick 0-100.

---

## Schema

Six tables in `picks.db` (`~/sports/dfs/picks.db`):

| Table | What's in it |
|---|---|
| `mlb_game_lines` | Underdog pick'em lines -- one row per player/stat/date |
| `player_recent_stats` | Pre-cached stats: last 5 starts (pitchers), last 15 games (batters) |
| `player_news` | IL status and recent transactions from MLB Stats API |
| `games` | Game results from MLB Stats API (Phase 1, not yet active) |
| `players` | Player registry (Phase 1, not yet active) |
| `player_game_logs` | Per-game hitting/pitching stats (Phase 1, not yet active) |

Bet tracking is separate: `betlog.db` (repo-local), `bets` table only.

See `schema.sql` for full column definitions.

---

## Key Scripts

| Script | What it does |
|---|---|
| `cache_stats.py` | Pulls last 5/15 game averages from MLB Stats API → `player_recent_stats` |
| `cache_news.py` | Pulls IL transactions from MLB Stats API → `player_news`. Use `--roster` flag the night before (no Underdog scrape needed). |
| `lines_query.py` | Query `mlb_game_lines` by game, player, stat |
| `betlog.py` | Bet log -- add, settle, list, summary |
| `matchup.py` | Generates lean signal from team/pitcher tiers (early read before Discord) |
| `fetch.py` | Ingest MLB Stats API → games + player logs (Phase 1) |

---

## Claude Skills

| Skill | What it does |
|---|---|
| `/playwright-underdog` | Logs into Underdog via Playwright (prerequisite) |
| `/underdog-mlb [game]` | Scrapes all 23 stat tabs for a game → `mlb_game_lines` |
| `/underdog-mlb-analyze [game] [lean]` | Scores and ranks picks using DB cache + lean logic |

---

## Quick Queries

```bash
python lines_query.py --list-games
python lines_query.py --game "SF Giants @ Athletics" --type pitcher
python lines_query.py --game "SF Giants @ Athletics" --player "Civale"

python cache_stats.py --game "SF Giants @ Athletics" --query
python cache_news.py --game "SF Giants @ Athletics" --query

python betlog.py list
python betlog.py summary
```

---

## Data Sources

- **MLB Stats API** -- free, no key. Schedule, boxscores, rosters, transactions. See `DATASOURCES.md`.
- **Underdog Sports** -- pick'em lines scraped via Playwright skill. Requires login.
- **Discord lean signal** -- daily at 12pm PT from the mlb-lean pipeline.
