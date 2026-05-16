# mlb-edge

Picks player prop bets on Underdog Sports for MLB games. Every day Underdog posts lines like "will Civale throw more or less than 4.5 strikeouts?" This system figures out which of those are worth betting on.

See `docs/FLOW.md` for the step-by-step game day cheat sheet.

---

## Glossary

**ERA** -- Earned Run Average. How many runs a pitcher gives up per 9 innings on average. Lower is better. Under 3.00 is great. Over 4.50 is bad.

**WHIP** -- Walks + Hits Per Inning. How many batters reach base per inning. Lower is better.

**K/9** -- Strikeouts per 9 innings. How often the pitcher gets batters out on strikes.

**ELITE / mid / FADE** -- Our daily grades. ELITE = performing well this season. FADE = struggling. mid = average. We bet *with* ELITE pitchers and *against* FADE pitchers.

**Lean signal** -- The noon Discord message. It tells you whether to expect a high-scoring game (OVER) or low-scoring game (UNDER), and gives ELITE/mid/FADE grades for each pitcher and offense.

**H+R+RBI** -- Hits + Runs + RBIs combined. Underdog's main batter stat. "Will this player get a total of more than 1.5 hits, runs, and RBIs today?"

**IL** -- Injured List. Players on it can't play. Players who just came *back* from it are often rusty for a week or two -- we discount their picks.

**vs RHP / vs LHP** -- How a batter performs against right-handed vs left-handed pitchers. Most batters perform very differently depending on the pitcher's throwing arm.

**Home/away split** -- How a batter performs at home vs on the road. Some players are dramatically different in each setting.

**xAVG / xwOBA** -- Expected stats. Measures how hard a batter actually hit the ball, not whether it fell for a hit. If a batter's real average is much higher than their expected average, they've been lucky -- a correction is coming.

**Multiplier** -- The payout on a correct Underdog pick. 1.05x on a $10 entry pays $10.50 if you're right. Below 1.00x means the market disagrees with that direction and you'd get less back than you put in.

**Bullpen** -- The relief pitchers who take over after the starter exits. Relevant for game total picks -- a bad bullpen gives up more runs after the starter leaves.

**Ballpark roof** -- Open = outdoor, weather matters. Dome = indoor, no weather effect. Retractable = depends.

**2FA** -- The 6-digit code texted to your phone when logging into Underdog. Required every session.

---

## What Runs Without You

| Time | What |
|---|---|
| 9am PT | Posts to Discord: all today's games, pitcher grades, anyone coming off the injured list |
| 11pm PT | Posts open bet stats to Discord when game ends. Pre-loads tomorrow's injury data. |

---

## Key Commands

```bash
# Full game report before picking
python dive.py --game "SF Giants @ Athletics"

# Drill into one player
python player.py pitcher "Aaron Civale"
python player.py batter "Matt Chapman"
python player.py matchup "Matt Chapman" "Aaron Civale"

# Check Underdog lines
python lines_query.py --list-games
python lines_query.py --game "SF Giants @ Athletics" --type pitcher

# Settle a bet after the game
python settle.py
python settle.py --id 1 --settle W

# Profit/loss
python betlog.py summary
```

---

## Scripts

| Script | What it does |
|---|---|
| `dive.py` | Full pre-game report -- pitchers, every batter vs the opposing pitcher with splits, regression flags, injury flags |
| `cache_stats.py` | Pulls recent player stats from MLB API (last 5 starts / last 15 games, splits, expected stats) |
| `cache_news.py` | Pulls injured list status. `--roster` works the night before without Underdog lines |
| `cache_team.py` | Pulls bullpen ERA, team batting stats, ballpark info |
| `player.py` | Per-pitcher start log, per-batter game log + splits, career head-to-head |
| `settle.py` | Checks live/final box score against open bets, settles W/L |
| `betlog.py` | Bet history -- add, settle, list, profit/loss summary |
| `morning_brief.py` | Runs at 9am, posts pitcher grades and injury flags to Discord |
| `matchup.py` | Early lean read before noon Discord signal |

---

## Data Sources

- **MLB Stats API** -- free, no account. Schedules, box scores, player stats, injuries, expected stats.
- **Underdog Sports** -- where the bets live. Requires login and browser automation.
- **Discord** -- where the lean signal arrives and where alerts post.

See `docs/DATASOURCES.md` for technical details.

---

## New Machine Setup

```bash
git clone https://github.com/silkyrex/mlb-edge.git
cd mlb-edge
cp .env.example .env
# Edit .env: fill in your Discord webhook URL, Python path, and repo path
pip install -r requirements.txt
launchctl load ~/Library/LaunchAgents/com.mlb-edge.morning-brief.plist
```
