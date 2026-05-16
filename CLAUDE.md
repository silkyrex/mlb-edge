# mlb-edge

MLB betting edge system. Two parallel pipelines: (1) game signal from MLB Stats API + matchup tiers, (2) Underdog pick'em line scraping + lean-based scoring.

## Pipeline Overview

```
MLB Stats API (daily 11pm PT via daily.sh)
  └── fetch.py → picks.db [games, players, player_game_logs]
         └── matchup.py → signal() → discord_push.py → Discord #sports

Discord lean signal (12:00 PT daily)
  └── /playwright-underdog (Claude login skill)
         └── /underdog-mlb [game] (Claude scraper skill)
                └── picks.db [mlb_game_lines]
                       ├── cache_stats.py --game [game] (pre-cache player stats)
                       │      └── picks.db [player_recent_stats]
                       └── /underdog-mlb-analyze [game] [lean] (Claude analyzer)
                              └── ranked pick list → /bet-score gate → betlog.py add
```

## Databases

| DB | Location | Tables |
|---|---|---|
| `picks.db` | `~/sports/dfs/picks.db` | `games`, `players`, `player_game_logs`, `mlb_game_lines` |
| `betlog.db` | `./betlog.db` (repo-local) | `bets` |

**Do not merge these.** `picks.db` is the shared sports DB (also used by NBA DFS tools). `betlog.db` is MLB-only bet tracking.

## Key Files

| File | Purpose |
|---|---|
| `fetch.py` | Ingest MLB Stats API → picks.db games + player logs |
| `db.py` | connect() + init() for picks.db |
| `matchup.py` | team_tiers(), pitcher_tiers(), signal() -- produces OVER/UNDER/AWAY/HOME lean |
| `discord_push.py` | Post daily signals to Discord; reads SPORTS_WEBHOOK_URL from .env |
| `cache_stats.py` | Pre-cache player recent stats from MLB Stats API → picks.db player_recent_stats |
| `lines_query.py` | Query mlb_game_lines from picks.db by game, stat, player |
| `query.py` | Query player_game_logs for historical stats |
| `scout.py` | Player scouting -- recent form, splits |
| `analyze.py` | Odds/signal analysis helpers |
| `betlog.py` | MLB bet log -- add, result, list, summary |
| `daily.sh` | Cron wrapper for nightly fetch; logs to /tmp/mlb-edge-daily.log |
| `schema.sql` | Full DB schema (run via db.py init, not directly) |

## Claude Skills

These Claude skills read/write this repo's DB:

- `/playwright-underdog` -- logs into Underdog (prerequisite)
- `/underdog-mlb [game]` -- scrapes all 23 Underdog stat tabs → `mlb_game_lines`
- `/underdog-mlb-analyze [game] [lean]` -- reads DB + researches + scores picks

## Tier System (matchup.py)

Tiers drive the lean signal and the analyze skill scoring:

- **ELITE** -- elite offense or ELITE-ERA pitcher (ERA ~2.50 and under)
- **mid** -- average team or pitcher
- **FADE** -- weak offense or bad pitcher (ERA ~4.50+)

Signal rules in `matchup.py signal()`:
- OVER lean: ELITE offense vs FADE pitcher
- UNDER lean: ELITE pitcher vs FADE offense
- AWAY/HOME lean: ELITE away/home offense + mid or FADE opponent

## Running the Tools

```bash
# Nightly ingest (runs via daily.sh at 11pm PT)
python fetch.py --date 2026-05-15

# Push today's signals to Discord
python discord_push.py

# Query historical player stats
python query.py --player "Aaron Civale" --days 10

# Pre-cache player stats (run after /underdog-mlb, before /underdog-mlb-analyze)
python cache_stats.py --game "SF Giants @ Athletics"
python cache_stats.py --game "SF Giants @ Athletics" --query

# Query today's Underdog lines
python lines_query.py --game "SF Giants @ Athletics"
python lines_query.py --game "SF Giants @ Athletics" --type pitcher
python lines_query.py --list-games

# Log a bet
python betlog.py add --matchup "SF @ ATH" --signal "UNDER LEAN" --bet "under 9.5" --line -127 --stake 25

# Settle a bet
python betlog.py result 1 W

# View open bets and P&L
python betlog.py list
python betlog.py summary
```

## Environment

- `.env` in repo root: `SPORTS_WEBHOOK_URL=https://discord.com/api/webhooks/...`
- Python venv: `~/.venv/bin/python` (has requests, sqlite3)
- No other dependencies beyond `requirements.txt`

## Player Research Rule

**Always research individual player form before finalizing a pick -- offense grade alone is not enough.**
A FADE team can have hot individual players. Check last 10-15 game averages against the line.
Example (2026-05-15): Ramos graded FADE offense but averaging 2.07 H+R+RBI/game vs line of 1.5 -- wrong fade.
Look for: xwOBA significantly below wOBA (regression due), IL return rusty, hamstring limiting DH-only players.

## Rules

- Never edit `picks.db` schema without updating `schema.sql` first.
- `betlog.db` is append-only for settled bets -- never delete rows.
- `daily.sh` runs at 11pm PT -- do not re-run same date if already ingested (fetch.py uses upsert so it's safe, just wasteful).
- Discord webhook UA must be non-Python: `"User-Agent": "mlb-edge/1.0"` -- default Python UA gets 403.
- All Underdog line scraping happens through Claude skills (Playwright), not Python scripts.
