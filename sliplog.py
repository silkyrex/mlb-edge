"""
sliplog.py -- Underdog pick-em slip tracker

Usage:
  # Legacy (no per-pick capture):
  python sliplog.py add --players "Teng,Soriano" --entry 20 --payout 77.80
  python sliplog.py result --id 1 --result win

  # With per-pick structure (enables pick_lessons auto-trigger):
  python sliplog.py add --entry 20 --payout 77.80 --picks '[
      {"player":"Kai-Wei Teng","player_type":"pitcher","stat":"Strikeouts","line":3.5,"side":"Lower","game":"TEX @ HOU"},
      {"player":"Jose Soriano","player_type":"pitcher","stat":"Pitching Outs","line":16.5,"side":"Higher","game":"LAD @ LAA"}
  ]'
  python sliplog.py result --id 1 --result loss --outcomes '{"Kai-Wei Teng":7,"Jose Soriano":15}'

  # Retrofit per-pick structure for an existing slip:
  python sliplog.py add-picks --slip-id 5 --picks '[...]'

  python sliplog.py list / --all      # slips
  python sliplog.py summary           # P&L
"""

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

from ob1 import ob1_push as _ob1_push
from pick_lessons import observe as _pl_observe
import notion_sync as _notion

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path(__file__).parent / "betlog.db"


SLIPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS slips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    platform    TEXT NOT NULL DEFAULT 'underdog',
    picks_count INTEGER NOT NULL,
    players     TEXT NOT NULL,
    boost       TEXT,
    entry       REAL NOT NULL,
    payout      REAL NOT NULL,
    multiplier  TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    profit      REAL,
    logged_at   TEXT DEFAULT (datetime('now')),
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS slip_picks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slip_id     INTEGER NOT NULL REFERENCES slips(id) ON DELETE CASCADE,
    player      TEXT NOT NULL,
    player_type TEXT NOT NULL,           -- 'pitcher' | 'batter' | 'team'
    stat        TEXT NOT NULL,           -- 'Strikeouts', 'Pitching Outs', 'Hits + Runs + RBIs', etc.
    line        REAL NOT NULL,
    side        TEXT NOT NULL,           -- 'Higher' | 'Lower'
    game        TEXT,                    -- 'TEX @ HOU' (per-pick: multi-game slips are common)
    actual      REAL,                    -- filled on result --outcomes
    hit         INTEGER,                 -- 0/1, filled on result --outcomes
    logged_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(slip_id, player, stat)
);
CREATE INDEX IF NOT EXISTS idx_slip_picks_slip ON slip_picks(slip_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SLIPS_SCHEMA)
    conn.commit()
    return conn


def _validate_pick(p: dict) -> dict:
    """Validate a single pick dict. Raises ValueError on bad input."""
    required = ("player", "player_type", "stat", "line", "side")
    missing = [k for k in required if k not in p]
    if missing:
        raise ValueError(f"pick missing required keys: {missing}. Got: {p}")
    if p["player_type"] not in ("pitcher", "batter", "team"):
        raise ValueError(f"player_type must be pitcher/batter/team, got: {p['player_type']}")
    if str(p["side"]).lower() not in ("higher", "lower"):
        raise ValueError(f"side must be Higher/Lower, got: {p['side']}")
    return {
        "player": str(p["player"]).strip(),
        "player_type": p["player_type"],
        "stat": str(p["stat"]).strip(),
        "line": float(p["line"]),
        "side": str(p["side"]).capitalize(),
        "game": str(p.get("game", "")).strip() or None,
        "reason": str(p["reason"]).strip() if p.get("reason") else None,
    }


def _insert_slip_picks(conn, slip_id: int, picks: list[dict]) -> None:
    for p in picks:
        v = _validate_pick(p)
        conn.execute(
            "INSERT OR IGNORE INTO slip_picks (slip_id, player, player_type, stat, line, side, game, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (slip_id, v["player"], v["player_type"], v["stat"], v["line"], v["side"], v["game"], v["reason"]),
        )
    conn.commit()


def _compute_hit(line: float, side: str, actual: float) -> bool:
    s = side.lower()
    if s == "higher":
        return actual > line
    if s == "lower":
        return actual < line
    return False


def cmd_add(args):
    # Two input modes:
    #   --picks JSON: rich per-pick structure (preferred, enables pick_lessons auto-trigger)
    #   --players csv: legacy, name-only list
    if args.picks:
        picks_raw = json.loads(args.picks)
        picks = [_validate_pick(p) for p in picks_raw]
        players = [p["player"] for p in picks]
    else:
        if not args.players:
            raise SystemExit("must provide either --picks JSON or --players csv")
        players = [p.strip() for p in args.players.split(",")]
        picks = []  # no per-pick structure -> no slip_picks rows, no auto-trigger on settle

    boost = None if args.boost in (None, "none", "None") else args.boost
    conn = connect()
    cur = conn.execute("""
        INSERT INTO slips (date, picks_count, players, boost, entry, payout, multiplier, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        args.date or date.today().isoformat(),
        len(players),
        json.dumps(players),
        boost,
        args.entry,
        args.payout,
        args.multiplier,
        args.notes,
    ))
    conn.commit()
    slip_id = cur.lastrowid

    if picks:
        _insert_slip_picks(conn, slip_id, picks)

    boost_str = f" [{boost}]" if boost else ""
    slip_date = args.date or date.today().isoformat()
    print(f"Logged slip #{slip_id}  |  {slip_date}  |  "
          f"{len(players)}-pick: {', '.join(players)}{boost_str}  |  "
          f"${args.entry} entry → ${args.payout} payout")
    if picks:
        for p in picks:
            game_tag = f" ({p['game']})" if p['game'] else ""
            reason_tag = f"  -- {p['reason']}" if p.get('reason') else ""
            print(f"    {p['player']} {p['stat']} {p['line']} {p['side']}{game_tag}{reason_tag}")
    conn.close()

    _ob1_push(
        f"underdog slip placed: {', '.join(players)} | date={slip_date} slip_id={slip_id} "
        f"picks={len(players)} entry=${args.entry:.0f} payout=${args.payout:.2f} "
        f"boost={boost or 'none'} multiplier={args.multiplier or '--'}",
        {"type": "underdog_slip_placed", "slip_id": slip_id, "date": slip_date,
         "players": players, "picks_count": len(players), "entry": args.entry,
         "payout": args.payout, "boost": boost, "agent": "sliplog"},
    )


def cmd_add_picks(args):
    """Retrofit per-pick structure for an existing slip that was logged without --picks."""
    conn = connect()
    slip = conn.execute("SELECT * FROM slips WHERE id=?", (args.slip_id,)).fetchone()
    if not slip:
        print(f"Slip #{args.slip_id} not found.")
        conn.close()
        return
    picks_raw = json.loads(args.picks)
    picks = [_validate_pick(p) for p in picks_raw]
    _insert_slip_picks(conn, args.slip_id, picks)
    print(f"Added {len(picks)} pick(s) to slip #{args.slip_id}:")
    for p in picks:
        game_tag = f" ({p['game']})" if p['game'] else ""
        print(f"    {p['player']} {p['stat']} {p['line']} {p['side']}{game_tag}")
    conn.close()


def cmd_result(args):
    conn = connect()
    slip = conn.execute("SELECT * FROM slips WHERE id=?", (args.id,)).fetchone()
    if not slip:
        print(f"Slip #{args.id} not found.")
        conn.close()
        return
    result = args.result.lower()
    if result not in ("win", "loss"):
        print("--result must be win or loss")
        conn.close()
        return
    profit = round(slip["payout"] - slip["entry"], 2) if result == "win" else round(-slip["entry"], 2)
    conn.execute(
        "UPDATE slips SET status=?, profit=? WHERE id=?",
        (result, profit, args.id)
    )
    conn.commit()
    sign = "+" if profit > 0 else ""
    print(f"Slip #{args.id} settled: {result.upper()}  |  P&L: {sign}${profit}")

    # ── Per-pick settle + pick_lessons auto-trigger ──
    triggered_picks = []
    if args.outcomes:
        try:
            outcomes = json.loads(args.outcomes)
        except json.JSONDecodeError as e:
            print(f"  [warn] --outcomes JSON parse failed: {e} -- per-pick settle skipped")
            outcomes = None
        if outcomes:
            picks = conn.execute(
                "SELECT * FROM slip_picks WHERE slip_id=?", (args.id,)
            ).fetchall()
            if not picks:
                print(f"  [warn] slip #{args.id} has no slip_picks rows -- "
                      "use 'add-picks' to retrofit before settling with --outcomes")
            for sp in picks:
                # Match outcomes dict by player name (case-insensitive contains for flexibility)
                actual = _lookup_outcome(outcomes, sp["player"])
                if actual is None:
                    print(f"  [warn] no outcome for {sp['player']} -- skipped")
                    continue
                hit = _compute_hit(sp["line"], sp["side"], actual)
                conn.execute(
                    "UPDATE slip_picks SET actual=?, hit=? WHERE id=?",
                    (actual, 1 if hit else 0, sp["id"]),
                )
                triggered_picks.append({
                    "player": sp["player"],
                    "player_type": sp["player_type"],
                    "stat": sp["stat"],
                    "line": sp["line"],
                    "side": sp["side"],
                    "actual": actual,
                    "hit": hit,
                    "game": sp["game"] or "",
                    "reason": sp["reason"] or "",
                })
            conn.commit()

    conn.close()

    # Call pick_lessons.observe per pick (post-commit, so DB state is consistent)
    if triggered_picks:
        print(f"\nAuto-trigger: pick_lessons.observe for {len(triggered_picks)} pick(s):")
        for tp in triggered_picks:
            ctx = json.dumps({"reason": tp["reason"]}) if tp.get("reason") else None
            _pl_observe(
                player=tp["player"],
                player_type=tp["player_type"],
                stat=tp["stat"],
                line=tp["line"],
                side=tp["side"],
                actual=tp["actual"],
                hit=tp["hit"],
                game=tp["game"],
                date_str=slip["date"],
                context=ctx,
            )

    try:
        _notion.sync_slip(args.id)
        _notion.post_summary()
        print("Notion: slip + summary updated")
    except Exception as e:
        print(f"Notion sync failed (non-fatal): {e}")

    players = json.loads(slip["players"])
    _ob1_push(
        f"underdog slip outcome: {', '.join(players)} | date={slip['date']} slip_id={args.id} "
        f"picks={slip['picks_count']} entry=${slip['entry']:.0f} payout=${slip['payout']:.2f} "
        f"result={result} profit={sign}${abs(profit):.2f} boost={slip['boost'] or 'none'}",
        {"type": "underdog_slip_outcome", "slip_id": args.id, "date": slip["date"],
         "players": players, "result": result, "profit": profit,
         "entry": slip["entry"], "payout": slip["payout"], "agent": "sliplog"},
    )


def _lookup_outcome(outcomes: dict, player_name: str) -> float | None:
    """Match outcome key to player name. Exact match first, then case-insensitive substring."""
    if player_name in outcomes:
        return float(outcomes[player_name])
    lower_name = player_name.lower()
    for k, v in outcomes.items():
        kl = k.lower()
        if kl == lower_name or kl in lower_name or lower_name in kl:
            return float(v)
    return None


def cmd_list(args):
    conn = connect()
    if args.all:
        rows = conn.execute("SELECT * FROM slips ORDER BY date DESC, id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM slips WHERE status='open' ORDER BY date DESC, id DESC"
        ).fetchall()
    if not rows:
        conn.close()
        print("No slips found.")
        return
    print(f"\n{'ID':<4} {'Date':<12} {'Picks':<6} {'Players':<35} {'Boost':<14} {'Entry':>7} {'Payout':>9} {'Status':<8} {'P&L'}")
    print("-" * 110)
    for r in rows:
        players = ", ".join(json.loads(r["players"]))
        boost = r["boost"] or "-"
        pnl = f"+${r['profit']:.2f}" if r["profit"] and r["profit"] > 0 else (f"-${abs(r['profit']):.2f}" if r["profit"] else "-")
        print(f"{r['id']:<4} {r['date']:<12} {r['picks_count']:<6} {players:<35} {boost:<14} ${r['entry']:>6.2f} ${r['payout']:>8.2f} {r['status']:<8} {pnl}")
        if args.detailed:
            picks = conn.execute(
                "SELECT player, player_type, stat, line, side, game, actual, hit "
                "FROM slip_picks WHERE slip_id=? ORDER BY id", (r["id"],)
            ).fetchall()
            if picks:
                for sp in picks:
                    result_tag = ""
                    if sp["hit"] is not None:
                        result_tag = " HIT" if sp["hit"] else " MISS"
                    actual_tag = f"  -> {sp['actual']}" if sp["actual"] is not None else ""
                    game_tag = f"  ({sp['game']})" if sp["game"] else ""
                    reason_tag = f"  [{sp['reason']}]" if sp["reason"] else ""
                    print(f"      {sp['player']:<28} {sp['stat']:<22} {sp['line']:>5} {sp['side']:<7}{actual_tag}{result_tag}{game_tag}{reason_tag}")
            else:
                print(f"      (no per-pick structure -- legacy slip, retrofit via 'add-picks')")
    conn.close()


def cmd_picks(args):
    """Show per-pick structure for one slip."""
    conn = connect()
    slip = conn.execute("SELECT * FROM slips WHERE id=?", (args.slip_id,)).fetchone()
    if not slip:
        print(f"Slip #{args.slip_id} not found.")
        conn.close()
        return
    picks = conn.execute(
        "SELECT * FROM slip_picks WHERE slip_id=? ORDER BY id", (args.slip_id,)
    ).fetchall()
    conn.close()
    boost = f" [{slip['boost']}]" if slip["boost"] else ""
    print(f"\nSlip #{slip['id']} ({slip['date']}, {slip['status']}){boost}  "
          f"${slip['entry']:.2f} -> ${slip['payout']:.2f}")
    if not picks:
        print("  (no per-pick structure -- legacy slip, retrofit via 'sliplog.py add-picks')")
        return
    for sp in picks:
        result_tag = ""
        if sp["hit"] is not None:
            result_tag = "  HIT" if sp["hit"] else "  MISS"
        actual_tag = f"  -> {sp['actual']}" if sp["actual"] is not None else "  (pending)"
        game_tag = f"  ({sp['game']})" if sp["game"] else ""
        reason_tag = f"\n    reason: {sp['reason']}" if sp["reason"] else ""
        print(f"  {sp['player']:<28} {sp['player_type']:<8} {sp['stat']:<22} {sp['line']:>5} {sp['side']:<7}{actual_tag}{result_tag}{game_tag}{reason_tag}")


def cmd_summary(args):
    conn = connect()
    rows = conn.execute("SELECT * FROM slips ORDER BY date").fetchall()
    conn.close()
    if not rows:
        print("No slips.")
        return
    wins = [r for r in rows if r["status"] == "win"]
    losses = [r for r in rows if r["status"] == "loss"]
    open_slips = [r for r in rows if r["status"] == "open"]
    total_staked = sum(r["entry"] for r in rows if r["status"] != "open")
    total_profit = sum(r["profit"] for r in rows if r["profit"] is not None)
    print(f"\nUNDERDOG SLIP SUMMARY")
    print(f"  Total slips: {len(rows)}  ({len(wins)}W / {len(losses)}L / {len(open_slips)} open)")
    if total_staked:
        roi = (total_profit / total_staked) * 100
        sign = "+" if total_profit >= 0 else ""
        print(f"  Staked: ${total_staked:.2f}  |  P&L: {sign}${total_profit:.2f}  |  ROI: {sign}{roi:.1f}%")
    if open_slips:
        print(f"\n  OPEN ({len(open_slips)}):")
        for r in open_slips:
            players = ", ".join(json.loads(r["players"]))
            boost = f" [{r['boost']}]" if r["boost"] else ""
            print(f"    #{r['id']}  {r['date']}  {players}{boost}  ${r['entry']} → ${r['payout']}")


def main():
    parser = argparse.ArgumentParser(description="Underdog slip tracker")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Log a slip")
    p_add.add_argument("--players", default=None, help="Legacy: comma-separated player names. Use --picks for full structure.")
    p_add.add_argument("--picks", default=None,
                       help='JSON list of pick dicts: [{"player":"...","player_type":"pitcher","stat":"Strikeouts","line":7.5,"side":"Higher","game":"TEX @ HOU"}, ...]')
    p_add.add_argument("--entry", type=float, required=True, help="Dollars wagered")
    p_add.add_argument("--payout", type=float, required=True, help="Payout if all picks win")
    p_add.add_argument("--boost", default=None, help="Boost name or 'none'")
    p_add.add_argument("--multiplier", default=None, help="e.g. '9.42x'")
    p_add.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_add.add_argument("--notes", default=None)

    p_ap = sub.add_parser("add-picks", help="Retrofit per-pick structure to an existing slip")
    p_ap.add_argument("--slip-id", type=int, required=True)
    p_ap.add_argument("--picks", required=True, help="JSON list of pick dicts (same shape as add --picks)")

    p_res = sub.add_parser("result", help="Settle a slip")
    p_res.add_argument("--id", type=int, required=True)
    p_res.add_argument("--result", required=True, choices=["win", "loss"])
    p_res.add_argument("--outcomes", default=None,
                       help='JSON dict of {player_name: actual_value}. Triggers pick_lessons.observe per pick.')

    p_list = sub.add_parser("list", help="List slips")
    p_list.add_argument("--all", action="store_true", help="Include settled slips")
    p_list.add_argument("--detailed", "-d", action="store_true",
                        help="Show per-pick structure under each slip (player, stat, line, side, outcome)")

    p_picks = sub.add_parser("picks", help="Show per-pick detail for one slip")
    p_picks.add_argument("--slip-id", type=int, required=True)

    sub.add_parser("summary", help="P&L summary")

    args = parser.parse_args()
    if args.cmd == "add":
        cmd_add(args)
    elif args.cmd == "add-picks":
        cmd_add_picks(args)
    elif args.cmd == "result":
        cmd_result(args)
    elif args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "picks":
        cmd_picks(args)
    elif args.cmd == "summary":
        cmd_summary(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
