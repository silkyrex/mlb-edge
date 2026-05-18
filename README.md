# mlb-edge

MLB player prop betting system for Underdog pick'em. Scrapes lines, caches stats, runs a 3-agent debate to find the highest-confidence picks each day.

See `docs/FLOW.md` for the step-by-step game day runbook.

---

## Daily Flow

```
9am PT    morning_brief.py auto-posts to Discord
           -- pitcher grades (ERA/FIP/WAR), IL flags, all starter stats cached

noon PT   Discord lean signal (OVER / UNDER / AWAY / HOME)

noon-1pm  /playwright-underdog        -- log into Underdog (2FA required)
          /underdog-mlb "[game]"       -- scrape all prop lines → mlb.db
          python prep.py               -- cache stats/ESPN/news/team for all games
          python prep.py --check       -- verify all surfaces are warm (all +)
          python closer.py             -- 3-agent debate + live Statcast → picks
          sliplog.py add --picks JSON  -- log the slip

~10pm PT  sliplog.py result --id X --result win/loss --outcomes JSON
           -- settles per-pick, auto-triggers pick_lessons.observe per pick
```

---

## Key Commands

```bash
# Prep and analyze
python prep.py                              # cache all data for today's games
python prep.py --check                      # status table: + cached, - missing
python closer.py                            # 3-agent final round critique
python closer.py --dry-run                  # verify data brief before agents run
python closer.py --game "SF Giants @ ATH"   # single game

# Log and settle
python sliplog.py add --entry 20 --payout TBD --picks '[{...}]'
python sliplog.py result --id N --result win/loss --outcomes '{"Player": actual}'
python sliplog.py list --all --detailed
python sliplog.py picks --slip-id N
python sliplog.py summary

# Research
python dive.py --game "SF Giants @ Athletics"
python player.py pitcher "Aaron Civale"
python player.py batter "Matt Chapman"
python player.py matchup "Chapman" "Civale"
python lines_query.py --list-games
python lines_query.py --game "SF Giants @ Athletics" --type pitcher

# Rules and lessons
python pick_lessons.py stats
python pick_lessons.py review
python pick_lessons.py review --confirmed
```

---

## Bet Rules

- 2-3 picks per slip; picks from 2+ different teams
- Max $50 Flex, max $20 Standard/Power
- No IL players; IL return within 7 days = penalty
- Never take first-inning pitch count Higher on a FADE pitcher
- Pitcher Ks Higher: skip if line >= L5 median Ks
- Pitcher Ks Lower: skip if L5 sample is from wrong role

---

## New Machine Setup

```bash
git clone https://github.com/silkyrex/mlb-edge.git
cd mlb-edge
cp .env.example .env
# Edit .env: SPORTS_WEBHOOK_URL, MLB_EDGE_DIR, PYTHON_BIN, OB1_MCP_URL
pip install -r requirements.txt
# Add NOTION_API_TOKEN to ~/.config/credentials/notion.env
launchctl load ~/Library/LaunchAgents/com.mlb-edge.morning-brief.plist
```

---

## Reference

- `docs/FLOW.md` -- full game day runbook with hard stops and pick direction rules
- `docs/GLOSSARY.md` -- ERA, FIP, WAR, WHIP, xAVG, multiplier, and more
- `docs/DATASOURCES.md` -- all APIs and data sources with endpoints
- `docs/PICK_LESSONS.md` -- auto-generated rule tracker: schema, taxonomy, CLI
- `CLAUDE.md` -- pipeline diagram, DB schema, hard rules (dev/Claude reference)
