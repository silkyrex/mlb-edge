# mlb-edge

Picks player prop bets on Underdog Sports for MLB games. Every day Underdog posts lines like "will this pitcher throw more or less than 4.5 strikeouts?" This system figures out which of those are worth betting on.

See `docs/FLOW.md` for the step-by-step game day cheat sheet.

---

## How a Game Day Works

```
9am PT    Morning brief auto-posts to Discord
           -- pitcher grades, IL flags, FIP/WAR for all starters
           -- stats pre-cached in DB so afternoon analysis is instant

noon PT   Discord lean signal arrives (OVER / UNDER / AWAY / HOME)

noon-1pm  /playwright-underdog  -- log into Underdog (requires 2FA)
           /underdog-mlb "[game]"  -- scrapes all lines, auto-caches stats
           /underdog-mlb-analyze "[game]" "[lean]"  -- ranked picks, BLOCKED flags
           python closer.py  -- 3-agent debate (Scout/Skeptic/Closer) + live Statcast
                               outputs ready-to-paste sliplog.py command with reasons

           Pick your slip (typically 2-3 picks). Different teams + $20-25 entry.
           Place on Underdog app.
           sliplog.py add --picks JSON  -- log the slip with per-pick structure

~10pm PT  sliplog.py result --id X --result win/loss --outcomes JSON
           -- per-pick settle; auto-triggers pick_lessons.observe per pick
           -- captures slip outcome + per-pick observations to OB1 memory
```

---

## Glossary

**ERA** -- Earned Run Average. Runs a pitcher gives up per 9 innings. Under 3.00 = ELITE. Over 4.50 = FADE.

**FIP** -- Fielding Independent Pitching. What a pitcher's ERA *should* be based only on strikeouts, walks, and home runs -- things they control directly. If FIP is much higher than ERA, the pitcher has been lucky and ERA will likely rise. If FIP is lower, they've been unlucky.

**ERA LUCKY** -- FIP is 0.75+ higher than ERA. The pitcher looks better than they are. Hurts ERA-based picks, not K picks.

**ERA UNLUCKY** -- FIP is 0.75+ lower than ERA. The pitcher looks worse than they are. Strengthens K picks.

**WAR** -- Wins Above Replacement. How many wins this pitcher adds vs a replacement-level player. Negative WAR = below replacement, reinforce FADE grade.

**WHIP** -- Walks + Hits Per Inning Pitched. How many batters reach base per inning. Lower is better.

**K/9** -- Strikeouts per 9 innings.

**K/BB** -- Strikeout-to-walk ratio. Higher is better control.

**ELITE / mid / FADE** -- Daily pitcher grades. ELITE = ERA ≤ 3.00. FADE = ERA ≥ 4.50. mid = in between. We bet *with* ELITE pitchers (K Higher, ERA Lower) and *against* FADE pitchers (K Lower, ERA Higher).

**Lean signal** -- Noon Discord message. OVER = expect high-scoring game. UNDER = expect low-scoring game. AWAY/HOME = one offense is significantly better. Drives the pick direction for everything downstream.

**BLOCKED** -- A pick that fails a hard rule and can't be placed. Appears in its own section in the analyze output, never in TOP PICKS. Current hard rules: IL/scratch player, first-inning pitch count on a FADE pitcher.

**H+R+RBI** -- Hits + Runs + RBIs combined. Underdog's main batter stat.

**IL** -- Injured List. Active IL = pick is BLOCKED. Returned from IL in last 7 days = pick penalized (-10 score), may be rusty.

**vs RHP / vs LHP** -- Batter performance against right-handed vs left-handed pitchers. Checked against the opposing starter's arm.

**xAVG / xwOBA** -- Expected stats based on how hard the ball was hit. Real avg much higher than xAVG = lucky, regression risk. Real avg much lower = unlucky, could improve.

**Multiplier** -- Underdog payout. 1.05x on $10 pays $10.50. Below 1.00x means the market thinks you're wrong -- counts against the pick score.

**Bullpen** -- Relief pitchers after the starter exits. Bad bullpen = more runs allowed in later innings.

**2FA** -- 6-digit code texted when logging into Underdog. Required every browser session.

---

## What Runs Without You

| Time | What |
|---|---|
| 9am PT | Morning brief → Discord: all games, pitcher grades (ERA/FIP/WAR), IL returns. Caches full pitcher stats in DB. |
| 11pm PT | Checks open bet stats, pre-loads tomorrow's IL data. |

---

## Key Commands

```bash
# Full game report
python dive.py --game "SF Giants @ Athletics"

# Drill into one player
python player.py pitcher "Trevor McDonald"
python player.py batter "Brent Rooker"
python player.py matchup "Brent Rooker" "Trevor McDonald"

# Check what's in the DB
python lines_query.py --list-games
python lines_query.py --game "SF Giants @ Athletics" --type pitcher
python cache_stats.py --game "SF Giants @ Athletics" --query

# Final round critique -- 3-agent debate + live Statcast, outputs sliplog command
python closer.py
python closer.py --game "SF Giants @ Athletics"   # single game
python closer.py --dry-run                         # verify data brief before agents run
python closer.py --model haiku                     # budget run

# Log an Underdog pick-em slip with full per-pick structure
python sliplog.py add --entry 20 --payout 77.80 --multiplier "3.89x" \
  --picks '[{"player":"Trevor McDonald","player_type":"pitcher","stat":"Strikeouts","line":4.5,"side":"Higher","game":"SF @ OAK","reason":"ELITE ERA 2.30, L5 Ks 6,7,5,8,6 all above line"}]'

# Settle a slip with per-pick outcomes (auto-fires pick_lessons.observe per pick)
python sliplog.py result --id 1 --result loss --outcomes '{"Trevor McDonald":3}'

# Retrofit per-pick structure on a legacy slip
python sliplog.py add-picks --slip-id 1 --picks '[{...}]'

# Verify what got captured
python sliplog.py list --all --detailed   # all slips with per-pick rows indented
python sliplog.py picks --slip-id 6       # focused view for one slip

# Browse the auto-generated rule tracker
python pick_lessons.py stats                # counts + near-graduation overview
python pick_lessons.py review               # watching queue
python pick_lessons.py review --confirmed   # active rules (applied as score modifiers)
python pick_lessons.py review --graveyard   # falsified rules
```

---

## Scripts

| Script | What it does |
|---|---|
| `morning_brief.py` | 9am auto-brief -- pitcher grades (ERA/FIP/WAR), IL flags → Discord. Also caches all starter stats to DB. `--notion` posts the brief to the Notion "Game Day Briefs" page via `ntn_create` (shared from `notion_sync`). |
| `cache_stats.py` | MLB Stats API → player_recent_stats (last 5 starts / last 15 games, splits, expected stats) |
| `cache_espn.py` | ESPN API → adds WAR, FIP, K/BB to pitcher rows in player_recent_stats. Run after cache_stats.py. |
| `cache_news.py` | IL status per player. `--roster` works the night before without Underdog lines. |
| `cache_team.py` | Bullpen ERA, team batting stats, ballpark info → team_game_stats |
| `cache_tomorrow.py` | Night-before IL pre-cache for all of tomorrow's games. Called by daily.sh. |
| `closer.py` | 3-agent final round critique (Scout/Skeptic/Closer). Reads today's lines + cached stats, fetches live Statcast (xFIP, wRC+, pitch arsenal) between Skeptic and Closer, outputs ranked picks with reason strings + ready-to-paste sliplog command. `--dry-run` to verify data first, `--model haiku` for budget run. |
| `sliplog.py` | Underdog pick-em slip log -- add (with --picks JSON), result (with --outcomes JSON auto-fires pick_lessons), add-picks (retrofit), list (--detailed for per-pick view), picks (per-slip detail), summary. Auto-syncs to Notion after result. |
| `notion_sync.py` | Sync Underdog slips + P&L summary to Notion. Auto-triggered by sliplog.py result. `--slip N`, `--slips`, `--summary`. Single-bet entries from betlog.py are NOT synced (one source of truth: slips). Exports 3 public helpers: `notion_request` (raw REST for properties), `ntn_update` (replace blocks via ntn CLI), `ntn_create` (new page with markdown content via ntn CLI). |
| `pick_lessons.py` | Auto-generated rule tracker. Observe per-pick outcomes; rules graduate watching → confirmed at 3 same-direction occurrences. Confirmed rules push to OB1 + sports/insights.md. |
| `dive.py` | Full pre-game report: pitchers, batter splits, regression flags, prop angles |
| `player.py` | Per-pitcher start log, per-batter game log + splits, head-to-head |
| `matchup.py` | Early lean read before noon Discord signal |
| `lines_query.py` | Query mlb_game_lines by game, stat, player |

---

## Databases

**`~/mlb-edge/mlb.db`** -- MLB-only stats cache.

| Table | What's in it |
|---|---|
| `mlb_game_lines` | Underdog lines scraped per game per day |
| `player_recent_stats` | Pitcher: ERA, K/9, WHIP, FIP, WAR, K/BB, last 5 starts (incl. last5_ip + pitcher_role), splits, xStats. Batter: last 15 games, L/R splits, home/away splits, xStats. |
| `player_news` | IL status per player per day |
| `team_game_stats` | Bullpen ERA, offense stats, venue info |

**`./sliplog.db`** -- local slip log + rule tracker. Append-only on settled rows.

| Table | What's in it |
|---|---|
| `slips` | Underdog multi-pick slip log -- managed by `sliplog.py` |
| `slip_picks` | Per-pick structure for each slip (player, stat, line, side, game, actual, hit, reason). reason = why the pick was made. Populated by `sliplog.py add --picks` or `add-picks` retrofit. |
| `pick_lessons` | Auto-generated rule tracker. rule_key uniqueness, status (watching/confirmed/falsified/under_review), evidence (JSON). Managed by `pick_lessons.py`. |

> `~/sports/dfs/picks.db` is the NBA-only shared sports DB. **MLB scripts never touch it.**

---

## Bet Rules

- Slips are typically 2-3 picks; stacking a 3-pick on top of a 2-pick is only +EV if the marginal leg clears `P(new leg | base legs hit) > 1 − base_mult/new_mult` (see `docs/PICK_LESSONS.md`)
- $20-25 per slip max
- Picks must be from 2 different teams
- Never take first-inning pitch count Higher on a FADE pitcher
- IL player = do not place
- For pitcher Ks Higher: skip if line ≥ L5 median Ks. For pitcher Ks Lower: skip if L5 sample is from wrong role (`pitcher_role` = reliever/mixed when tonight is a start)

---

## Data Sources

- **MLB Stats API** -- free, no account. Schedules, box scores, player stats, injuries, expected stats. Also used live by `closer.py` for Statcast: xFIP, wRC+, pitch arsenal (velocity + usage).
- **ESPN API** -- unofficial JSON endpoint. Pitcher WAR, FIP, K/BB for qualified starters (75 pitchers, updated after each outing).
- **Underdog Sports** -- where bets are placed. Requires browser login and Playwright automation.
- **OB1** -- semantic memory. Every settled bet is captured with full context (lean, picks, FIP flags, result). Queryable: "which stat types win under UNDER lean."
- **Notion (MLB Edge Bet Log database)** -- visual P&L dashboard. Underdog slips only. Single-bet entries from `betlog.py` are intentionally NOT pushed here -- avoids double-counting when a multi-leg slip would otherwise be logged in both tables.
- **Discord** -- lean signal arrives here at noon. Morning brief and settlement alerts post here.

### Where each log goes

| Log | OB1 (signal memory) | Notion (P&L dashboard) | Purpose |
|---|:---:|:---:|---|
| `sliplog.py` (slips table) | yes | yes | P&L source of truth; per-pick settle drives pick_lessons |

`sliplog` is canonical for everything -- placement, settlement, P&L, and rule generation.

---

## New Machine Setup

```bash
git clone https://github.com/silkyrex/mlb-edge.git
cd mlb-edge
cp .env.example .env
# Edit .env: SPORTS_WEBHOOK_URL, MLB_EDGE_DIR, PYTHON_BIN, OB1_MCP_URL
pip install -r requirements.txt

# Install Notion CLI (for notion_sync.py block writes)
# Add NOTION_API_TOKEN to ~/.config/credentials/notion.env

launchctl load ~/Library/LaunchAgents/com.mlb-edge.morning-brief.plist
```
