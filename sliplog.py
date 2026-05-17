"""
sliplog.py -- Underdog pick-em slip tracker

Usage:
  python sliplog.py add --players "Teng,Soriano" --entry 20 --payout 77.80
  python sliplog.py add --players "Teng,deGrom,Soriano" --entry 20 --payout 188.40 --boost "30% MLB" --multiplier "9.42x"
  python sliplog.py result --id 1 --result win
  python sliplog.py result --id 2 --result loss
  python sliplog.py list              # open slips
  python sliplog.py list --all        # all slips
  python sliplog.py summary           # P&L by date
"""

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

from ob1 import ob1_push as _ob1_push

load_dotenv(Path(__file__).parent / ".env")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
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
        )
    """)
    conn.commit()
    return conn


def cmd_add(args):
    players = [p.strip() for p in args.players.split(",")]
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
    boost_str = f" [{boost}]" if boost else ""
    slip_date = args.date or date.today().isoformat()
    print(f"Logged slip #{slip_id}  |  {slip_date}  |  "
          f"{len(players)}-pick: {', '.join(players)}{boost_str}  |  "
          f"${args.entry} entry → ${args.payout} payout")
    conn.close()

    _ob1_push(
        f"underdog slip placed: {', '.join(players)} | date={slip_date} slip_id={slip_id} "
        f"picks={len(players)} entry=${args.entry:.0f} payout=${args.payout:.2f} "
        f"boost={boost or 'none'} multiplier={args.multiplier or '--'}",
        {"type": "underdog_slip_placed", "slip_id": slip_id, "date": slip_date,
         "players": players, "picks_count": len(players), "entry": args.entry,
         "payout": args.payout, "boost": boost, "agent": "sliplog"},
    )


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
    conn.close()

    players = json.loads(slip["players"])
    _ob1_push(
        f"underdog slip outcome: {', '.join(players)} | date={slip['date']} slip_id={args.id} "
        f"picks={slip['picks_count']} entry=${slip['entry']:.0f} payout=${slip['payout']:.2f} "
        f"result={result} profit={sign}${abs(profit):.2f} boost={slip['boost'] or 'none'}",
        {"type": "underdog_slip_outcome", "slip_id": args.id, "date": slip["date"],
         "players": players, "result": result, "profit": profit,
         "entry": slip["entry"], "payout": slip["payout"], "agent": "sliplog"},
    )


def cmd_list(args):
    conn = connect()
    if args.all:
        rows = conn.execute("SELECT * FROM slips ORDER BY date DESC, id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM slips WHERE status='open' ORDER BY date DESC, id DESC"
        ).fetchall()
    conn.close()
    if not rows:
        print("No slips found.")
        return
    print(f"\n{'ID':<4} {'Date':<12} {'Picks':<6} {'Players':<35} {'Boost':<14} {'Entry':>7} {'Payout':>9} {'Status':<8} {'P&L'}")
    print("-" * 110)
    for r in rows:
        players = ", ".join(json.loads(r["players"]))
        boost = r["boost"] or "-"
        pnl = f"+${r['profit']:.2f}" if r["profit"] and r["profit"] > 0 else (f"-${abs(r['profit']):.2f}" if r["profit"] else "-")
        print(f"{r['id']:<4} {r['date']:<12} {r['picks_count']:<6} {players:<35} {boost:<14} ${r['entry']:>6.2f} ${r['payout']:>8.2f} {r['status']:<8} {pnl}")


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
    p_add.add_argument("--players", required=True, help="Comma-separated player names e.g. 'Teng,Soriano'")
    p_add.add_argument("--entry", type=float, required=True, help="Dollars wagered")
    p_add.add_argument("--payout", type=float, required=True, help="Payout if all picks win")
    p_add.add_argument("--boost", default=None, help="Boost name or 'none'")
    p_add.add_argument("--multiplier", default=None, help="e.g. '9.42x'")
    p_add.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_add.add_argument("--notes", default=None)

    p_res = sub.add_parser("result", help="Settle a slip")
    p_res.add_argument("--id", type=int, required=True)
    p_res.add_argument("--result", required=True, choices=["win", "loss"])

    p_list = sub.add_parser("list", help="List slips")
    p_list.add_argument("--all", action="store_true")

    sub.add_parser("summary", help="P&L summary")

    args = parser.parse_args()
    if args.cmd == "add":
        cmd_add(args)
    elif args.cmd == "result":
        cmd_result(args)
    elif args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "summary":
        cmd_summary(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
