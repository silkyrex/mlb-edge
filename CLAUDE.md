# mlb-edge (dev reference)

For the game day runbook see `docs/FLOW.md`. For project overview see `README.md`. For tier system, scoring rules, hard stops, and pick direction see `docs/REFERENCE.md`. For the decision guide (game selection, bankroll, lines source rule) see `docs/PLAYBOOK.md`.

---

## Architecture

```
9am PT  mlb_brief.py (launchd) -- pitchers + IL → Discord + mlb.db

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
| 9am M-F | `mlb_brief.py --post` | launchd | Discord brief + mlb.db pitcher cache |
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
| `cash_txns` | sliplog.py deposit / withdrawal | date, kind ('deposit'/'withdrawal'), amount, notes |

`~/sports/dfs/picks.db` -- NBA only. MLB scripts never touch it.

---

## Key Files

| File | Purpose |
|---|---|
| `prescan.py` | Pre-scrape game ranker. Pulls full slate + ESPN FIP + team stats, scores 4-factor rubric, writes `pre_scan_scores` with `reason` string per game. Default top 5. Run BEFORE Playwright opens. `--date`, `--top`. Also called by `mlb_brief.py` as subprocess fallback if today's rows are missing. |
| `prep.py` | One-command cache runner. Finds today's scraped games, runs cache_stats → cache_espn (sequential) + cache_news + cache_team (parallel). `--check` for status-only. |
| `closer.py` | 3-agent final round critique. Scout/Skeptic always haiku (executor); Closer uses `--model` (default sonnet, critic). Live Statcast + ump + lineup + days rest. `--dry-run`, `--game`, `--model haiku/opus`. |
| `dive.py` | Full pre-game report: pitchers, batter splits, regression flags, prop angles. |
| `cache_stats.py` | MLB Stats API → player_recent_stats. Idempotent. |
| `cache_espn.py` | ESPN API → espn_fip, espn_war, espn_k_bb on player_recent_stats. Run after cache_stats.py. |
| `cache_news.py` | IL status → player_news. `--roster` = night-before mode (no mlb_game_lines needed). |
| `cache_team.py` | Bullpen ERA, offense stats, venue → team_game_stats. Idempotent. |
| `cache_tomorrow.py` | Night-before IL pre-cache for all tomorrow's games. Called by daily.sh. |
| `sliplog.py` | Slip log + bankroll. `add --picks JSON` (with reason), `result --outcomes JSON` (auto-triggers pick_lessons), `add-picks` retrofit, `list --detailed`, `picks`, `summary` (includes bankroll), `deposit --amount`, `withdrawal --amount`, `txns`. OB1 + Notion on add + result. |
| `pick_lessons.py` | Auto-generated rule tracker. `observe`, `list`, `stats`, `review`, `falsify`, `resurrect`, `edit`. Graduates watching → confirmed at 3 occurrences. Pushes OB1 + insights.md on promotion. |
| `mlb_brief.py` | 9am pitcher grades + IL flags → Discord. Caches all starter stats in mlb.db. |
| `matchup.py` | Team + pitcher tier rankings. Early lean read. |
| `player.py` | Per-pitcher start log / per-batter game log + splits / head-to-head. |
| `lines_query.py` | Query mlb_game_lines by game, stat, player. |
| `notion_sync.py` | Sync slips + P&L to Notion. Auto-triggered by sliplog.py result. |
| `settle.py` | Show stats + settle open bets. `--id N --settle W/L [--payout X]`. `--payout` overrides stored payout for flex/partial wins. Pushes `type=mlb_bet_settled` to OB1, prompts for lesson on every settle. |
| `mlb_api.py` | MLB Stats API helpers. `find_game_pk`, `get_box_stats`, `get_game_status`, `get_linescore`. Team abbreviations resolved at runtime from `/api/v1/teams` (handles relocations). Import from here, not settle.py. |
| `box_stats.py` | Box score stat extraction + pick_lessons observation. `extract_player_stats`, `get_actual_from_box`, `auto_observe_picks`. |
| `ob1.py` | Shared OB1 push helper. `from ob1 import ob1_push`. All scripts use this -- never the HTTP MCP path. |
| `sync_sb.py` | Post-settle second-brain sync. Reads sliplog.db, rewrites the `## Underdog Fantasy` block in `~/second-brain/sports/context.md`. Auto-called by `sliplog.py result`. Also runnable standalone: `python sync_sb.py`. |

---

## Hard Rules

- **Never click Confirm on an Underdog bet.** Build the slip and enter the amount, then stop. Raymond confirms all bets himself.
- **Bankroll tiers — run `python sliplog.py summary` before recommending entry size:**

  | Balance     | Max entry/slip |
  |-------------|----------------|
  | < $220      | $10            |
  | $220–$329   | $20            |
  | $330–$439   | $30            |
  | $440–$549   | $40            |
  | $550+       | $50            |

  Never recommend an entry above the tier for the current balance.
- **Any auto-running job must have a failure alert before it is considered done.** Silent failure = not shipped. Discord ping, health check, or EXIT trap -- pick one. If it runs unattended and nothing tells you it failed, the build is incomplete.
- Never edit mlb.db schema without updating `schema/schema.sql` first.
- sliplog.db is append-only -- never delete or update settled rows.
- All Underdog scraping goes through Claude skills (Playwright). No Python scraping.
- Discord webhook must use `"User-Agent": "mlb-edge/1.0"` -- default Python UA gets 403.
- `prep.py` runs all 4 cache scripts -- use instead of running them individually.
- `cache_stats.py`, `cache_news.py`, `cache_team.py`, `cache_espn.py` are idempotent -- safe to re-run.
- `cache_news.py --roster` = night-before mode. Plain mode = game-day (needs mlb_game_lines).
- `daily.sh` and `run_morning_brief.sh` source `.env` automatically.
