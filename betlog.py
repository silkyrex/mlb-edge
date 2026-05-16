"""
betlog.py -- MLB bet tracker

Usage:
  python betlog.py add --date 2026-05-15 --matchup "PHI @ PIT" --signal "OVER lean" --bet "over 8.5" --line -115 --stake 25
  python betlog.py result 1 W
  python betlog.py result 1 L
  python betlog.py result 1 P          # push
  python betlog.py list                # all open bets
  python betlog.py list --all          # every bet
  python betlog.py list --days 14
  python betlog.py summary             # P&L by signal type
"""

import argparse
import json
import os
import sqlite3
import urllib.request
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "betlog.db"

# OB1 capture -- optional; silently skips if env vars not set
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")


_IMPROVE_SCRIPT = Path.home() / "projects" / "core" / "workloads" / "ob1-self-improve" / "improve.py"


def _notion_sync(bet_id: int, update_summary: bool = False) -> None:
    """Push a single bet to Notion in the background. Silently skips if no token."""
    import subprocess, sys
    _sync_script = Path(__file__).parent / "notion_sync.py"
    if not _sync_script.exists():
        return
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        _env = Path(__file__).parent / ".env"
        if _env.exists():
            for line in _env.read_text().splitlines():
                if line.startswith("NOTION_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if not token:
        return
    env = os.environ.copy()
    env["NOTION_TOKEN"] = token
    cmd = [sys.executable, str(_sync_script), "--bet", str(bet_id)]
    if update_summary:
        cmd.append("--and-summary")
    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _trigger_self_improve() -> None:
    """Fire ob1-self-improve sports after every settled outcome. Runs in background."""
    if not (_SUPABASE_URL and _SUPABASE_KEY and _OPENROUTER_KEY):
        return
    if not _IMPROVE_SCRIPT.exists():
        return
    import subprocess, sys
    env = os.environ.copy()
    subprocess.Popen(
        [sys.executable, str(_IMPROVE_SCRIPT), "sports"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ob1_push(content: str, metadata: dict) -> None:
    if not (_SUPABASE_URL and _SUPABASE_KEY and _OPENROUTER_KEY):
        return
    try:
        emb_req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=json.dumps({"model": "openai/text-embedding-3-small", "input": content}).encode(),
            headers={"Authorization": f"Bearer {_OPENROUTER_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(emb_req, timeout=20) as r:
            embedding = json.loads(r.read())["data"][0]["embedding"]
    except Exception:
        embedding = None

    payload = json.dumps({
        "p_content": content,
        "p_payload": {"metadata": {**metadata, "source": "betlog", "era": "live"}},
    }).encode()
    upsert_req = urllib.request.Request(
        f"{_SUPABASE_URL}/rest/v1/rpc/upsert_thought",
        data=payload,
        headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(upsert_req, timeout=15) as r:
        result = json.loads(r.read())

    if embedding and result and result.get("id"):
        urllib.request.urlopen(urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/thoughts?id=eq.{result['id']}",
            data=json.dumps({"embedding": embedding}).encode(),
            headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="PATCH",
        ), timeout=15).read()


# ── schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    matchup     TEXT NOT NULL,
    signal      TEXT,
    bet_on      TEXT NOT NULL,
    line        INTEGER NOT NULL,
    stake       REAL NOT NULL,
    result      TEXT,                  -- W / L / P / NULL (pending)
    profit      REAL,                  -- NULL until settled
    notes       TEXT,
    logged_at   TEXT DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ── payout math ────────────────────────────────────────────────────────────────

def calc_profit(stake: float, line: int, result: str) -> float:
    if result == "P":
        return 0.0
    if result == "L":
        return -stake
    # Win
    if line < 0:
        return round(stake * (100 / abs(line)), 2)
    else:
        return round(stake * (line / 100), 2)


# ── commands ───────────────────────────────────────────────────────────────────

def cmd_add(args):
    conn = connect()
    game_date = args.date or str(date.today())
    conn.execute("""
        INSERT INTO bets (date, matchup, signal, bet_on, line, stake, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (game_date, args.matchup, args.signal, args.bet, args.line, args.stake, args.notes))
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() as id").fetchone()
    bet_id = row['id']
    print(f"Logged bet #{bet_id}  |  {game_date}  {args.matchup}  |  {args.bet} @ {args.line:+d}  |  ${args.stake:.0f} stake")
    conn.close()

    content = (
        f"mlb bet placed: {args.matchup} | date={game_date} bet_id={bet_id} "
        f"bet={args.bet} line={args.line:+d} stake=${args.stake:.0f} "
        f"signal={args.signal or '--'} notes={args.notes or '--'}"
    )
    _ob1_push(content, {
        "type": "mlb_bet_placed", "matchup": args.matchup, "date": game_date,
        "bet_id": bet_id, "bet": args.bet, "signal": args.signal, "agent": "betlog",
    })
    _notion_sync(bet_id)


def cmd_result(args):
    conn = connect()
    row = conn.execute("SELECT * FROM bets WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"Bet #{args.id} not found")
        conn.close()
        return
    if row["result"]:
        print(f"Bet #{args.id} already settled: {row['result']}")
        conn.close()
        return

    result = args.result.upper()
    if result not in ("W", "L", "P"):
        print("Result must be W, L, or P")
        conn.close()
        return

    profit = calc_profit(row["stake"], row["line"], result)
    conn.execute("UPDATE bets SET result = ?, profit = ? WHERE id = ?", (result, profit, args.id))
    conn.commit()
    sign = "+" if profit >= 0 else ""
    print(f"Bet #{args.id} settled: {result}  |  {sign}${profit:.2f}  |  {row['matchup']}  {row['bet_on']}")
    conn.close()

    content = (
        f"mlb bet outcome: {row['matchup']} | date={row['date']} bet_id={args.id} "
        f"bet={row['bet_on']} line={row['line']:+d} stake=${row['stake']:.0f} "
        f"result={result} profit={sign}${profit:.2f} signal={row['signal'] or '--'}"
    )
    _ob1_push(content, {
        "type": "mlb_bet_outcome", "matchup": row["matchup"], "date": row["date"],
        "bet_id": args.id, "result": result, "profit": profit, "signal": row["signal"],
        "agent": "betlog",
    })
    _notion_sync(args.id, update_summary=True)
    _trigger_self_improve()


def cmd_list(args):
    conn = connect()
    clauses = []
    params  = []

    if not args.all:
        if args.days:
            clauses.append("date >= date('now', ?)")
            params.append(f"-{args.days} days")
        else:
            clauses.append("result IS NULL")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"""
        SELECT * FROM bets {where} ORDER BY date DESC, id DESC
    """, params).fetchall()
    conn.close()

    if not rows:
        print("No bets found.")
        return

    label = "OPEN BETS" if (not args.all and not args.days) else "BETS"
    print(f"\n{label}\n")
    print(f"{'#':>3} {'Date':<12} {'Matchup':<30} {'Bet':<20} {'Line':>5} {'Stake':>6} {'Result':>6} {'Profit':>8}")
    print("-" * 100)
    for r in rows:
        profit_str = f"+${r['profit']:.2f}" if r["profit"] and r["profit"] >= 0 else (f"-${abs(r['profit']):.2f}" if r["profit"] else "-")
        result_str = r["result"] or "open"
        print(f"{r['id']:>3} {r['date']:<12} {r['matchup']:<30} {r['bet_on']:<20} {r['line']:>+5} ${r['stake']:>5.0f} {result_str:>6} {profit_str:>8}")
        if r["signal"]:
            print(f"    signal: {r['signal']}")


def cmd_summary(args):
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM bets WHERE result IS NOT NULL ORDER BY date
    """).fetchall()
    conn.close()

    if not rows:
        print("No settled bets yet.")
        return

    total_stake  = sum(r["stake"] for r in rows)
    total_profit = sum(r["profit"] for r in rows)
    wins   = sum(1 for r in rows if r["result"] == "W")
    losses = sum(1 for r in rows if r["result"] == "L")
    pushes = sum(1 for r in rows if r["result"] == "P")
    win_pct = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    roi     = total_profit / total_stake * 100 if total_stake else 0

    print(f"\nBET LOG SUMMARY  ({len(rows)} settled)\n")
    print(f"  Record:      {wins}W - {losses}L - {pushes}P  ({win_pct:.0f}% win rate)")
    print(f"  Total stake: ${total_stake:.2f}")
    print(f"  Net P&L:     {'+'if total_profit>=0 else ''}{total_profit:.2f}")
    print(f"  ROI:         {roi:+.1f}%")

    # break down by signal type
    by_signal: dict[str, dict] = {}
    for r in rows:
        sig = r["signal"] or "no signal"
        key = sig.split("--")[0].strip().lower()
        if key not in by_signal:
            by_signal[key] = {"w": 0, "l": 0, "p": 0, "profit": 0.0}
        by_signal[key][r["result"].lower()] += 1
        by_signal[key]["profit"] += r["profit"]

    if len(by_signal) > 1:
        print(f"\n  By signal type:")
        for sig, s in sorted(by_signal.items(), key=lambda x: -x[1]["profit"]):
            total = s["w"] + s["l"]
            wp = s["w"] / total * 100 if total else 0
            sign = "+" if s["profit"] >= 0 else ""
            print(f"    {sig:<35} {s['w']}W-{s['l']}L  {wp:.0f}%  {sign}${s['profit']:.2f}")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MLB bet log")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Log a new bet")
    p_add.add_argument("--date",    default=None, help="Game date YYYY-MM-DD (default: today)")
    p_add.add_argument("--matchup", required=True, help='e.g. "PHI @ PIT"')
    p_add.add_argument("--signal",  default=None, help='Signal from matchup.py e.g. "OVER lean"')
    p_add.add_argument("--bet",     required=True, help='What you bet e.g. "over 8.5" or "Pirates ML"')
    p_add.add_argument("--line",    required=True, type=int, help="American odds e.g. -115 or +130")
    p_add.add_argument("--stake",   required=True, type=float, help="Dollars wagered")
    p_add.add_argument("--notes",   default=None)

    p_res = sub.add_parser("result", help="Settle a bet")
    p_res.add_argument("id",     type=int, help="Bet ID")
    p_res.add_argument("result", help="W, L, or P (push)")

    p_lst = sub.add_parser("list", help="List bets")
    p_lst.add_argument("--all",  action="store_true", help="Show all bets including settled")
    p_lst.add_argument("--days", type=int, default=0, help="Last N days")

    sub.add_parser("summary", help="P&L summary by signal type")

    args = parser.parse_args()
    if args.cmd == "add":       cmd_add(args)
    elif args.cmd == "result":  cmd_result(args)
    elif args.cmd == "list":    cmd_list(args)
    elif args.cmd == "summary": cmd_summary(args)
    else: parser.print_help()


if __name__ == "__main__":
    main()
