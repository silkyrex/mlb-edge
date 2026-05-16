# Data Sources

## MLB Stats API (active)

The official MLB data source. Free, no account needed.

- Base URL: `https://statsapi.mlb.com/api/v1`
- Community docs: https://github.com/toddrob99/MLB-StatsAPI

What we use it for:
- Daily game schedules (who's playing, when, where)
- Box scores (final stats for each player after a game ends)
- Player stats: recent starts for pitchers, recent games for batters, splits (vs lefties/righties, home/away)
- Expected stats (xAVG, xwOBA) -- how hard batters actually hit the ball, regardless of whether it fell for a hit
- Injury transactions -- who got placed on the injured list, who came back, and when
- Team rosters -- who's on the active roster right now
- Bullpen vs starter ERA splits -- whether the starting rotation or relief pitchers are performing better
- Ballpark info -- outdoor vs dome, field dimensions

---

## Underdog Sports (active)

Where the bets live. Requires a login.

Site: https://app.underdogsports.com/pick-em

The system logs in using browser automation (Playwright) and scrapes all available bets for a specific game. About 170 bets per game across 23 categories:

**Pitcher bets:** Strikeouts, Pitching Outs (how many batters he gets out total), Hits Allowed, Runs Allowed, Walks, Fantasy Points, and first-inning versions of most of these.

**Batter bets:** Hits + Runs + RBIs combined, Home Runs, Total Bases (singles=1, doubles=2, triples=3, HRs=4), Hits, Runs, RBIs, Singles, Strikeouts, Walks, Stolen Bases, Doubles.

**Game-level bets:** Moneyline (who wins), Spread (win by how much), Total Runs (game total over/under).

Each bet also shows a multiplier -- if a pick says 1.05x Higher, a $10 entry pays $10.50 if you're right. If it says 0.88x, you'd only get $8.80 back. The multiplier tells you what the market thinks -- below 1.00x means the market disagrees with that direction.

**Navigation note:** The system navigates to the general pick-em page first, then clicks to the MLB section. Going directly to the MLB URL triggers a location check that blocks the page.

---

## Discord (active)

Two uses:
1. **Lean signal** -- arrives daily at noon PT. Grades each pitcher and offense for the day's games.
2. **Alerts** -- the 9am morning brief and 11pm bet status updates post here.

Webhook URL is stored in `.env` (not in the repo -- it's private).

---

## Baseball Reference (not yet active)

Site: https://www.baseball-reference.com

Has rich historical data and advanced stats not available in the free MLB API. Would require scraping (no official API), which is fragile. Only worth adding if we need multi-year historical data for backtesting.

---

## Paid Services (not yet active)

- **The Odds API** -- betting lines from multiple sportsbooks, historical odds. Free tier is 500 requests/month. Would let us compare Underdog lines to the broader market.
- **Sportradar** -- enterprise MLB data feed. Expensive. Not needed at current scale.
- **DraftKings / FanDuel** -- unofficial and fragile. Not recommended.
