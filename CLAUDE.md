# mlb-edge (dev reference)

For the game day runbook see `docs/FLOW.md`. For project overview see `README.md`.

---

## Pipeline

```
9am PT  morning_brief.py (launchd) -- pitchers + IL → Discord + mlb.db

session start  prescan.py -- rank today's full slate before any scrape
  └── pulls MLB Stats API schedule + ESPN FIP, scores 4-factor rubric
  └── writes mlb.db [pre_scan_scores] + prints top-N games to scrape

noon PT Discord lean signal (or top-N from prescan)
  └── /playwright-underdog
         └── /underdog-mlb [game] → mlb.db [mlb_game_lines]
                └── prep.py  → runs all 4 cache scripts in parallel per game
                       ├── cache_stats.py → mlb.db [player_recent_stats]
                       ├── cache_espn.py  → mlb.db [player_recent_stats] (FIP/WAR/K/BB)
                       ├── cache_news.py  → mlb.db [player_news]
                       └── cache_team.py  → mlb.db [team_game_stats]

  └── /underdog-mlb-analyze [game] [lean] → ranked picks (optional quick read)

  └── closer.py → Scout / Skeptic / Closer agents
                  + live Statcast: xFIP, wRC+, pitch arsenal (MLB Stats API)
                  + umpire rating (UmpScorecards, one call per run)
                  + lineup card + pitcher days rest (MLB Stats API)
                  → sliplog.py add --picks (with reason strings)

~10pm  sliplog.py result --outcomes → pick_lessons.observe per pick → OB1 + Notion
```

---

## Auto-Running Jobs

| Time (PT) | Script | Trigger | Output |
|---|---|---|---|
| 9am M-F | `morning_brief.py --post` | launchd | Discord brief + mlb.db pitcher cache |
| nightly | `daily.sh` → `cache_tomorrow.py` | launchd/cron | mlb.db player_news (night-before IL) |

---

## Databases

`mlb.db` at `~/mlb-edge/mlb.db` -- MLB stats cache. All scripts use `Path(__file__).parent / "mlb.db"`.

| Table | Populated by | Key columns |
|---|---|---|
| `mlb_game_lines` | /underdog-mlb | scraped_date, game, player, player_type, stat, line, higher_mult, lower_mult |
| `player_recent_stats` | cache_stats.py + cache_espn.py | player, cache_date, mlb_player_id, season_era, season_k9, last5_ks (JSON), espn_fip, espn_war, splits, xStats |
| `player_news` | cache_news.py | news_date, player, status (active/IL-10/IL-15/IL-60/IL-return-today/IL-return-Nd) |
| `team_game_stats` | cache_team.py | cache_date, game, team_name, bullpen_era, team_avg, venue_name, venue_roof |
| `pre_scan_scores` | prescan.py | date, game, score, pitcher_edge, k_gap, venue_score, certainty, components_json |

`sliplog.db` at `./sliplog.db` -- slip log + rule tracker. Append-only on settled rows.

| Table | Populated by | Key columns |
|---|---|---|
| `slips` | sliplog.py | date, picks_count, players (JSON), entry, payout, status, profit |
| `slip_picks` | sliplog.py add --picks | slip_id, player, player_type, stat, line, side, game, actual, hit, reason |
| `pick_lessons` | pick_lessons.py observe | rule_key, hypothesis, direction, occurrences, counters, status, evidence (JSON) |

`~/sports/dfs/picks.db` -- NBA only. MLB scripts never touch it.

---

## Key Files

| File | Purpose |
|---|---|
| `prescan.py` | Pre-scrape game ranker. Pulls full slate + ESPN FIP + team stats, scores 4-factor rubric, writes `pre_scan_scores`. Run BEFORE Playwright opens to pick top-N games. `--date`, `--top`. |
| `prep.py` | One-command cache runner. Finds today's scraped games, runs cache_stats → cache_espn (sequential) + cache_news + cache_team (parallel). `--check` for status-only. |
| `closer.py` | 3-agent final round critique. Scout/Skeptic/Closer + live Statcast + ump + lineup + days rest. `--dry-run`, `--game`, `--model haiku`. |
| `dive.py` | Full pre-game report: pitchers, batter splits, regression flags, prop angles. |
| `cache_stats.py` | MLB Stats API → player_recent_stats. Idempotent. |
| `cache_espn.py` | ESPN API → espn_fip, espn_war, espn_k_bb on player_recent_stats. Run after cache_stats.py. |
| `cache_news.py` | IL status → player_news. `--roster` = night-before mode (no mlb_game_lines needed). |
| `cache_team.py` | Bullpen ERA, offense stats, venue → team_game_stats. Idempotent. |
| `cache_tomorrow.py` | Night-before IL pre-cache for all tomorrow's games. Called by daily.sh. |
| `sliplog.py` | Slip log. `add --picks JSON` (with reason), `result --outcomes JSON` (auto-triggers pick_lessons), `add-picks` retrofit, `list --detailed`, `picks`, `summary`. OB1 + Notion on add + result. |
| `pick_lessons.py` | Auto-generated rule tracker. `observe`, `list`, `stats`, `review`, `falsify`, `resurrect`, `edit`. Graduates watching → confirmed at 3 occurrences. Pushes OB1 + insights.md on promotion. |
| `morning_brief.py` | 9am pitcher grades + IL flags → Discord. Caches all starter stats in mlb.db. |
| `matchup.py` | Team + pitcher tier rankings. Early lean read. |
| `player.py` | Per-pitcher start log / per-batter game log + splits / head-to-head. |
| `lines_query.py` | Query mlb_game_lines by game, stat, player. |
| `notion_sync.py` | Sync slips + P&L to Notion. Auto-triggered by sliplog.py result. |
| `settle.py` | Show stats + settle open bets. `--id N --settle W/L`. Pushes `type=mlb_bet_settled` to OB1, then prompts for a lesson on every settle (WIN or LOSS). Lesson captures `type=bet_lesson` to OB1 + sports/insights.md. FIP flags shown for context. |
| `ob1.py` | Shared OB1 push helper. `from ob1 import ob1_push`. All scripts use this -- never the HTTP MCP path. |

---

## Tier System

- **ELITE** -- ERA ≤ ~3.00 (pitchers), top-tier offense
- **mid** -- average
- **FADE** -- ERA ≥ ~4.50 (pitchers), weak offense

Signal logic (`matchup.py`): OVER = ELITE offense vs FADE pitcher; UNDER = ELITE pitcher vs FADE offense; AWAY/HOME = ELITE away/home offense vs mid/FADE opponent.

---

## Scoring Rules (underdog-mlb-analyze)

```
base = 50
lean alignment:    +25 aligned / -25 opposed
market mult:       +10 if >1.04x / +5 if 1.00-1.04x / -5 if 0.95-1.00x / -10 if <0.90x
research confirm:  +15 confirmed rule / -15 contradicted
grade bonus:       +10 for ELITE pitcher K/PO Higher, FADE offense batter Lower, FADE pitcher ERA Higher
IL return penalty: -10 for IL-return-today or IL-return-Nd (N <= 7)
```

GREEN >= 65. YELLOW 50-64. SKIP below 50 or IL-flagged.

---

## Hard Rules

- Never edit mlb.db schema without updating `schema/schema.sql` first.
- sliplog.db is append-only -- never delete or update settled rows.
- All Underdog scraping goes through Claude skills (Playwright). No Python scraping.
- Discord webhook must use `"User-Agent": "mlb-edge/1.0"` -- default Python UA gets 403.
- `prep.py` runs all 4 cache scripts -- use instead of running them individually.
- `cache_stats.py`, `cache_news.py`, `cache_team.py`, `cache_espn.py` are idempotent -- safe to re-run.
- `cache_news.py --roster` = night-before mode. Plain mode = game-day (needs mlb_game_lines).
- `daily.sh` and `run_morning_brief.sh` source `.env` automatically.
- First-inning pitch count Higher on FADE pitchers is volatile -- only take when opposing lineup has documented high walk rates.
- Check `last15_h_r_rbi` against the line before fading any batter regardless of team grade.
- Check `x_avg` vs `season_avg` -- actual much higher than expected = regression risk.
