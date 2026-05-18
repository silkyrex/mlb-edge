# Game Day Runbook

See `docs/GLOSSARY.md` for term definitions. See `docs/DATASOURCES.md` for API details.

---

## What Runs Automatically

| Time (PT) | What | Where |
|---|---|---|
| 9am M-F | `morning_brief.py` -- pitcher grades, IL flags, all starter stats cached | Discord + mlb.db |
| nightly | `daily.sh` -- `cache_tomorrow.py` (night-before IL pre-cache) | mlb.db |

Everything else is manual.

---

## The Night Before

```bash
# Only needed if tomorrow's games aren't in the auto-cache yet
python cache_news.py --game "SF Giants @ Athletics" --date 2026-05-16 --roster
```

---

## Morning (before noon)

```bash
python dive.py --game "SF Giants @ Athletics"    # full pre-game picture
python matchup.py                                 # early lean read before signal
python lines_query.py --list-games               # check if lines are posted yet
```

---

## After Lean Signal Drops (~noon PT)

**Step 1 -- Log into Underdog**
```
/playwright-underdog
```
Enter the 6-digit phone code when prompted.

**Step 2 -- Scrape lines**
```
/underdog-mlb Giants
```
~70 seconds. Saves all prop lines to mlb.db.

**Step 3 -- Cache all stats (one command)**
```bash
python prep.py
```
Runs `cache_stats` → `cache_espn` → `cache_news` + `cache_team` for every scraped game in parallel. ~30-60s.

Check before running closer.py:
```bash
python prep.py --check    # all columns must show + before proceeding
```

**Step 4 -- Optional quick read**
```
/underdog-mlb-analyze "SF Giants @ Athletics" "UNDER LEAN, Civale ELITE ERA 2.54, ..."
```
GREEN / YELLOW / SKIP ranked by score. Use as a sanity check, not final word.

**Step 5 -- Final round critique**
```bash
python closer.py
```
Scout finds 6-8 angles across today's slate. Skeptic challenges each. Closer fetches live Statcast (xFIP, wRC+, pitch arsenal), umpire rating, and today's lineup card, then renders 3-5 final picks with `reason` strings. Output is a ready-to-paste `sliplog.py add --picks` command.

Single game or budget run:
```bash
python closer.py --game "Giants"    # filter to one game
python closer.py --model haiku      # faster/cheaper
```

---

## Picking a Slip

Hard stops -- skip regardless of score:
- Player is on the IL or returned from IL today
- IL return within 7 days (rust, -10 score penalty)
- Both Higher and Lower show no multiplier
- First-inning pitch count Higher on a FADE pitcher (only take if opposing lineup has documented high walk rates)
- Pitcher Ks Higher when line >= recent L5 median Ks
- Pitcher Ks Lower when L5 sample is from wrong role (relief sample, starting tonight)
- Check `last15_h_r_rbi` against the line before fading any batter regardless of team grade
- Check `x_avg` vs `season_avg` -- actual much higher than expected = regression risk

Slip construction rules:
- Picks from 2+ different teams
- Max $50 Flex, max $20 Standard/Power
- 3-pick on top of 2-pick only if `P(new leg hits | base legs hit) > 1 - (base_mult / new_mult)`

---

## Log the Slip

Paste the command from `closer.py` output, or build manually:

```bash
python sliplog.py add \
  --entry 20 --payout 77.80 --multiplier "3.89x" \
  --notes "UNDER LEAN -- Civale ELITE / SF FADE" \
  --picks '[
    {"player":"Aaron Civale","player_type":"pitcher","stat":"Strikeouts","line":5.5,"side":"Higher","game":"SF @ OAK","reason":"ELITE ERA 2.54, L5 Ks above line, UNDER lean aligns"},
    {"player":"Matt Chapman","player_type":"batter","stat":"Batter Strikeouts","line":1.5,"side":"Higher","game":"SF @ OAK","reason":"FADE lineup, high K rate"}
  ]'
```

---

## After the Game

```bash
# Verify picks before settling
python sliplog.py picks --slip-id N
python sliplog.py list --all --detailed

# Settle Underdog slip (auto-fires pick_lessons.observe per pick)
python sliplog.py result --id N --result win/loss \
  --outcomes '{"Aaron Civale": 4, "Matt Chapman": 0}'
# Notion + OB1 update automatically after result

# Settle a moneyline bet (live stats + lesson prompt)
python settle.py --id N --settle W   # win -- shows stats, prompts for lesson
python settle.py --id N --settle L   # loss -- shows FIP flags, prompts for lesson
# Every settle captures type=bet_lesson to OB1. Non-empty lessons write to sports/insights.md.

# Review totals and lessons
python sliplog.py summary                  # P&L + bankroll (matches Underdog balance)
python pick_lessons.py review
python pick_lessons.py review --confirmed
```

---

See `docs/REFERENCE.md` for the full pick direction table, tier system, and scoring rules.
