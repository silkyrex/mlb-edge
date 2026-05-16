# mlb-edge

This system helps pick player prop bets on Underdog Sports for MLB games.

Every day, Underdog lets you bet on whether a player will do **more or less** than a set number for a specific stat. For example: "Will Aaron Civale throw more than 4.5 strikeouts today?" You pick Higher or Lower. If you're right, you win a multiplier on your entry fee.

This system figures out which of those bets are worth taking.

---

## How It Works

Three things feed into every decision:

**1. The daily lean signal**
Every day at noon PT, a Discord message arrives grading today's game. It tells you whether the total score is expected to be high or low (OVER/UNDER), and grades each pitcher and offense as ELITE, mid, or FADE:
- **ELITE pitcher** -- throwing well lately, low ERA (runs allowed per 9 innings), hard to hit
- **FADE pitcher** -- getting hit hard, high ERA, giving up runs
- **ELITE/FADE offense** -- same idea for the hitting team as a whole

**2. The Underdog lines**
This system logs into Underdog and scrapes all the available bets for a specific game -- about 170 numbers covering pitchers, batters, and the game total. Things like "Civale strikeouts: 4.5" or "Chapman hits + runs + RBIs: 1.5."

**3. Player research**
For each player, the system pulls recent stats from the official MLB Stats API (free, no key needed). It stores:
- Last 5 starts for pitchers (how many strikeouts, runs allowed, etc.)
- Last 15 games for batters (hits, runs, RBIs per game)
- How each batter hits against left-handed vs right-handed pitchers
- How each batter hits at home vs on the road
- Expected stats (xAVG, xwOBA) -- based on how hard the ball was actually hit, not luck

These three inputs get combined into a 0-100 score for every available bet. The highest-scoring picks go on the slip.

---

## What Runs Automatically

You don't have to do everything manually. Two things run on a schedule:

- **9am every day:** A morning brief posts to Discord listing all of today's games, who's pitching, and whether they're performing well or badly this season. Any players who just came off the injured list get flagged.
- **11pm every day:** The system checks any open bets against the final box score and posts the stats to Discord. It also pre-loads injury data for tomorrow's games overnight so you wake up with that ready.

---

## The Daily Session (what you actually do)

1. Check Discord at noon for the lean signal
2. Log into Underdog (takes 30 seconds, requires a phone 2FA code)
3. Tell the system which game to scrape -- it collects all the lines and automatically pulls player stats and injury info
4. Run the analyzer -- it reads everything from the database and outputs a ranked list of picks (GREEN = take it, YELLOW = maybe, SKIP = pass)
5. Log the bet and place it on Underdog
6. When the game ends, run one command to see the result and settle the bet

---

## The Database

Everything gets stored in a local database. Six tables:

| Table | What's in it |
|---|---|
| `mlb_game_lines` | All the Underdog bet lines -- player, stat, number, and the payout multiplier |
| `player_recent_stats` | Each player's recent performance -- last 5 starts (pitchers), last 15 games (batters), vs lefty/righty splits, home/away splits, expected stats |
| `player_news` | Injured list status -- who's out, who just came back. Players who just returned from injury tend to underperform for a week or two |
| `team_game_stats` | Each team's bullpen ERA (the relief pitchers who come in after the starter), team batting stats, and the ballpark type (outdoor vs dome, dimensions) |
| `games` / `players` / `player_game_logs` | Historical game results (not yet active) |

Bet history lives separately in `betlog.db`: what you bet, how much, whether it won, your running profit/loss.

See [`schema/schema.sql`](schema/schema.sql) for the full column definitions.

---

## Key Scripts

| Script | What it does |
|---|---|
| `dive.py` | Full pre-game report -- pitchers, every batter vs the opposing pitcher, who's hot or cold, injury flags, interesting lines |
| `cache_stats.py` | Pulls recent player stats from MLB API and stores them |
| `cache_news.py` | Pulls injury/roster moves and stores who's on the injured list |
| `cache_team.py` | Pulls bullpen ERA, team batting stats, and ballpark info |
| `player.py` | Drill into one player -- their last 5 starts (pitcher), last 15 games (batter), or head-to-head history between a specific batter and pitcher |
| `settle.py` | Checks a live or finished game and shows you whether your picks won or lost |
| `betlog.py` | Tracks your bets -- add new ones, record results, see profit/loss |
| `morning_brief.py` | Runs at 9am, posts today's pitcher grades to Discord |
| `matchup.py` | Early lean read from the MLB API before the noon Discord signal |

---

## Claude Skills (the commands you type in this chat)

| Skill | What it does |
|---|---|
| `/playwright-underdog` | Logs into Underdog in a browser so the scraper can run |
| `/underdog-mlb [game]` | Scrapes all available bets for one game and saves them to the database |
| `/underdog-mlb-analyze [game] [lean]` | Scores every pick against the lean signal and outputs a ranked list |

---

## Data Sources

- **MLB Stats API** -- the official source. Free, no account needed. Has schedules, box scores, player stats, injury transactions.
- **Underdog Sports** -- where the bets live. Requires a login and browser automation to collect lines.
- **Discord** -- where the lean signal arrives and where alerts get posted.

See [`docs/DATASOURCES.md`](docs/DATASOURCES.md) for technical details.

---

## New Machine Setup

```bash
git clone https://github.com/silkyrex/mlb-edge.git
cd mlb-edge
cp .env.example .env
# Edit .env: fill in your Discord webhook URL, Python path, and repo path
pip install -r requirements.txt
```
