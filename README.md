# mlb-edge

MLB player prop betting system for Underdog pick'em. Scrapes lines, caches stats, tiers players by mismatch (ELITE vs FADE), runs a 3-agent debate to surface the highest-confidence picks each day.

See `docs/FLOW.md` for the full game day runbook.

---

## Daily Flow

```
9am PT    morning_brief.py auto-posts to Discord
           -- pitcher grades (ERA/FIP/WAR), IL flags, all starter stats cached
           -- top 5 matchups by edge score (includes mismatch bonus)
           -- edge_track.py: PRESS/HOLD/DROP per edge type

noon PT   Discord lean signal (OVER / UNDER / AWAY / HOME)

noon-1pm  /playwright-underdog              -- log into Underdog (2FA required)
          python prescan.py                 -- rank today's games (K-edge + mismatch bonus)
          /underdog-mlb "[game]"            -- scrape top 2-3 games → mlb.db
          python prep.py                    -- REQUIRED: cache stats/ESPN/news/team
          python prep.py --check            -- verify all columns show + before continuing
          python rank.py                    -- tier every pitcher/batter/lineup ELITE/MID/FADE
          python mismatch.py                -- find ELITE vs FADE pairings → mismatch_candidates
          python closer.py                  -- Scout+Skeptic+Closer debate → picks
                                               (auto-checks prep status; blocks if cache incomplete)
          python player.py batter "[name]"  -- GATE: validate every pick (last 15 games)
          python player.py pitcher "[name]" -- check H/A split, sample size, IP cap
          sliplog.py add --picks JSON \
            --edge-key "[edge_key]"         -- log slip; edge_key wires to edge_performance

~10pm PT  sliplog.py result --id X --result win/loss --outcomes JSON
           -- settles per-pick, fires pick_lessons.observe per pick
           -- closes edge_performance row with lesson note
           -- auto-syncs ~/second-brain/sports/context.md
```

---

## Mismatch Edge Framework

Ranks players ELITE / MID / FADE and finds matchup edges before running closer.py.

### Tier thresholds

**Pitchers** (FIP primary, ERA fallback)
- ELITE: FIP ≤ 3.50 AND K/9 ≥ 9.0
- FADE: FIP ≥ 4.50, OR ERA ≥ 4.50, OR IL return ≤ 7d, OR role mismatch (reliever starting)

**Batters** (L15 H+R+RBI primary)
- ELITE: L15 H+R+RBI ≥ 2.0 AND season_avg ≥ .275
- FADE: L15 H+R+RBI ≤ 1.0 OR season_avg ≤ .220

**Lineups** (team_game_stats)
- ELITE: team_avg ≥ .265 AND OPS ≥ .780
- FADE: team_avg ≤ .235 OR OPS ≤ .700

### Edge key priority

| Rank | Mismatch | Bet | Confidence |
|------|---------|-----|-----------|
| 1 | ELITE batter vs FADE pitcher | H+R+RBI Higher | ⭐⭐⭐ Confirmed |
| 2 | FADE pitcher (hook risk) | PO Lower | ⭐⭐ Logical |
| 3 | ELITE batter vs FADE pitcher | Hits Higher | ⭐⭐ Logical |
| 4 | ELITE pitcher vs FADE lineup | Batter Ks Higher | ⭐ Untested |
| 5 | ELITE power hitter + FADE pitcher | Total Bases Higher | ⭐ Untested |

### Edge tracker

`edge_track.py` tracks hit rate per edge_key over the last 10 settled bets:
- **PRESS** ≥ 55% hit rate
- **HOLD** 40–55%
- **DROP** < 40% (stop betting until it recovers)
- **WAIT** < 5 settled (building sample)

Runs at 9am in the morning brief. Every slip tagged with `--edge-key` feeds into this tracker automatically on settle.

---

## Key Commands

```bash
# Mismatch pipeline (run after prep.py)
python rank.py                              # tier all players for today's scraped games
python rank.py --game "ATH @ LAA"           # single game
python mismatch.py                          # find ELITE vs FADE pairings, write mismatch_candidates
python mismatch.py --dry-run               # print candidates without writing to DB
python edge_track.py                        # PRESS/HOLD/DROP per edge key (rolling 10)
python edge_track.py --all                  # lifetime totals

# Prep and analyze
python prep.py                              # cache all data for today's games (REQUIRED after scrape)
python prep.py --check                      # status table: + cached, - missing, ! failed
python closer.py                            # 3-agent debate: Scout+Skeptic (haiku) → Closer (sonnet)
python closer.py --dry-run                  # verify data brief before agents run
python closer.py --game "SF Giants @ ATH"   # single game
python closer.py --model haiku              # full budget run (all agents haiku)
python closer.py --model opus               # premium run (Closer on opus)

# Log and settle
python sliplog.py add --entry 10 --payout TBD \
  --picks '[{...}]' --edge-key "elite_batter_fade_pitcher__hrbi_higher"
python sliplog.py result --id N --result win/loss --outcomes '{"Player": actual}'
python sliplog.py list --all --detailed
python sliplog.py picks --slip-id N
python sliplog.py summary                                    # includes bankroll + tier

# Bankroll tiers (run `sliplog.py summary` before recommending entry size)
# Balance < $220  → $10 max/slip
# Balance $220–$329 → $20 max/slip
# Balance $330–$439 → $30 max/slip
# Balance $440–$549 → $40 max/slip
# Balance $550+    → $50 max/slip

# Cash movements
python sliplog.py deposit --amount 50 --notes "..."
python sliplog.py withdrawal --amount 20 --notes "..."
python sliplog.py txns

# Research
python prescan.py                           # rank today's full slate (K-edge + mismatch bonus)
python dive.py --game "SF Giants @ Athletics"
python player.py pitcher "Aaron Civale"     # last 5 starts with H/A flag
python player.py batter "Matt Chapman"      # last 15 games + splits
python player.py matchup "Chapman" "Civale"
python lines_query.py --list-games
python lines_query.py --game "SF Giants @ Athletics" --type pitcher

# Live game scout (~$1/game)
python watch.py "NYM @ ATL"
python watch.py "NYM @ ATL" --date 2026-05-20
python watch.py --game-pk 745582
python watch.py "LAD @ SF" --no-discord

# Rules and lessons
python pick_lessons.py stats
python pick_lessons.py review
python pick_lessons.py review --confirmed
```

---

## Bet Rules

- 2-3 picks per slip; picks from 2+ different teams
- Entry size is bankroll-gated — run `sliplog.py summary` before every slip
- No IL players; IL return within 7 days = FADE modifier
- Always use `--edge-key` on `sliplog.py add` — feeds the edge tracker

See `docs/FLOW.md` for full hard stops, slip construction rules, and the winning-slip process.

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

- `docs/FLOW.md` -- full game day runbook with hard stops and winning-slip process
- `docs/PLAYBOOK.md` -- decision guide: lines source rule, game selection, bankroll gate
- `docs/REFERENCE.md` -- tier system, scoring rules, pick direction table, hard stops
- `docs/GLOSSARY.md` -- ERA, FIP, WAR, WHIP, xAVG, multiplier definitions
- `docs/DATASOURCES.md` -- all APIs and data sources with endpoints
- `docs/PICK_LESSONS.md` -- auto-generated rule tracker: schema, taxonomy, CLI
- `sync_sb.py` -- auto-syncs second-brain/sports/context.md on every settle
- `CLAUDE.md` -- architecture, DB schema, hard dev rules (Claude/dev reference)
