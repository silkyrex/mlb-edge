# MLB Edge -- Daily Flow

Step-by-step for every game day. Follow in order.

---

## The Night Before

You don't need Underdog to be up yet. Just pre-cache the injury data.

```bash
# Check who's on IL, who just came back -- uses team rosters, no scrape needed
python cache_news.py --game "SF Giants @ Athletics" --date 2026-05-16 --roster
```

Flags to watch:
- `IL-10 / IL-15 / IL-60` -- player may not play. Skip their picks.
- `IL-return-today` -- first game back. Expect rust. Score -10.
- `IL-return-Nd` (N ≤ 7) -- still shaking off rust. Flag it.

---

## Game Day -- Before the Discord Lean (~10am PT)

Lines may or may not be up on Underdog yet for evening games. Check with:

```bash
python lines_query.py --list-games
```

If the game shows up -- lines are live, you can scrape now.
If not -- wait until closer to noon.

You can also get an early read on today's lean from the MLB Stats API:

```bash
python matchup.py
```

This generates the OVER/UNDER/AWAY/HOME signal from team and pitcher tiers.
Use it as a preview -- Discord signal at noon is the official one.

---

## Game Day -- After Discord Lean (~12pm PT)

Discord drops: `UNDER LEAN, Civale ELITE ERA 2.54, Mahle FADE ERA 5.42, ATH mid, SF FADE`

Copy the lean string. Then run the full pipeline:

### 1. Log into Underdog
```
/playwright-underdog
```
Enter 2FA when prompted. Wait for confirmation before continuing.

### 2. Scrape the lines (~70 seconds)
```
/underdog-mlb Giants
```
Writes all 23 stat tabs to picks.db. Confirm "Saved to picks.db mlb_game_lines."

### 3. Pre-cache player stats (~5 seconds)
```bash
python cache_stats.py --game "SF Giants @ Athletics"
```
Pulls last 5 starts (pitchers) and last 15 games (batters) from MLB Stats API.
The analyze skill reads from here -- no slow web searches per player.

### 4. Pre-cache injury status (~3 seconds)
```bash
python cache_news.py --game "SF Giants @ Athletics"
```
Checks official IL transactions again with today's date. Confirms active roster.
Skip if you already ran `--roster` the night before with today's date.

### 5. Verify what you have
```bash
python cache_stats.py --game "SF Giants @ Athletics" --query
python cache_news.py --game "SF Giants @ Athletics" --query
python lines_query.py --game "SF Giants @ Athletics" --type pitcher
```

### 6. Analyze
```
/underdog-mlb-analyze "SF Giants @ Athletics" "[lean string from Discord]"
```
Outputs GREEN / YELLOW / SKIP picks ranked by score.

---

## Picking a Slip

Rules:
- Only GREEN picks (score >= 65)
- Different teams -- no all-same-team slip
- Max $50 Flex, max $20 Standard per slip
- Always check the KEY BATTER FLAGS section -- hot batters override team grade

Red flags that kill a pick regardless of score:
- Player on IL (`IL-10 / IL-15 / IL-60`) -- skip
- `IL-return-today` -- first game back, skip or heavily discount
- Flat multiplier (no market signal) -- treat as 50/50
- xwOBA significantly below wOBA -- regression due, fade less aggressively

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

Look up final box score, then settle:

```bash
python betlog.py result [ID] W    # win
python betlog.py result [ID] L    # loss
python betlog.py summary          # running P&L
```

To check live box score mid-game:
```bash
curl -s "https://statsapi.mlb.com/api/v1/game/[GAME_PK]/boxscore" | python3 -c "
import sys, json; data=json.load(sys.stdin)
for side in ('home','away'):
    for p in data['teams'][side]['players'].values():
        name = p['person']['fullName']
        if 'Chapman' in name or 'Civale' in name:
            s = p.get('stats', {})
            print(name, s.get('batting') or s.get('pitching'))
"
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
| Game total (UNDER) | Total Runs | Lower |
| 1st Inn PC (FADE starter) | Pitch Count | Higher -- VOLATILE, only if opp lineup walks a lot |

---

## DB Quick Queries

```bash
# What games have been scraped?
python lines_query.py --list-games

# All pitcher lines for a game
python lines_query.py --game "SF Giants @ Athletics" --type pitcher

# Specific player
python lines_query.py --game "SF Giants @ Athletics" --player "Civale"

# Open bets
python betlog.py list

# P&L summary
python betlog.py summary
```
