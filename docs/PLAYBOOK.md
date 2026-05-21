# MLB Underdog Playbook

Decision guide. Commands → FLOW.md. Tiers, pick direction, hard stops → REFERENCE.md.

---

## Lines Source Rule

Lines always come from the Underdog scrape (`/underdog-mlb`). External sites (Covers, BetMGM, FantasyPros, DraftKings) can kill a pick — they cannot create one. If Playwright isn't active, start it. Do not substitute.

---

## Game Selection

Run `prescan.py` first. Top 5 ranked by edge score — but scrape only the 2-3 you actually want to bet. Never scrape the full slate (geo-lock risk).

Target: ELITE pitcher vs FADE offense, or ELITE offense vs FADE pitcher. prescan.py ranks this. Trust the ranking.

---

## Bankroll Gate

Run `python sliplog.py summary` before every session. Entry size is gated by current balance:

| Balance | Max entry/slip |
|---------|----------------|
| < $220 | $10 |
| $220–$329 | $20 |
| $330–$439 | $30 |
| $440–$549 | $40 |
| $550+ | $50 |

- Max 2 slips per slate
- Picks from 2+ different teams per slip
- Never the same player on more than one open slip
- 3rd leg: sliplog.py flags if it doesn't improve EV — trust it

---

## Gates

Do not revise stat tiers or pick_lessons thresholds until 20 resolved picks.
Do not run prescan backtest until 7+ days of pre_scan_scores exist in mlb.db.
