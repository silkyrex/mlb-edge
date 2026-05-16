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

           Pick your 2-pick combo. Confirm different teams + $25 entry.
           Place on Underdog app.
           betlog.py add  -- log the slip

~10pm PT  settle.py --id X --settle W/L  -- after game ends
           auto-captures result + FIP context to OB1 memory
           appends row to bet-score rubric data log (builds toward calibrated rubric)
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

# Settle a bet after the game
python settle.py
python settle.py --id 1 --settle W

# Log and review bets
python betlog.py add --matchup "SF Giants @ Athletics" --signal "UNDER" --bet-on "McDonald K Higher" --line -115 --stake 25
python betlog.py list --all
python betlog.py summary
```

---

## Scripts

| Script | What it does |
|---|---|
| `morning_brief.py` | 9am auto-brief -- pitcher grades (ERA/FIP/WAR), IL flags → Discord. Also caches all starter stats to DB. |
| `cache_stats.py` | MLB Stats API → player_recent_stats (last 5 starts / last 15 games, splits, expected stats) |
| `cache_espn.py` | ESPN API → adds WAR, FIP, K/BB to pitcher rows in player_recent_stats. Run after cache_stats.py. |
| `cache_news.py` | IL status per player. `--roster` works the night before without Underdog lines. |
| `cache_team.py` | Bullpen ERA, team batting stats, ballpark info → team_game_stats |
| `cache_tomorrow.py` | Night-before IL pre-cache for all of tomorrow's games. Called by daily.sh. |
| `settle.py` | Box score lookup for open bets, W/L settlement. Auto-captures result to OB1 with FIP context. |
| `betlog.py` | Bet log -- add, result, list, summary |
| `dive.py` | Full pre-game report: pitchers, batter splits, regression flags, prop angles |
| `player.py` | Per-pitcher start log, per-batter game log + splits, head-to-head |
| `matchup.py` | Early lean read before noon Discord signal |
| `lines_query.py` | Query mlb_game_lines by game, stat, player |

---

## Databases

**`~/sports/dfs/picks.db`** -- shared sports DB.

| Table | What's in it |
|---|---|
| `mlb_game_lines` | Underdog lines scraped per game per day |
| `player_recent_stats` | Pitcher: ERA, K/9, WHIP, FIP, WAR, K/BB, last 5 starts, splits, xStats. Batter: last 15 games, L/R splits, home/away splits, xStats. |
| `player_news` | IL status per player per day |
| `team_game_stats` | Bullpen ERA, offense stats, venue info |

**`./betlog.db`** -- local MLB bet log only. Append-only. Never merge with picks.db.

---

## Bet Rules

- 2 picks per slip, exactly
- $25 per slip max
- Picks must be from 2 different teams
- Never take first-inning pitch count Higher on a FADE pitcher
- IL player = do not place

---

## Data Sources

- **MLB Stats API** -- free, no account. Schedules, box scores, player stats, injuries, expected stats.
- **ESPN API** -- unofficial JSON endpoint. Pitcher WAR, FIP, K/BB for qualified starters (75 pitchers, updated after each outing).
- **Underdog Sports** -- where bets are placed. Requires browser login and Playwright automation.
- **OB1** -- semantic memory. Every settled bet is captured with full context (lean, picks, FIP flags, result). Queryable: "which stat types win under UNDER lean."
- **Discord** -- lean signal arrives here at noon. Morning brief and settlement alerts post here.

---

## New Machine Setup

```bash
git clone https://github.com/silkyrex/mlb-edge.git
cd mlb-edge
cp .env.example .env
# Edit .env: SPORTS_WEBHOOK_URL, MLB_EDGE_DIR, PYTHON_BIN, OB1_MCP_URL
pip install -r requirements.txt
launchctl load ~/Library/LaunchAgents/com.mlb-edge.morning-brief.plist
```
