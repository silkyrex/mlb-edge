"""
pick_lessons.py -- auto-generated rule tracker for MLB picks.

Every settled pick generates one observation tagged with a normalized rule_key.
Same rule_key -> increments occurrences (same game = 1, not N). Different rule_key
-> new 'watching' row. Graduates to 'confirmed' at 3 same-direction occurrences
with 0 counters. Falsified at 2 counters. Confirmed rules push to OB1 + insights.md.

Usage:
  python pick_lessons.py observe --player "Jacob deGrom" --player-type pitcher \
      --stat Strikeouts --line 7.5 --side Higher --actual 4 --game "TEX @ HOU"
  python pick_lessons.py list                        # all rules
  python pick_lessons.py list --status watching      # filter
  python pick_lessons.py review                      # watching queue
  python pick_lessons.py review --confirmed          # active rules
  python pick_lessons.py review --graveyard          # falsified rules
  python pick_lessons.py falsify --rule-key K --reason "no longer works"
  python pick_lessons.py resurrect --rule-key K
  python pick_lessons.py edit --rule-key K --hypothesis "new text"
"""

import argparse
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from ob1 import ob1_push as _ob1_push

DB_PATH = Path(__file__).parent / "sliplog.db"
MLB_DB = Path(__file__).parent / "mlb.db"
INSIGHTS_MD = Path.home() / "second-brain" / "sports" / "insights.md"

PROMOTION_THRESHOLD = 3       # occurrences to graduate watching -> confirmed
FALSIFY_THRESHOLD = 2         # counters to falsify watching
UNDER_REVIEW_THRESHOLD = 3    # counters to flip confirmed -> under_review

# Controlled vocabulary -- generator must pick ONE context token from this list.
# Extend deliberately; new tokens require code change so we never silently grow keyspace.
CONTEXT_TOKENS = [
    "line_ge_l5_median",
    "line_lt_l5_median",
    "line_ge_season_avg",
    "line_lt_season_avg",
    "elite_vs_fade",
    "fade_vs_elite",
    "elite_vs_elite",
    "fade_vs_fade",
    "lhb_vs_lhp",
    "rhb_vs_rhp",
    "lhb_vs_rhp",
    "rhb_vs_lhp",
    "home_split_strong",
    "away_split_strong",
    "il_return_le_7d",
    "pitcher_role_mismatch",
    "unspecified_context",
]

CONTEXT_DESCRIPTIONS = {
    "line_ge_l5_median": "when the line is at or above the player's L5 median",
    "line_lt_l5_median": "when the line is below the player's L5 median",
    "line_ge_season_avg": "when the line is at or above the season average",
    "line_lt_season_avg": "when the line is below the season average",
    "elite_vs_fade": "in an elite-vs-fade tier matchup",
    "fade_vs_elite": "in a fade-vs-elite tier matchup",
    "elite_vs_elite": "in an elite-vs-elite tier matchup",
    "fade_vs_fade": "in a fade-vs-fade tier matchup",
    "lhb_vs_lhp": "as LHB facing LHP",
    "rhb_vs_rhp": "as RHB facing RHP",
    "lhb_vs_rhp": "as LHB facing RHP",
    "rhb_vs_lhp": "as RHB facing LHP",
    "home_split_strong": "with a strong home split",
    "away_split_strong": "with a strong away split",
    "il_return_le_7d": "within 7 days of an IL return",
    "pitcher_role_mismatch": "when L5 sample is from the wrong role (relief sample, starting tonight)",
    "unspecified_context": "(context unmapped)",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS pick_lessons (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key     TEXT NOT NULL UNIQUE,
    hypothesis   TEXT NOT NULL,
    direction    TEXT NOT NULL,
    occurrences  INTEGER NOT NULL DEFAULT 1,
    counters     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'watching',
    first_seen   DATE NOT NULL,
    last_seen    DATE NOT NULL,
    evidence     TEXT NOT NULL,
    promoted_at  DATETIME,
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS idx_pl_status ON pick_lessons(status);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ── context auto-fill from mlb.db ──────────────────────────────────────────────

def _fetch_player_context(player: str, cache_date: str) -> dict:
    """Pull cached stats for player on a given date. Used to enrich the rule context."""
    if not MLB_DB.exists():
        return {}
    out = {}
    conn = sqlite3.connect(MLB_DB)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM player_recent_stats WHERE player = ? AND cache_date = ?",
            (player, cache_date),
        ).fetchone()
        if row:
            if row["last5_ks"]:
                try:
                    ks = json.loads(row["last5_ks"])
                    if ks:
                        ks_sorted = sorted(ks)
                        out["l5_median"] = float(ks_sorted[len(ks_sorted) // 2])
                except Exception:
                    pass
            if row["season_avg"] is not None:
                out["season_avg"] = row["season_avg"]
            if row["season_k9"] is not None:
                out["season_k9"] = row["season_k9"]
            if row["pitcher_role"]:
                out["pitcher_role"] = row["pitcher_role"]

        news = conn.execute(
            "SELECT status FROM player_news WHERE player = ? AND news_date = ?",
            (player, cache_date),
        ).fetchone()
        if news and news["status"]:
            status = news["status"]
            if status == "IL-return-today":
                out["il_status_days"] = 0
            elif status.startswith("IL-return-") and status.endswith("d"):
                try:
                    out["il_status_days"] = int(status.replace("IL-return-", "").rstrip("d"))
                except Exception:
                    pass
    finally:
        conn.close()
    return out


# ── deterministic classifier ───────────────────────────────────────────────────

def _classify_context(ctx: dict) -> str:
    """Pick the single most predictive context token from CONTEXT_TOKENS."""
    player_type = ctx.get("player_type", "")
    stat = (ctx.get("stat") or "").lower()
    line = ctx.get("line")

    # Highest priority: structural sample defects
    if player_type == "pitcher" and ctx.get("pitcher_role") in ("reliever", "mixed"):
        return "pitcher_role_mismatch"

    if ctx.get("il_status_days") is not None and 0 <= ctx["il_status_days"] <= 7:
        return "il_return_le_7d"

    # Line vs L5 median (only valid for K stats -- L5 cache stores last5_ks)
    stat_is_k = any(k in stat for k in ("strikeout", "ks"))
    if stat_is_k and line is not None and ctx.get("l5_median") is not None:
        if line >= ctx["l5_median"]:
            return "line_ge_l5_median"
        return "line_lt_l5_median"

    # Handedness matchup
    bh = (ctx.get("batter_hand") or "").lower()
    ph = (ctx.get("pitcher_hand") or "").lower()
    if bh in ("l", "r") and ph in ("l", "r"):
        return f"{bh}hb_vs_{ph}hp"

    # Tier matchup
    bt = (ctx.get("batter_tier") or ctx.get("offense_tier") or "").lower()
    pt = (ctx.get("pitcher_tier") or "").lower()
    if bt in ("elite", "fade") and pt in ("elite", "fade"):
        return f"{bt}_vs_{pt}"

    # Line vs season avg fallback
    if line is not None and ctx.get("season_avg") is not None:
        if line >= ctx["season_avg"]:
            return "line_ge_season_avg"
        return "line_lt_season_avg"

    return "unspecified_context"


def _normalize_stat(stat: str) -> str:
    s = stat.lower().strip()
    s = s.replace("+", "_plus_").replace(" ", "_").replace("-", "_").replace("/", "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def _build_rule_key(player_type: str, stat: str, side: str, context_token: str) -> str:
    return f"{player_type}_{_normalize_stat(stat)}_{side.lower()}_{context_token}"


def _build_hypothesis(player_type: str, stat: str, side: str, context_token: str, direction: str) -> str:
    verb = "fails" if direction == "fail" else "hits"
    side_word = side.lower()
    desc = CONTEXT_DESCRIPTIONS.get(context_token, context_token)
    return f"{player_type.title()} {stat} {side_word} {verb} {desc}."


# ── observation ────────────────────────────────────────────────────────────────

def observe(
    player: str,
    player_type: str,
    stat: str,
    line: float,
    side: str,
    actual: float,
    hit: bool,
    game: str,
    date_str: str,
    extra_context: dict | None = None,
) -> tuple[str, str]:
    """Record one pick observation. Returns (rule_key, final_status)."""
    auto_ctx = _fetch_player_context(player, date_str)
    ctx = {
        "player": player,
        "player_type": player_type,
        "stat": stat,
        "line": line,
        "side": side,
        "actual": actual,
        "hit": hit,
        "game": game,
        "date": date_str,
        **auto_ctx,
    }
    if extra_context:
        ctx.update(extra_context)

    context_token = _classify_context(ctx)
    direction = "fail" if not hit else "hit"
    rule_key = _build_rule_key(player_type, stat, side, context_token)
    hypothesis = _build_hypothesis(player_type, stat, side, context_token, direction)

    new_ev = {
        "date": date_str,
        "player": player,
        "stat": stat,
        "line": line,
        "side": side.lower(),
        "actual": actual,
        "hit": hit,
        "game": game,
    }

    conn = connect()
    existing = conn.execute(
        "SELECT * FROM pick_lessons WHERE rule_key = ?", (rule_key,)
    ).fetchone()

    if existing:
        evidence_list = json.loads(existing["evidence"])
        dup = any((e["game"], e["date"]) == (game, date_str) for e in evidence_list)
        if dup:
            new_ev["redundant"] = True
            evidence_list.append(new_ev)
            conn.execute(
                "UPDATE pick_lessons SET evidence=?, last_seen=? WHERE rule_key=?",
                (json.dumps(evidence_list), date_str, rule_key),
            )
            conn.commit()
            conn.close()
            print(f"  rule_key={rule_key} [redundant same-game, no counter update]")
            return rule_key, existing["status"]

        evidence_list.append(new_ev)

        if existing["status"] == "falsified":
            conn.execute(
                "UPDATE pick_lessons SET evidence=?, last_seen=? WHERE rule_key=?",
                (json.dumps(evidence_list), date_str, rule_key),
            )
            conn.commit()
            conn.close()
            print(f"  rule_key={rule_key} [graveyard hit, manual resurrect required]")
            return rule_key, "falsified"

        if direction == existing["direction"]:
            new_occ = existing["occurrences"] + 1
            new_cnt = existing["counters"]
        else:
            new_occ = existing["occurrences"]
            new_cnt = existing["counters"] + 1

        new_status = existing["status"]
        promoted_at = existing["promoted_at"]
        if existing["status"] == "watching":
            if new_occ >= PROMOTION_THRESHOLD and new_cnt == 0:
                new_status = "confirmed"
                promoted_at = datetime.now(timezone.utc).isoformat()
            elif new_cnt >= FALSIFY_THRESHOLD:
                new_status = "falsified"
        elif existing["status"] == "confirmed":
            if new_cnt >= UNDER_REVIEW_THRESHOLD:
                new_status = "under_review"

        conn.execute(
            "UPDATE pick_lessons SET occurrences=?, counters=?, status=?, evidence=?, "
            "last_seen=?, promoted_at=? WHERE rule_key=?",
            (new_occ, new_cnt, new_status, json.dumps(evidence_list),
             date_str, promoted_at, rule_key),
        )
        conn.commit()
        conn.close()

        transition = f" [STATUS: {existing['status']} -> {new_status}]" if new_status != existing["status"] else ""
        print(f"  rule_key={rule_key} occ={new_occ} cnt={new_cnt} status={new_status}{transition}")
        if new_status == "confirmed" and existing["status"] == "watching":
            _on_promotion(rule_key, existing["hypothesis"], evidence_list, date_str)
        return rule_key, new_status

    conn.execute(
        "INSERT INTO pick_lessons (rule_key, hypothesis, direction, occurrences, counters, "
        "status, first_seen, last_seen, evidence) "
        "VALUES (?, ?, ?, 1, 0, 'watching', ?, ?, ?)",
        (rule_key, hypothesis, direction, date_str, date_str, json.dumps([new_ev])),
    )
    conn.commit()
    conn.close()
    print(f"  rule_key={rule_key} NEW [watching, 1/{PROMOTION_THRESHOLD}]")
    return rule_key, "watching"


def _on_promotion(rule_key: str, hypothesis: str, evidence_list: list, date_str: str) -> None:
    """Fires when watching -> confirmed. Pushes to OB1 and appends to insights.md."""
    real_ev = [e for e in evidence_list if not e.get("redundant")]
    summary_lines = [
        f"  - {e['date']} {e['player']} {e['stat']} {e['line']} {e['side']} -> {e['actual']} ({'hit' if e['hit'] else 'miss'})"
        for e in real_ev
    ]
    ob1_content = (
        f"RULE CONFIRMED [{rule_key}]: {hypothesis}\n"
        f"Evidence ({len(real_ev)}):\n" + "\n".join(summary_lines)
    )
    _ob1_push(ob1_content, {
        "type": "rule_confirmed",
        "rule_key": rule_key,
        "agent": "mlb-edge",
        "evidence_count": len(real_ev),
    })

    if INSIGHTS_MD.exists():
        section = (
            f"## {date_str} — [Confirmed Rule] {hypothesis}\n"
            f"**rule_key:** `{rule_key}`\n"
            f"**Evidence ({len(real_ev)}):**\n" +
            "\n".join(summary_lines) +
            "\n\n---\n\n"
        )
        body = INSIGHTS_MD.read_text()
        # Insert before the first ## entry (preserve title + format note).
        idx = body.find("\n## ")
        if idx == -1:
            INSIGHTS_MD.write_text(body.rstrip() + "\n\n" + section)
        else:
            INSIGHTS_MD.write_text(body[:idx + 1] + section + body[idx + 1:])

    print(f"  [PROMOTED] {rule_key} -> confirmed; OB1 + insights.md updated.")


# ── CLI commands ───────────────────────────────────────────────────────────────

def _compute_hit(line: float, side: str, actual: float) -> bool:
    s = side.lower()
    if s == "higher":
        return actual > line
    if s == "lower":
        return actual < line
    return False


def cmd_observe(args):
    side = args.side
    actual = args.actual
    hit = args.hit if args.hit is not None else _compute_hit(args.line, side, actual)
    extra = json.loads(args.context) if args.context else None
    observe(
        player=args.player,
        player_type=args.player_type,
        stat=args.stat,
        line=args.line,
        side=side,
        actual=actual,
        hit=hit,
        game=args.game,
        date_str=args.date or date.today().isoformat(),
        extra_context=extra,
    )


def cmd_list(args):
    conn = connect()
    if args.status:
        rows = conn.execute(
            "SELECT * FROM pick_lessons WHERE status=? ORDER BY last_seen DESC",
            (args.status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pick_lessons ORDER BY status, last_seen DESC"
        ).fetchall()
    conn.close()
    if not rows:
        print("No pick lessons yet.")
        return
    print(f"\n{'rule_key':<60} {'status':<12} {'occ':>3} {'cnt':>3} {'first':<11} {'last':<11}")
    print("-" * 110)
    for r in rows:
        print(f"{r['rule_key']:<60} {r['status']:<12} {r['occurrences']:>3} {r['counters']:>3} {r['first_seen']:<11} {r['last_seen']:<11}")


def cmd_review(args):
    conn = connect()
    if args.graveyard:
        rows = conn.execute("SELECT * FROM pick_lessons WHERE status='falsified' ORDER BY last_seen DESC").fetchall()
        title = "FALSIFIED RULES (graveyard)"
    elif args.confirmed:
        rows = conn.execute("SELECT * FROM pick_lessons WHERE status='confirmed' ORDER BY occurrences DESC").fetchall()
        title = "CONFIRMED RULES"
    elif args.under_review:
        rows = conn.execute("SELECT * FROM pick_lessons WHERE status='under_review' ORDER BY last_seen DESC").fetchall()
        title = "RULES UNDER REVIEW"
    else:
        rows = conn.execute(
            "SELECT * FROM pick_lessons WHERE status='watching' ORDER BY occurrences DESC, last_seen DESC"
        ).fetchall()
        title = "WATCHING QUEUE"
    conn.close()
    if not rows:
        print(f"No rules in {title.lower()}.")
        return
    print(f"\n{title}\n")
    for r in rows:
        ev = json.loads(r["evidence"])
        print(f"[{r['rule_key']}] occ={r['occurrences']} cnt={r['counters']} {r['first_seen']} -> {r['last_seen']}")
        print(f"  {r['hypothesis']}")
        for e in ev:
            tag = " (dup)" if e.get("redundant") else ""
            print(f"    - {e['date']} {e['player']} {e['stat']} {e['line']} {e['side']} -> {e['actual']} ({'hit' if e['hit'] else 'miss'}){tag}")
        print()


def cmd_stats(args):
    """Operational overview: counts by status, top watching near graduation, top falsified."""
    conn = connect()
    rows = conn.execute("SELECT * FROM pick_lessons").fetchall()
    conn.close()
    if not rows:
        print("No pick lessons yet.")
        return

    by_status = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)

    print(f"\nPICK LESSONS — TOTAL {len(rows)} rule(s)")
    print(f"  watching:    {len(by_status.get('watching', []))}")
    print(f"  confirmed:   {len(by_status.get('confirmed', []))}")
    print(f"  falsified:   {len(by_status.get('falsified', []))}")
    print(f"  under_review:{len(by_status.get('under_review', []))}")

    watching = by_status.get("watching", [])
    if watching:
        near = sorted(
            watching,
            key=lambda r: (-(r["occurrences"] - r["counters"]), r["counters"]),
        )
        print(f"\nNEAR GRADUATION (need {PROMOTION_THRESHOLD} occ + 0 cnt for confirm, {FALSIFY_THRESHOLD} cnt for falsify):")
        for r in near[:10]:
            occ = r["occurrences"]
            cnt = r["counters"]
            gap_promote = PROMOTION_THRESHOLD - occ if cnt == 0 else None
            gap_falsify = FALSIFY_THRESHOLD - cnt if cnt > 0 else None
            tags = []
            if gap_promote is not None and gap_promote > 0:
                tags.append(f"{gap_promote} to promote")
            elif cnt > 0:
                tags.append("blocked from promote (cnt>0)")
            if gap_falsify is not None and gap_falsify > 0:
                tags.append(f"{gap_falsify} to falsify")
            tag_str = "  " + ", ".join(tags) if tags else ""
            print(f"  [{occ}/{PROMOTION_THRESHOLD} occ, {cnt} cnt]  {r['rule_key']}{tag_str}")
            print(f"      {r['hypothesis']}")

    confirmed = by_status.get("confirmed", [])
    if confirmed:
        print(f"\nCONFIRMED RULES (active score modifiers):")
        for r in sorted(confirmed, key=lambda x: -x["occurrences"])[:10]:
            print(f"  [{r['occurrences']}/{r['counters']}]  {r['rule_key']}")
            print(f"      {r['hypothesis']}")

    falsified = by_status.get("falsified", [])
    if falsified:
        print(f"\nFALSIFIED RULES (graveyard, {len(falsified)} total):")
        for r in falsified[:5]:
            print(f"  {r['rule_key']}")
        if len(falsified) > 5:
            print(f"  ... ({len(falsified) - 5} more — see 'review --graveyard')")
    print()


def cmd_falsify(args):
    conn = connect()
    conn.execute(
        "UPDATE pick_lessons SET status='falsified', notes=? WHERE rule_key=?",
        (args.reason or "manually falsified", args.rule_key),
    )
    conn.commit()
    conn.close()
    print(f"Marked {args.rule_key} as falsified.")


def cmd_resurrect(args):
    conn = connect()
    conn.execute(
        "UPDATE pick_lessons SET status='watching', counters=0, notes=? "
        "WHERE rule_key=? AND status='falsified'",
        (args.reason or "manually resurrected", args.rule_key),
    )
    conn.commit()
    conn.close()
    print(f"Resurrected {args.rule_key} -> watching (counters reset).")


def cmd_edit(args):
    conn = connect()
    conn.execute(
        "UPDATE pick_lessons SET hypothesis=? WHERE rule_key=?",
        (args.hypothesis, args.rule_key),
    )
    conn.commit()
    conn.close()
    print(f"Updated hypothesis for {args.rule_key}.")


def main():
    parser = argparse.ArgumentParser(description="MLB pick lesson rule tracker")
    sub = parser.add_subparsers(dest="cmd")

    p_obs = sub.add_parser("observe", help="Record one pick observation")
    p_obs.add_argument("--player", required=True)
    p_obs.add_argument("--player-type", required=True, choices=["pitcher", "batter", "team"])
    p_obs.add_argument("--stat", required=True, help="e.g. Strikeouts, Hits, Pitching Outs")
    p_obs.add_argument("--line", required=True, type=float)
    p_obs.add_argument("--side", required=True, choices=["Higher", "Lower", "higher", "lower"])
    p_obs.add_argument("--actual", required=True, type=float)
    p_obs.add_argument("--game", required=True, help="e.g. 'TEX @ HOU'")
    p_obs.add_argument("--date", default=None, help="YYYY-MM-DD")
    p_obs.add_argument("--hit", type=lambda x: x.lower() == "true", default=None,
                       help="Override hit/miss. Default: computed from line/side/actual.")
    p_obs.add_argument("--context", default=None, help="JSON extra context (overrides auto-fill)")

    p_lst = sub.add_parser("list", help="List rules")
    p_lst.add_argument("--status", choices=["watching", "confirmed", "falsified", "under_review"])

    sub.add_parser("stats", help="Operational overview: counts + near-graduation watching rules")

    p_rev = sub.add_parser("review", help="Review queue")
    p_rev.add_argument("--graveyard", action="store_true")
    p_rev.add_argument("--confirmed", action="store_true")
    p_rev.add_argument("--under-review", action="store_true")

    p_fal = sub.add_parser("falsify", help="Manually mark rule as falsified")
    p_fal.add_argument("--rule-key", required=True)
    p_fal.add_argument("--reason", default=None)

    p_res = sub.add_parser("resurrect", help="Move falsified rule back to watching")
    p_res.add_argument("--rule-key", required=True)
    p_res.add_argument("--reason", default=None)

    p_edt = sub.add_parser("edit", help="Edit rule hypothesis text")
    p_edt.add_argument("--rule-key", required=True)
    p_edt.add_argument("--hypothesis", required=True)

    args = parser.parse_args()
    cmd = args.cmd
    if cmd == "observe":
        cmd_observe(args)
    elif cmd == "list":
        cmd_list(args)
    elif cmd == "stats":
        cmd_stats(args)
    elif cmd == "review":
        cmd_review(args)
    elif cmd == "falsify":
        cmd_falsify(args)
    elif cmd == "resurrect":
        cmd_resurrect(args)
    elif cmd == "edit":
        cmd_edit(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
