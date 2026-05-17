# Pick Lessons — Auto-Generated Rule Discovery

Design doc. Status: **v1 shipped** (2026-05-16). See [Build status](#build-status-v1-shipped) at bottom.

---

## Goal

Every settled pick (win or loss) automatically generates a one-line hypothesis tagged with a normalized rule_key. Hypotheses graduate from `watching → confirmed` after 3 same-direction occurrences, or `→ falsified` after 2 counterexamples. Confirmed rules push to OB1 + `~/second-brain/sports/insights.md`. Watching rules live in a local review queue.

Solves: today we only capture lessons when Raymond and the agent both happen to notice the pattern. Most picks settle and the signal is lost.

## Non-goals

- Replacing manual `/insight` capture. Auto-gen runs alongside it; manual captures still graduate faster.
- Cross-sport generalization. MLB only in v1. Underdog pick'em legs first; single-pick bets second.
- Automated bet placement based on confirmed rules. Confirmed rules surface as recommendations; placement stays human.

---

## Data model

New table in `betlog.db`:

```sql
CREATE TABLE pick_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL,                  -- normalized pattern (see taxonomy)
    hypothesis TEXT NOT NULL,                -- one-line plain-talk rule
    direction TEXT NOT NULL,                 -- 'fail' | 'hit' (does this pattern miss or hit?)
    occurrences INTEGER NOT NULL DEFAULT 1,  -- count of same-direction confirmations
    counters INTEGER NOT NULL DEFAULT 0,     -- count of opposite-direction outcomes
    status TEXT NOT NULL DEFAULT 'watching', -- 'watching' | 'confirmed' | 'falsified'
    first_seen DATE NOT NULL,
    last_seen DATE NOT NULL,
    evidence TEXT NOT NULL,                  -- JSON: [{bet_id, slip_id, pick, outcome, score_delta, date}, ...]
    promoted_at DATETIME,                    -- when status flipped to confirmed
    notes TEXT                               -- free-form, manual edits allowed
);
CREATE UNIQUE INDEX idx_pick_lessons_rule_key ON pick_lessons(rule_key);
```

Append-only on evidence array (never delete past observations). Status transitions:
- `watching` → `confirmed` when `occurrences >= 3 AND counters == 0`
- `watching` → `falsified` when `counters >= 2`
- `confirmed` → `falsified` when 3 counters arrive after promotion (rare; warrants review)

---

## Rule-key taxonomy

The hard part. Keys must be:
- **Narrow enough** that confirmations are real (not spurious co-occurrence)
- **Broad enough** that the same pattern recurs within ~10 picks

v1 key format: `{player_type}_{stat}_{side}_{context}`

Context vocabulary (controlled list, extend as needed):
- `line_ge_l5_median` — line ≥ player's last-5 median for the stat
- `line_lt_l5_median` — line < player's last-5 median
- `line_ge_season_avg` / `line_lt_season_avg` — vs season average
- `elite_vs_fade` / `fade_vs_elite` / `elite_vs_elite` / `fade_vs_fade` — tier matchup (pitchers only)
- `lhb_vs_lhp` / `rhb_vs_rhp` / `lhb_vs_rhp` / `rhb_vs_lhp` — handedness
- `home_split_strong` / `away_split_strong` — venue split tier
- `il_return_le_7d` — coming off IL within 7 days
- `pitcher_role_mismatch` — sample from wrong role (relief sample, starting tonight)

Examples:
- `pitcher_ks_higher_line_ge_l5_median` (deGrom today)
- `pitcher_pos_higher_line_ge_l5_median` (pitching outs)
- `batter_hits_higher_lhb_vs_rhp_line_ge_season_avg`
- `pitcher_ks_lower_pitcher_role_mismatch` (Teng today)

The generator must pick from this controlled list. New context tokens require manual addition to the vocab. This bounds rule_key cardinality.

---

## Generator flow

Triggered after `sliplog.py result` or `betlog.py result` writes a final status.

```
for each pick in settled slip/bet:
    context = {
        player, stat, side, line, outcome,
        pitcher_role, l5_values, l5_median, season_avg,
        handedness_matchup, venue_split, il_status,
        tier_matchup, score_delta = abs(actual - line),
    }
    # generator call: gives 1 hypothesis + 1 rule_key from controlled vocab
    rule_key, hypothesis, direction = generate_hypothesis(context, outcome)

    existing = SELECT * FROM pick_lessons WHERE rule_key = ?
    if existing:
        if direction matches existing.direction:
            occurrences += 1
            evidence.append(new_observation)
            if occurrences >= 3 and counters == 0 and status == 'watching':
                status = 'confirmed'; push_to_ob1(); append_to_insights_md()
        else:
            counters += 1
            evidence.append(new_observation)
            if counters >= 2 and status == 'watching':
                status = 'falsified'
            elif counters >= 3 and status == 'confirmed':
                status = 'under_review'  # surface for manual call
    else:
        INSERT new row with status='watching', occurrences=1
```

Generator implementation **(v1)**: deterministic classifier. `_classify_context()` in `pick_lessons.py` walks a priority cascade — pitcher_role_mismatch → il_return_le_7d → line_vs_l5_median (K stats) → handedness matchup → tier matchup → line_vs_season_avg → unspecified. No LLM, no API cost, no failure mode. Hypothesis text is templated from `CONTEXT_DESCRIPTIONS` constant.

Trade-off: deterministic classifier can only pick from the cascade order. Adding new context tokens means a code change. Accepted — bounded keyspace beats free-form expansion.

LLM generator is a v1.5 candidate if the deterministic classifier proves too coarse after 30 days of data.

---

## Review surfaces

**Watching queue** — new skill `/lessons-review`:
- Lists all `watching` rules with occurrences ≥ 2 (close to graduation)
- For each: hypothesis, evidence dates, gap to confirmation
- Manual confirm/refute/edit-hypothesis options

**Confirmed rules feed** — read by scoring + analyze flows:
- `underdog-mlb-analyze` reads confirmed rules and applies as score modifiers
- e.g. `pitcher_ks_higher_line_ge_l5_median` (confirmed, direction=fail) → -10 penalty on any matching pick

**Insights.md sync** — only on `watching → confirmed` promotion:
- Append entry with `## YYYY-MM-DD — [hypothesis]` + evidence summary
- Same format as existing manual insights so they read uniformly

**OB1 sync** — on promotion only:
- `ob1_push(content, {type: 'rule_confirmed', agent: 'mlb-edge', rule_key, evidence_count})`

---

## Open questions

1. **Threshold tuning.** Is 3 confirmations enough? With 5-10 picks per night, 3 takes 1-3 days for common patterns. Too fast = false positives. Too slow = stale by the time we trust it. Proposal: 3 for v1, revisit after 30 days.

2. **Same-game overlap.** If a slip has 2 picks both matching the same rule_key, count as 1 or 2 occurrences? Proposal: 1, to avoid double-counting correlated outcomes from the same matchup.

3. **Generator drift.** Different LLM calls might generate slightly different hypothesis wording for the same rule_key. Proposal: the rule_key is the dedup primary; hypothesis text on existing rows stays frozen at first-seen wording, never rewritten.

4. **Falsified rules** — should they remain queryable so we don't re-investigate them? Proposal: yes. Keep falsified rows; `underdog-mlb-analyze` ignores them; `/lessons-review` shows them in a separate "graveyard" section.

5. **Confidence score.** Should confirmed rules carry a strength score (e.g. occurrences / (occurrences + counters))? Proposal: skip for v1. Boolean confirmed/falsified is enough until we have >50 rules.

6. **Backfill.** Do we run the generator over historical settled bets/slips? Proposal: yes, one-time pass over all settled rows in `betlog.db` after v1 ships. Seeds the watching queue with real history.

---

## Build steps (v1)

Locked decisions (wu wei recommendations, all confirmed):
1. Threshold = hardcoded 3 occurrences (commit now, tune later)
2. Count by game, not by pick (same-game observations dedup to 1)
3. Hypothesis text freezes on first-seen, manual `edit` subcommand for revisions
4. Falsified rules → graveyard (same table, partition by status, scoring layer ignores)
5. No confidence scores in v1 (boolean confirmed/falsified only)
6. Backfill is deferred to v1.5 once `slip_picks` capture exists

## Build status (v1 shipped)

Shipped 2026-05-16:
- [x] `pick_lessons` table added to `schema/schema.sql` and applied to `betlog.db`
- [x] `pick_lessons.py` with `observe()`, `_classify_context()`, `_on_promotion()`
- [x] `CONTEXT_TOKENS` controlled vocab + `CONTEXT_DESCRIPTIONS` for hypothesis templating
- [x] CLI subcommands: `observe`, `list`, `review`, `falsify`, `resurrect`, `edit`
- [x] OB1 + insights.md push on `watching → confirmed` promotion
- [x] Two real seed observations (Teng Lower role-mismatch, deGrom Higher line_ge_median)
- [x] Smoke test verified promotion + insights.md prepend + OB1 push (then reverted)

Shipped 2026-05-16 (v1.5 — sliplog auto-trigger):
- [x] `slip_picks` table added to `betlog.db` and `schema/schema.sql`
- [x] `sliplog.py add --picks` JSON arg captures per-pick structure on slip log
- [x] `sliplog.py add-picks --slip-id N --picks JSON` retrofits structure to legacy slips
- [x] `sliplog.py result --outcomes` JSON arg settles per-pick (computes hit/miss) and auto-triggers `pick_lessons.observe()` for each pick
- [x] Backwards compat: legacy `--players` csv flow still works (no slip_picks rows, no auto-trigger)
- [x] Graceful warnings for `--outcomes` without `slip_picks` rows, name mismatches, JSON parse failures
- [x] Smoke test: 4 picks across 3 games verified add → settle → observe → same-game dedup → promotion flow → OB1 push → insights.md prepend; all test data rolled back

Daily flow going forward:
```
# Log slip with full structure
python sliplog.py add --entry 20 --payout 77.80 --multiplier "3.89x" \
  --picks '[{"player":"...","player_type":"pitcher","stat":"Strikeouts","line":7.5,"side":"Higher","game":"TEX @ HOU"},...]'

# Settle with per-pick outcomes -- triggers pick_lessons.observe automatically
python sliplog.py result --id 6 --result loss \
  --outcomes '{"player one":4,"player two":15}'
```

Deferred to v2:
- [ ] Backfill subcommand over historical settled rows (low priority — most legacy slips lack per-pick context to regenerate)
- [ ] `/lessons-review` skill wrapper (CLI works for now)
- [ ] Wire `underdog-mlb-analyze` to read confirmed rules as score modifiers (defer until ≥5 confirmed rules exist)
- [ ] `slip_picks` columns in `sliplog.py list` output
