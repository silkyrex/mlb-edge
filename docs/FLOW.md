# Game Day Cheat Sheet

For explanations of terms (ERA, WHIP, splits, etc.) see `README.md`.

---

## The Night Before

```bash
# Pre-load injury data for tomorrow -- no Underdog login needed
python cache_news.py --game "SF Giants @ Athletics" --date 2026-05-16 --roster
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

**2. Scrape lines + auto-cache everything**
```
/underdog-mlb Giants
```
Takes ~70 seconds. Automatically runs `cache_stats`, `cache_news`, `cache_team` when done.

**3. Analyze**
```
/underdog-mlb-analyze "SF Giants @ Athletics" "UNDER LEAN, Civale ELITE ERA 2.54, ..."
```
Outputs GREEN / YELLOW / SKIP picks ranked by score.

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

---

## Log the Bet

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

```bash
python settle.py                      # see current stats for all open bets
python settle.py --id 1 --settle W    # mark win
python settle.py --id 1 --settle L    # mark loss
python betlog.py summary              # running profit/loss
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
