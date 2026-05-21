# Reference

Lookup doc for tiers, scoring, and pick direction. See `docs/FLOW.md` for game-day steps.

---

## Tier System

- **ELITE** -- ERA ≤ ~3.00 (pitchers), top-tier offense
- **mid** -- average
- **FADE** -- ERA ≥ ~4.50 (pitchers), weak offense

Signal logic (`matchup.py`): OVER = ELITE offense vs FADE pitcher; UNDER = ELITE pitcher vs FADE offense; AWAY/HOME = ELITE away/home offense vs mid/FADE opponent.

---

## Scoring Rules (underdog-mlb-analyze)

```
base = 50
lean alignment:    +25 aligned / -25 opposed
market mult:       +10 if >1.04x / +5 if 1.00-1.04x / -5 if 0.95-1.00x / -10 if <0.90x
research confirm:  +15 stats/trends confirm / -15 contradict
rule history:      confidence * (hit_rate - 0.5) * 20, capped +/-10
                   where confidence = min(n, 15) / 15, n = occurrences + counters
                   0 if no matching rule in pick_lessons
grade bonus:       +10 for ELITE pitcher K/PO Higher, FADE offense batter Lower, FADE pitcher ERA Higher
IL return penalty: -10 for IL-return-today or IL-return-Nd (N <= 7)
```

GREEN >= 65. YELLOW 50-64. SKIP below 50 or IL-flagged.

---

## Pick Direction Quick Reference

| Situation | Stat | Direction |
|---|---|---|
| ELITE pitcher | Strikeouts, Pitching Outs | Higher |
| ELITE pitcher | Hits Allowed, Runs Allowed | Lower |
| FADE pitcher | Strikeouts, Pitching Outs | Lower |
| FADE pitcher | Runs Allowed, Hits Allowed | Higher |
| FADE offense batter | H+R+RBI, Hits, Total Bases | Lower |
| ELITE offense batter | H+R+RBI | Higher |
| UNDER lean | Total Runs | Lower |

**Batter split rule:** use vs-RHP or vs-LHP split based on today's starter's arm -- not season average.

**Hot batter rule:** if L15 H+R+RBI average is well above the line, don't fade regardless of team grade.

**Regression rule:** real avg much higher than xAVG = lucky, lean toward fading.

**Ump rule:** high error rate ump = downgrade K Higher props for both starters that game.

**Rest rule:** short rest (<=3d) = downgrade K Higher; extra rest (>=6d) = note first-inning rust risk.

---

## Hard Stops (skip immediately — no override)

- Player on IL or returned from IL today
- IL return within 7 days (rust penalty)
- Both Higher and Lower show no multiplier on Underdog
- Pitcher Ks Higher when line ≥ player's L5 median Ks
- Pitcher Ks Lower when L5 sample is from wrong role (was relief, starting tonight)
- 1st-inn pitch count Higher on a FADE pitcher — skip, period
- Batter H+R+RBI Higher when line ≥ season average — **falsified** (1 hit vs 3 fails in pick_lessons)
- `x_avg` well below `season_avg` — closer.py flags this; trust the flag
- L15 H+R+RBI average below the line — don't fade the batter regardless of team grade
