# mlb-edge

MLB betting edge system. Phase 3 active: Underdog pick'em scraping + lean-based scoring.

For the game day runbook, see `docs/FLOW.md`. For project overview, see `README.md`.
For new machine setup, copy `.env.example` → `.env` and fill in `SPORTS_WEBHOOK_URL`, `MLB_EDGE_DIR`, `PYTHON_BIN`.

---

## Pipeline

```
Discord lean signal (12:00 PT)
  └── /playwright-underdog
         └── /underdog-mlb [game] → picks.db [mlb_game_lines]
                ├── cache_stats.py → picks.db [player_recent_stats]
                ├── cache_news.py  → picks.db [player_news]
                └── /underdog-mlb-analyze [game] [lean] → ranked picks → betlog.py add
```

---

## Databases

`picks.db` at `~/sports/dfs/picks.db` -- shared sports DB (also used by NBA tools).
`betlog.db` at `./betlog.db` -- repo-local, MLB bet tracking only. Never merge these.

### picks.db tables in use

**mlb_game_lines** -- Underdog lines scraped per game per day.
Key columns: `scraped_date`, `game`, `player`, `team`, `player_type` (pitcher/batter/team), `stat`, `line`, `higher_mult`, `lower_mult`.
Unique on `(scraped_date, game, player, stat)`.

**player_recent_stats** -- Pre-cached per player per day.
Pitcher columns: `season_era`, `season_k9`, `season_whip`, `recent_era`, `last5_ks` (JSON array).
Batter columns: `last15_h_r_rbi`, `last15_hits`, `last15_ks_batter`, `last15_hr`, `last15_tb`, `season_avg`, `season_ops`.
Unique on `(player, cache_date)`.

**player_news** -- IL status per player per day.
Key columns: `status` (active / IL-10 / IL-15 / IL-60 / IL-return-today / IL-return-Nd), `note`, `source` (mlb-api / web).
Unique on `(news_date, player)`.

**betlog.db bets table** -- append-only. Never delete rows.
Key columns: `date`, `matchup`, `signal`, `bet_on`, `line`, `stake`, `result` (open/W/L), `profit`.

---

## Key Files

| File | Purpose |
|---|---|
| `cache_stats.py` | MLB Stats API → player_recent_stats. Idempotent. |
| `cache_news.py` | MLB transactions API → player_news. `--roster` flag skips mlb_game_lines dependency. |
| `lines_query.py` | Query mlb_game_lines by game, stat, player |
| `betlog.py` | Bet log -- add, result, list, summary |
| `matchup.py` | team_tiers(), pitcher_tiers(), signal() -- early lean read |
| `fetch.py` | MLB Stats API ingest (Phase 1 -- not yet run against picks.db) |
| `schema/schema.sql` | Full DB schema. Always update before editing picks.db schema. |
| `docs/FLOW.md` | Game day runbook -- step-by-step order of operations |
| `.env` | Runtime config: `SPORTS_WEBHOOK_URL`, `MLB_EDGE_DIR`, `PYTHON_BIN`. Gitignored. |
| `.env.example` | Template for new machine setup. Copy to `.env` and fill in. |
| `run_morning_brief.sh` | launchd wrapper -- sources `.env`, runs `morning_brief.py --post` |

---

## Tier System

Drives lean signal and pick scoring.

- **ELITE** -- ERA ~2.50 and under (pitchers), top-tier offense
- **mid** -- average
- **FADE** -- ERA ~4.50+ (pitchers), weak offense

Signal rules (`matchup.py signal()`):
- OVER: ELITE offense vs FADE pitcher
- UNDER: ELITE pitcher vs FADE offense
- AWAY/HOME: ELITE away/home offense + mid or FADE opponent

---

## Scoring Rules (underdog-mlb-analyze)

```
base = 50
lean alignment:    +25 aligned / -25 opposed
market mult:       +10 if >1.04x / +5 if 1.00-1.04x / -5 if 0.95-1.00x / -10 if <0.90x
research confirm:  +15 confirmed / -15 contradicted
grade bonus:       +10 for ELITE pitcher K/PO Higher, FADE offense batter Lower, FADE pitcher ERA Higher
IL return penalty: -10 for IL-return-today or IL-return-Nd (N <= 7)
```

GREEN >= 65. YELLOW 50-64. SKIP below 50 or IL-flagged.

---

## Player Research Rule

Check `last15_h_r_rbi` against the line before fading any batter, regardless of team grade.
A FADE team can have hot individuals. If `last15_h_r_rbi` is well above the Underdog line, do not fade.

Example (2026-05-15): Ramos graded FADE offense, averaging 2.07 H+R+RBI/g vs line of 1.5 -- wrong fade.

IL return rule: `IL-return-today` = first game back, unreliable, -10 to score. `IL-return-Nd` N<=7 = still shaking rust, -10.

---

## Hard Rules

- Never edit picks.db schema without updating `schema/schema.sql` first.
- betlog.db is append-only -- never delete or update settled rows.
- All Underdog scraping goes through Claude skills (Playwright). No Python scraping.
- Discord webhook must use `"User-Agent": "mlb-edge/1.0"` -- default Python UA gets 403.
- `cache_stats.py` and `cache_news.py` are idempotent -- safe to re-run same game+date.
- `cache_news.py --roster` = night-before mode (uses team rosters). Plain mode = game-day (uses mlb_game_lines).
- First-inning pitch count picks on FADE pitchers are volatile. Only take 1st Inn PC Higher when the opposing lineup has documented high walk rates or deep count tendencies. Season WHIP does not predict first-inning behavior.
- `daily.sh` and `run_morning_brief.sh` resolve their own directory and source `.env` automatically. No hardcoded paths -- update `.env` if Python or repo location changes.
