# mlb-edge

MLB betting edge system. Phase 3 active: Underdog pick'em scraping + lean-based scoring.

For the game day runbook, see `docs/FLOW.md`. For project overview, see `README.md`.
For new machine setup, copy `.env.example` → `.env` and fill in `SPORTS_WEBHOOK_URL`, `MLB_EDGE_DIR`, `PYTHON_BIN`.

---

## Pipeline

```
Discord lean signal (12:00 PT)
  └── /playwright-underdog
         └── /underdog-mlb [game] → mlb.db [mlb_game_lines]
                └── prep.py  → runs all 4 cache scripts in parallel per game
                       ├── cache_stats.py → mlb.db [player_recent_stats]
                       ├── cache_espn.py  → mlb.db [player_recent_stats] (FIP/WAR/K/BB)
                       ├── cache_news.py  → mlb.db [player_news]
                       └── cache_team.py  → mlb.db [team_game_stats]

  └── /underdog-mlb-analyze [game] [lean] → ranked picks (optional quick read)

  └── closer.py → 3-agent debate (Scout/Skeptic/Closer)
                  + live Statcast (xFIP/wRC+/arsenal)
                  + umpire rating (UmpScorecards)
                  + lineup card + pitcher days rest (MLB Stats API)
                  → sliplog.py add --picks (with reason strings)
```

---

## Databases

`mlb.db` at `~/mlb-edge/mlb.db` -- MLB-only DB. All MLB scripts use `Path(__file__).parent / "mlb.db"`.
`sliplog.db` at `./sliplog.db` -- repo-local, Underdog slip tracking only (sliplog.py, notion_sync.py, pick_lessons.py, settle.py, closer.py).
`~/sports/dfs/picks.db` -- NBA tools only. MLB scripts do not touch this file.

### picks.db tables in use

**mlb_game_lines** -- Underdog lines scraped per game per day.
Key columns: `scraped_date`, `game`, `player`, `team`, `player_type` (pitcher/batter/team), `stat`, `line`, `higher_mult`, `lower_mult`.
Unique on `(scraped_date, game, player, stat)`.

**player_recent_stats** -- Pre-cached per player per day.
Pitcher columns: `season_era`, `season_k9`, `season_whip`, `recent_era`, `last5_ks` (JSON), `split_home_era`, `split_away_era`, `x_woba_against`, `x_avg_against`.
Batter columns: `last15_h_r_rbi`, `last15_hits`, `last15_ks_batter`, `last15_hr`, `last15_tb`, `season_avg`, `season_ops`, `vs_lhp_avg/ops/ab`, `vs_rhp_avg/ops/ab`, `split_home_avg/ops/ab`, `split_away_avg/ops/ab`, `x_avg`, `x_slg`, `x_woba`.
Unique on `(player, cache_date)`.

**player_news** -- IL status per player per day.
Key columns: `status` (active / IL-10 / IL-15 / IL-60 / IL-return-today / IL-return-Nd), `note`, `source`.
Unique on `(news_date, player)`.

**team_game_stats** -- Team-level stats per game per day.
Columns: `side`, `bullpen_era`, `bullpen_whip`, `bullpen_k9`, `starter_era`, `team_avg`, `team_ops`, `team_k_pct`, `venue_name`, `venue_roof`, `venue_left/center/right`.
Unique on `(cache_date, game, team_name)`.

**sliplog.db pick_lessons table** -- Auto-generated rule tracker. Managed by `pick_lessons.py`.
Key columns: `rule_key` (unique, e.g. `pitcher_strikeouts_higher_line_ge_l5_median`), `hypothesis`, `direction` (fail/hit), `occurrences`, `counters`, `status` (watching/confirmed/falsified/under_review), `evidence` (JSON).
Same-game observations dedup to 1 occurrence. Confirmed rules push to OB1 + insights.md on promotion.

**sliplog.db slips table** -- Underdog pick-em multi-pick entries. Managed by `sliplog.py`.
Key columns: `date`, `picks_count`, `players` (JSON), `boost`, `entry`, `payout`, `multiplier`, `status` (open/win/loss), `profit`.
OB1 types: `underdog_slip_placed` (on add), `underdog_slip_outcome` (on result).

**sliplog.db slip_picks table** -- Per-pick structure for Underdog slips. Managed by `sliplog.py`.
Key columns: `slip_id` (FK to slips), `player`, `player_type`, `stat`, `line`, `side`, `game`, `actual`, `hit`, `reason`.
Populated by `sliplog.py add --picks` JSON or `sliplog.py add-picks` retrofit. Settled by `sliplog.py result --outcomes` JSON,
which also auto-triggers `pick_lessons.observe()` per pick.

---

## Key Files

| File | Purpose |
|---|---|
| `dive.py` | Full pre-game report: pitchers, batter lineup splits, regression flags, prop angles |
| `cache_stats.py` | MLB Stats API → player_recent_stats (stats + L/R + home/away splits + xStats). Idempotent. |
| `cache_news.py` | MLB transactions API → player_news. `--roster` skips mlb_game_lines dependency. |
| `cache_team.py` | Bullpen ERA, offense K%, venue roof/dims → team_game_stats. Idempotent. |
| `cache_tomorrow.py` | Wraps cache_news --roster for all of tomorrow's games. Called by daily.sh. |
| `player.py` | `pitcher [name]` per-start log / `batter [name]` per-game + splits / `matchup [b] [p]` H2H |
| `prep.py` | One-command cache runner. Finds today's scraped games, runs cache_stats → cache_espn (sequential) + cache_news + cache_team (parallel) across all games. `--check` for status-only, `--game` for single game. Run after /underdog-mlb, before closer.py. |
| `closer.py` | 3-agent final round critique. Scout finds angles, Skeptic challenges, Closer fetches live Statcast (xFIP/wRC+/arsenal), umpire rating, lineup card, pitcher days rest, renders 3-5 picks with reason strings. `--dry-run`, `--game`, `--model haiku`. |
| `morning_brief.py` | 9am PT launchd -- all games + starter grades + IL returns → Discord |
| `lines_query.py` | Query mlb_game_lines by game, stat, player |
| `ob1.py` | Shared OB1 push helper. Import `from ob1 import ob1_push` in any script. Auto-loads creds from `~/.config/credentials/ob1.env`. |
| `sliplog.py` | Slip log -- add, result, list (--detailed), picks (per-slip), add-picks, summary. Pushes to OB1 on add + result. `--picks` JSON captures per-pick structure; `result --outcomes` auto-triggers pick_lessons.observe per pick. `add-picks` retrofits structure to legacy slips. |
| `pick_lessons.py` | Auto-generated rule tracker. Each settled pick → rule_key + hypothesis (deterministic classifier). Graduates `watching → confirmed` at 3 same-direction occurrences (game-deduped). Falsifies at 2 counters. Pushes to OB1 + insights.md on promotion. Subcommands: observe, list, stats, review, falsify, resurrect, edit. |
| `matchup.py` | team_tiers(), pitcher_tiers(), signal() -- early lean read |
| `fetch.py` | MLB Stats API ingest (Phase 1 -- not yet active against picks.db) |
| `schema/schema.sql` | Full DB schema. Always update before editing picks.db schema. |
| `docs/FLOW.md` | Game day runbook |
| `.env` | `SPORTS_WEBHOOK_URL`, `MLB_EDGE_DIR`, `PYTHON_BIN`. Gitignored. |
| `.env.example` | New machine template. |
| `run_morning_brief.sh` | launchd wrapper -- sources `.env`, runs morning_brief.py --post |

---

## Tier System

- **ELITE** -- ERA ≤ ~3.00 (pitchers), top-tier offense
- **mid** -- average
- **FADE** -- ERA ≥ ~4.50 (pitchers), weak offense

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

Check `last15_h_r_rbi` against the line before fading any batter regardless of team grade.
Check `x_avg` vs `season_avg` -- actual significantly above expected = regression risk; below expected = underperforming, be cautious fading.
IL return rule: IL-return-today or IL-return-Nd (N ≤ 7) = -10 to score.

---

## Hard Rules

- Never edit mlb.db schema without updating `schema/schema.sql` first.
- sliplog.db is append-only -- never delete or update settled rows.
- All Underdog scraping goes through Claude skills (Playwright). No Python scraping.
- `sliplog.py` is the single bet logger -- pushes to OB1 + Notion. Notion is the P&L dashboard.
- Discord webhook must use `"User-Agent": "mlb-edge/1.0"` -- default Python UA gets 403.
- `prep.py` runs all 4 cache scripts for today's games in one command -- use instead of running scripts individually.
- `cache_stats.py`, `cache_news.py`, `cache_team.py`, `cache_espn.py` are idempotent -- safe to re-run same game+date.
- `cache_news.py --roster` = night-before mode. Plain mode = game-day (uses mlb_game_lines).
- `daily.sh` and `run_morning_brief.sh` source `.env` automatically. Update `.env` if paths change.
- First-inning pitch count on FADE pitchers is volatile. Only take 1st Inn PC Higher when opposing lineup has documented high walk rates. Season WHIP does not predict first-inning behavior.
