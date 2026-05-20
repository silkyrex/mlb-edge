# Pick Lessons -- Auto-Generated Rule Tracker

Every settled pick auto-generates a hypothesis keyed to a normalized `rule_key`. Rules graduate `watching → confirmed` at 3 same-direction occurrences (game-deduped), or `→ falsified` at 2 counters. Confirmed rules push to OB1 + `~/second-brain/sports/insights.md`.

---

## Schema (`sliplog.db`)

```sql
CREATE TABLE pick_lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key      TEXT NOT NULL UNIQUE,   -- e.g. pitcher_strikeouts_higher_line_ge_l5_median
    hypothesis    TEXT NOT NULL,          -- one-line plain-talk rule (frozen on first-seen)
    direction     TEXT NOT NULL,          -- 'hit' | 'fail'
    occurrences   INTEGER DEFAULT 1,      -- same-direction confirmations
    counters      INTEGER DEFAULT 0,      -- opposite-direction outcomes
    status        TEXT DEFAULT 'watching',-- watching | confirmed | falsified | under_review
    first_seen    DATE NOT NULL,
    last_seen     DATE NOT NULL,
    evidence      TEXT NOT NULL,          -- JSON array of observations
    promoted_at   DATETIME,
    notes         TEXT
);
```

Status transitions:
- `watching → confirmed` when `occurrences >= 3 AND counters == 0`
- `watching → falsified` when `counters >= 2`
- `confirmed → under_review` when 3 counters arrive post-promotion

---

## Rule-Key Taxonomy

Format: `{player_type}_{stat}_{side}_{context}`

**Context tokens** (controlled list -- add new ones in `pick_lessons.py`):

| Token | Meaning |
|---|---|
| `line_ge_l5_median` | Line >= player's last-5 median for this stat |
| `line_lt_l5_median` | Line < last-5 median |
| `line_ge_season_avg` | Line >= season average |
| `line_lt_season_avg` | Line < season average |
| `elite_vs_fade` | ELITE pitcher vs FADE offense (or vice versa) |
| `fade_vs_elite` | FADE pitcher vs ELITE offense |
| `lhb_vs_lhp` / `rhb_vs_rhp` | Same-hand matchup |
| `lhb_vs_rhp` / `rhb_vs_lhp` | Cross-hand matchup |
| `home_split_strong` / `away_split_strong` | Venue split advantage |
| `il_return_le_7d` | Returned from IL within 7 days |
| `pitcher_role_mismatch` | L5 sample from wrong role (relief sample, starting tonight) |
| `unspecified_context` | Fallback when no token matches |

Examples:
- `pitcher_strikeouts_higher_line_ge_l5_median`
- `batter_hits_plus_runs_plus_rbis_higher_line_ge_season_avg`
- `pitcher_strikeouts_lower_pitcher_role_mismatch`

---

## Auto-Trigger Flow

Triggered by `sliplog.py result --outcomes JSON`. For each pick:

1. Classifier determines context token from player stats in `mlb.db`
2. `rule_key` generated deterministically; hypothesis templated from `CONTEXT_DESCRIPTIONS`
3. If rule exists: same-direction → `occurrences++`; opposite → `counters++`
4. If `occurrences >= 3`: promote to confirmed, push OB1, append `insights.md`
5. Same-game observations dedup to 1 occurrence (prevents correlated legs inflating count)

---

## CLI

```bash
python pick_lessons.py stats                         # counts + near-graduation overview
python pick_lessons.py list                          # all rules with status
python pick_lessons.py list --status confirmed       # confirmed only
python pick_lessons.py review                        # watching queue (near-graduation)
python pick_lessons.py review --confirmed            # active rules
python pick_lessons.py review --graveyard            # falsified rules
python pick_lessons.py falsify --rule-key K --reason "no longer works"
python pick_lessons.py resurrect --rule-key K
python pick_lessons.py edit --rule-key K --hypothesis "revised text"
```

---

## How Confirmed Rules Feed Back

`closer.py` passes all confirmed rules to Scout in the data brief. Scout uses them as positive evidence for matching picks; Skeptic can challenge if the rule is thin. Closer includes rule confirmation in the `reason` string.

`underdog-mlb-analyze` applies a data-driven Rule History modifier (Step 1.6 + Step 4):

```
n = occurrences + counters
hit_rate = occurrences / n
confidence = min(n, 15) / 15      # scales from 0 → 1.0 at 15+ observations
modifier = round(confidence * (hit_rate - 0.5) * 20)   # max +/-10
```

0 if no matching rule exists. Modifier is shown inline in the pick output so you can see what fired.
