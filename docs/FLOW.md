# MLB Edge -- Daily Flow

What runs automatically vs what you do each day.

---

## Glossary (read this first)

- **ERA** -- Earned Run Average. How many runs a pitcher gives up per 9 innings on average. Lower = better. Under 3.00 is great. Over 4.50 is bad.
- **WHIP** -- Walks + Hits Per Inning. How many batters reach base per inning. Lower = better. Under 1.10 is elite.
- **K/9** -- Strikeouts per 9 innings. How often the pitcher gets batters out without the ball being hit.
- **ERA (expected)** -- xwOBA, xAVG. These measure how hard batters actually made contact, not whether the ball fell for a hit. A pitcher with a low ERA but high xwOBA has been getting lucky -- regression is coming.
- **H+R+RBI** -- Hits + Runs + RBIs. The main combined stat Underdog uses for batters. "Will this batter total more than 1.5 hits, runs, and RBIs combined today?"
- **ELITE / mid / FADE** -- our grades. ELITE = performing well, take bets in their favor. FADE = performing poorly, bet against them. mid = average.
- **Lean** -- the daily signal that tells you which direction to bet. UNDER lean = expect low scoring, fade hitters. OVER lean = expect high scoring.
- **IL** -- Injured List. Players on the IL can't play. Players who just came *off* the IL are often rusty for a week or two.
- **vs RHP / vs LHP** -- how a batter performs against right-handed pitchers vs left-handed pitchers. Most batters have a significant difference.
- **Bullpen** -- the relief pitchers who take over after the starter exits. If the starter is FADE and exits early, a good bullpen limits damage. A bad bullpen extends it.
- **Multiplier** -- the payout on a correct pick. 1.03x means a $10 entry pays $10.30 if you're right. Higher multiplier = market agrees with you less = more value if right.
- **2FA** -- the 6-digit code texted to your phone when logging in. Required for Underdog.

---

## What Runs Without You

| Time | What happens | Where you see it |
|---|---|---|
| 9am PT | Morning brief: all today's games, pitcher grades (ELITE/mid/FADE), and anyone who just came off the injured list | Discord #sports |
| 11pm PT | Checks any open bets against final game stats, posts results to Discord. Also pre-loads tomorrow's injury data for all games | Discord #sports |

You wake up with pitcher grades already done and overnight injury data already cached.

---

## New Machine Setup

```bash
git clone https://github.com/silkyrex/mlb-edge.git
cd mlb-edge
cp .env.example .env
# Edit .env: fill in SPORTS_WEBHOOK_URL, MLB_EDGE_DIR, PYTHON_BIN
pip install -r requirements.txt
launchctl load ~/Library/LaunchAgents/com.mlb-edge.morning-brief.plist
```

---

## Game Day (the short version)

1. Discord lean arrives at noon -- copy it
2. `/playwright-underdog` → enter your phone 2FA code
3. `/underdog-mlb Giants` → scrapes lines + auto-pulls all stats and injury info
4. `/underdog-mlb-analyze "SF Giants @ Athletics" "[lean string]"` → ranked pick list
5. Log the bet, place it on Underdog
6. When game ends: `python settle.py` → see if you won → settle with one command

That's the whole session.

---

## Before Noon -- Optional Early Research

Run a full game report before the lean signal arrives. This shows you everything about the matchup so you know which games are worth focusing on:

```bash
python dive.py --game "SF Giants @ Athletics" --date 2026-05-16
```

This gives you:
- Both pitchers' stats with a note if they're trending hot or cold
- Every batter on each team vs the opposing pitcher, using the correct split (e.g. a batter who hits .320 vs right-handed pitchers vs .180 vs lefties -- which one matters depends on who's pitching today)
- Home/away splits for each batter (players often perform very differently at home vs on the road)
- A flag if any batter's actual average is way above or below their expected average (the expected number is based on how hard they hit the ball -- if actual is much higher, they've been lucky and a correction is coming)
- Any injured list flags
- Which Underdog lines look interesting based on the pitcher's stats

Check if lines are up yet:
```bash
python lines_query.py --list-games
```

Get an early lean read from the MLB Stats API (before the Discord signal):
```bash
python matchup.py
```

Drill into a specific player:
```bash
python player.py pitcher "Trevor McDonald"     # last 5 starts, strikeout trend, home vs away ERA
python player.py batter "Matt Chapman"         # last 15 games, vs lefty/righty, home/away
python player.py matchup "Matt Chapman" "Aaron Civale"   # their career history against each other
```

---

## After Discord Lean (~12pm PT)

Discord drops something like:
`UNDER LEAN, Civale ELITE ERA 2.54, Mahle FADE ERA 5.42, ATH mid, SF FADE`

This means: expect low scoring. Civale is pitching well (ELITE). Mahle is giving up runs (FADE). The Athletics offense is average (mid). The Giants offense is weak (FADE).

**Step 1 -- Log into Underdog**
```
/playwright-underdog
```
Wait for the "check your phone" message. Enter the 6-digit code. Wait for confirmation before going to Step 2.

**Step 2 -- Scrape lines + auto-cache everything**
```
/underdog-mlb Giants
```
Takes about 70 seconds. Collects all available bets from all 23 stat categories. When done, automatically runs:
- Player stats cache (last 5 starts / last 15 games for everyone in the game)
- Injury cache (confirms who's active)
- Team stats cache (bullpen ERA, offense, ballpark)

You'll see the results inline. No extra commands needed.

**Step 3 -- Analyze**
```
/underdog-mlb-analyze "SF Giants @ Athletics" "UNDER LEAN, Civale ELITE ERA 2.54, ..."
```
Reads everything from the database and scores each available bet. Outputs:
- **GREEN** (score 65+) -- take these
- **YELLOW** (score 50-64) -- proceed with caution
- **SKIP** -- pass

---

## Picking a Slip

Rules:
- Only GREEN picks
- Picks must be from different teams -- no all-same-team slip
- Max $50 for a Flex entry (3+ picks), max $20 for a Standard entry (2 picks)
- Check the HOT BATTER FLAGS section -- a batter who's been averaging 2.5 H+R+RBI/game shouldn't be faded just because their team is graded FADE

Hard stops (skip the pick no matter the score):
- Player is on the injured list -- skip, they might not play
- Player came back from injury today -- skip, first game back is unreliable
- Both Higher and Lower show no multiplier -- the market has no opinion, treat it as 50/50
- First-inning pitch count on a FADE pitcher -- volatile. A pitcher can throw a clean 3-up-3-down inning regardless of their season stats. Only take this if the opposing lineup specifically walks a lot or runs deep counts.

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

At 11pm, the system automatically checks your open bets and posts the stats to Discord. Then you run:

```bash
python settle.py              # see current stats for all open bets
python settle.py --id 1 --settle W    # mark as win
python settle.py --id 1 --settle L    # mark as loss
python betlog.py summary      # see your running profit/loss
```

---

## Player Research Commands

```bash
# Pitcher: their last N starts with strikeouts, runs allowed, innings pitched per start
# Also shows home ERA vs away ERA -- some pitchers are much better at home
python player.py pitcher "Aaron Civale"
python player.py pitcher "Aaron Civale" --starts 8

# Batter: last 15 games row by row, plus their splits vs left vs right-handed pitchers
# Also shows if their actual average is above or below their expected average (regression flag)
python player.py batter "Matt Chapman"
python player.py batter "Matt Chapman" --games 20

# Head-to-head: how this specific batter has done vs this specific pitcher over their careers
python player.py matchup "Matt Chapman" "Aaron Civale"
```

---

## Pick Direction Rules (Quick Reference)

This is how the lean signal translates into pick directions:

| Situation | What to bet | Direction |
|---|---|---|
| ELITE pitcher | Strikeouts, Pitching Outs | Higher (he'll rack up Ks, stay in longer) |
| ELITE pitcher | Hits Allowed, Runs Allowed | Lower (batters will struggle) |
| FADE pitcher | Strikeouts, Pitching Outs | Lower (batters will make contact, he'll exit early) |
| FADE pitcher | Runs Allowed, Hits Allowed | Higher (batters will get to him) |
| FADE offense batter | H+R+RBI, Hits, Total Bases | Lower (weak team, struggling batters) |
| ELITE offense batter | H+R+RBI | Higher (hot team, productive batters) |
| UNDER lean (game total) | Total Runs | Lower |
| 1st inning pitch count, FADE starter | Pitch Count | Higher -- ONLY if opposing lineup walks a lot |

**Batter split rule:** always check which hand the pitcher throws with. If a batter crushes right-handed pitchers (.320 average) but struggles vs lefties (.180), and today's pitcher is right-handed -- use the .320 number, not their overall season average.

---

## Quick Database Queries

```bash
# What games have been scraped from Underdog?
python lines_query.py --list-games

# All pitcher bet lines for a game
python lines_query.py --game "SF Giants @ Athletics" --type pitcher

# One specific player's lines
python lines_query.py --game "SF Giants @ Athletics" --player "Civale"

# Cached player stats
python cache_stats.py --game "SF Giants @ Athletics" --query

# Cached injury info
python cache_news.py --game "SF Giants @ Athletics" --query

# Your open bets
python betlog.py list

# Your profit/loss summary
python betlog.py summary
```
