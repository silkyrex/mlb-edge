# Game Day Cheat Sheet

For explanations of terms (ERA, WHIP, splits, etc.) see `README.md`.

---

## The Night Before

```bash
# Pre-load injury data for tomorrow -- no Underdog login needed
python cache_news.py --game "SF Giants @ Athletics" --date 2026-05-16 --roster
# Or let daily.sh handle it automatically via cache_tomorrow.py
```

---

## Morning (before noon)

```bash
# Full game picture -- pitchers, lineups, splits, regression flags, prop angles
python dive.py --game "SF Giants @ Athletics" --date 2026-05-16

# Early lean read (before Discord signal arrives)
python matchup.py

# Check if Underdog has tomorrow's lines up yet
python lines_query.py --list-games
```

---

## After Lean Signal Drops (~12pm PT)

Discord: `UNDER LEAN, Civale ELITE ERA 2.54, Mahle FADE ERA 5.42, ATH mid, SF FADE`

**1. Log into Underdog**
```
/playwright-underdog
```
Enter the 6-digit phone code. Wait for confirmation.

**2. Scrape lines**
```
/underdog-mlb Giants
```
Takes ~70 seconds. Saves all prop lines to mlb.db.

**3. Cache all stats (one command)**
```bash
python prep.py
```
Runs `cache_stats` → `cache_espn` → `cache_news` + `cache_team` for every scraped game in parallel. Takes ~30-60s. Verify first with `--check`:
```bash
python prep.py --check    # all + before running closer.py
```

**4. Analyze (optional quick read)**
```
/underdog-mlb-analyze "SF Giants @ Athletics" "UNDER LEAN, Civale ELITE ERA 2.54, ..."
```
Outputs GREEN / YELLOW / SKIP picks ranked by score. Use as a sanity check before closer.py.

**5. Final round critique (3-agent debate)**
```bash
python closer.py
```
Scout finds 6-8 angles. Skeptic tears each apart. Closer fetches live Statcast (xFIP, wRC+, pitch arsenal), umpire rating, and today's lineup card, then renders final 3-5 picks with pre-filled `reason` strings. Outputs a ready-to-paste `sliplog.py add --picks` command.

---

## Picking a Slip

- GREEN picks only (score ≥ 65)
- Picks must be from different teams
- Max $50 Flex, max $20 Standard

**Hard stops -- skip regardless of score:**
- Player is on the injured list
- Player came back from injury today
- Both Higher and Lower show no multiplier
- First-inning pitch count on a FADE pitcher (unless the opposing lineup walks a lot)
- Pitcher Ks Higher when line ≥ recent L5 median Ks (`/underdog-mlb-analyze` flags this; verify before placing)
- Pitcher Ks Lower when `pitcher_role` is `reliever` / `mixed` and tonight is a start (relief K sample does not predict starter Ks)

**Marginal-leg gate (when stacking a 3-pick on top of a 2-pick of the same legs):**

Adding a 3rd leg is only +EV if `P(new leg hits | base legs hit) > 1 − (base_mult / new_mult)`. Otherwise drop the 3-pick and double the 2-pick stake instead. See `docs/PICK_LESSONS.md` for the worked example.

---

## Log the Slip (Underdog pick-em)

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
# Verify what got captured before settling
python sliplog.py picks --slip-id 6              # focused per-slip detail
python sliplog.py list --all --detailed          # all slips with per-pick rows indented

# Settle with per-pick outcomes (auto-fires pick_lessons.observe per pick)
python sliplog.py result --id 6 --result loss \
  --outcomes '{"Aaron Civale":4,"Matt Chapman":0}'

# Notion pages auto-update after result (slip + summary)

# Review running totals
python sliplog.py summary                       # slip P&L
python pick_lessons.py review                   # watching queue (near-graduation rules)
python pick_lessons.py review --confirmed       # active score-modifier rules
```

---

## Pick Direction Quick Reference

| Situation | Bet | Direction |
|---|---|---|
| ELITE pitcher | Strikeouts, Pitching Outs | Higher |
| ELITE pitcher | Hits Allowed, Runs Allowed | Lower |
| FADE pitcher | Strikeouts, Pitching Outs | Lower |
| FADE pitcher | Runs Allowed, Hits Allowed | Higher |
| FADE offense batter | H+R+RBI, Hits, Total Bases | Lower |
| ELITE offense batter | H+R+RBI | Higher |
| UNDER lean, game total | Total Runs | Lower |

**Batter split rule:** use the vs-RHP or vs-LHP split based on today's pitcher's throwing hand, not the season average.

**Hot batter rule:** if a batter's last-15-game H+R+RBI average is well above the line, don't fade them even if their team is graded FADE.

**Regression rule:** if a batter's actual average is much higher than their expected average (xAVG), they've been lucky -- lean toward fading.

---

## Drill Deeper

```bash
python player.py pitcher "Aaron Civale"          # last 5 starts
python player.py batter "Matt Chapman"           # last 15 games + splits
python player.py matchup "Chapman" "Civale"      # career head-to-head
```
