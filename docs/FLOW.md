# MLB Edge -- Daily Flow

What runs automatically vs what needs you.

---

## New Machine Setup

```bash
git clone https://github.com/silkyrex/mlb-edge.git
cd mlb-edge
cp .env.example .env
# Edit .env: set SPORTS_WEBHOOK_URL, MLB_EDGE_DIR, PYTHON_BIN
pip install -r requirements.txt
launchctl load ~/Library/LaunchAgents/com.mlb-edge.morning-brief.plist
```

The plist is in `~/Library/LaunchAgents/` -- copy it there from the repo if setting up fresh.

---

## What Runs Without You

| Time | What | Where |
|---|---|---|
| 9am PT | Morning brief -- all games, starter grades (ELITE/mid/FADE), IL returns | Discord #sports |
| 11pm PT | Ingest today's results, cache tomorrow's IL for all 15 games, post open bet stats to Discord | Background (daily.sh) |

You wake up with starter grades already done and injury data already cached for tomorrow.

---

## What You Do (the short version)

1. Check Discord at noon for the lean signal
2. `/playwright-underdog` → enter 2FA
3. `/underdog-mlb [game]` → scrape + auto-caches stats and injuries
4. `/underdog-mlb-analyze "[game]" "[lean]"` → ranked picks
5. Log the bet, place it on Underdog
6. At game end: `python settle.py` → see stats → settle W/L

That's it. Steps 3 auto-triggers the cache scripts now. No manual cache commands needed.

---

## Game Day Detail

### Before noon -- optional early read

If you want a lean preview before Discord:
```bash
python matchup.py
```

Check if Underdog lines are up yet:
```bash
python lines_query.py --list-games
```

Drill into a specific player before the session:
```bash
python player.py pitcher "McDonald"
python player.py batter "Chapman"
python player.py matchup "Chapman" "Civale"
```

### After Discord lean (~12pm PT)

Discord drops: `UNDER LEAN, Civale ELITE ERA 2.54, Mahle FADE ERA 5.42, ATH mid, SF FADE`

**Step 1 -- Log into Underdog**
```
/playwright-underdog
```
Enter 2FA. Wait for confirmation.

**Step 2 -- Scrape lines + auto-cache**
```
/underdog-mlb Giants
```
This scrapes all 23 stat tabs, saves to picks.db, then automatically runs:
- `cache_stats.py` -- player recent stats (pitchers: last 5 starts, batters: last 15 games + L/R splits)
- `cache_news.py` -- IL/injury status

You'll see the output inline. No extra commands needed.

**Step 3 -- Analyze**
```
/underdog-mlb-analyze "SF Giants @ Athletics" "UNDER LEAN, Civale ELITE ERA 2.54, ..."
```
Reads everything from DB. Outputs GREEN / YELLOW / SKIP picks ranked by score.

---

## Picking a Slip

- Only GREEN picks (score >= 65)
- Different teams -- no all-same-team slip
- Max $50 Flex, max $20 Standard per slip
- Check KEY BATTER FLAGS -- hot batters override team grade

Hard stops regardless of score:
- `IL-10 / IL-15 / IL-60` -- skip, player may not play
- `IL-return-today` -- first game back, skip
- Flat multiplier on both sides -- market has no signal, treat as 50/50
- 1st inning pitch count on FADE pitcher -- volatile, only take if opposing lineup walks a lot

---

## Logging the Bet

```bash
python betlog.py add \
  --matchup "SF @ ATH" \
  --signal "UNDER LEAN -- Civale ELITE / SF FADE" \
  --bet "Chapman Batter Ks 1.5 Higher + Civale ERA 2.5 Lower (3.48x)" \
  --line +248 \
  --stake 25
```

---

## After the Game

At 11pm, `daily.sh` posts final stats to Discord automatically. You just read them and settle:

```bash
python settle.py              # see current stats for all open bets
python settle.py --id 1 --settle W
python settle.py --id 1 --settle L
python betlog.py summary      # running P&L
```

---

## Player Research Commands

```bash
# Pitcher: last N starts with K/ER/IP per start, home vs away ERA, trend
python player.py pitcher "Aaron Civale"
python player.py pitcher "Aaron Civale" --starts 8

# Batter: last 15 games row by row, L/R splits, H+R+RBI trend
python player.py batter "Matt Chapman"
python player.py batter "Matt Chapman" --games 20

# Head-to-head: career matchup history by season
python player.py matchup "Matt Chapman" "Aaron Civale"
```

---

## Pick Rules (Quick Reference)

| Situation | Stat | Direction |
|---|---|---|
| ELITE pitcher | Strikeouts, Pitching Outs | Higher |
| ELITE pitcher | Hits Allowed, Earned Runs | Lower |
| FADE pitcher | Strikeouts, Pitching Outs | Lower |
| FADE pitcher | Earned Runs, Hits Allowed | Higher |
| FADE offense batter | H+R+RBI, Hits, Total Bases | Lower |
| ELITE offense batter | H+R+RBI | Higher |
| Game total (UNDER lean) | Total Runs | Lower |
| 1st Inn PC (FADE starter) | Pitch Count | Higher -- VOLATILE only |

L/R split rule: always check which hand the pitcher throws. If batter's split vs that handedness is significantly better than their season average, trust the split over the team grade.

---

## DB Quick Queries

```bash
python lines_query.py --list-games
python lines_query.py --game "SF Giants @ Athletics" --type pitcher
python lines_query.py --game "SF Giants @ Athletics" --player "Civale"

python cache_stats.py --game "SF Giants @ Athletics" --query
python cache_news.py --game "SF Giants @ Athletics" --query

python betlog.py list
python betlog.py summary
```
