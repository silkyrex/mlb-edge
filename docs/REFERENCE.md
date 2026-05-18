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
research confirm:  +15 confirmed rule / -15 contradicted
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

## Bet-Strategy Rules

- First-inning pitch count Higher on FADE pitchers is volatile -- only take when opposing lineup has documented high walk rates.
- Check `last15_h_r_rbi` against the line before fading any batter regardless of team grade.
- Check `x_avg` vs `season_avg` -- actual much higher than expected = regression risk.
