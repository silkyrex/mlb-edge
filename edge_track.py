"""
edge_track.py -- edge performance tracker (PRESS / HOLD / DROP per edge_key)

Reads edge_performance from sliplog.db. Outputs hit rate per edge_key over
last N settled bets, with PRESS/HOLD/DROP/WAIT signal.

Usage:
    python edge_track.py           # last 10 per key (default)
    python edge_track.py --n 20    # last 20
    python edge_track.py --all     # lifetime totals
    python edge_track.py --json    # machine-readable
"""

import argparse
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "sliplog.db"

WINDOW = 10           # default rolling window
PRESS_THRESHOLD = 55  # hit rate % to PRESS
DROP_THRESHOLD  = 40  # hit rate % below which DROP
WAIT_MIN        = 5   # min settled bets to leave WAIT

EDGE_KEY_LABELS = {
    "elite_batter_fade_pitcher__hrbi_higher": "H+R+RBI Higher  (ELITE bat vs FADE pitch)",
    "fade_pitcher_po_lower":                  "PO Lower        (FADE pitcher hook risk)",
    "elite_batter_hits_higher":               "Hits Higher     (ELITE bat vs FADE pitch)",
    "elite_pitcher_fade_lineup__bk_higher":   "Batter Ks Higher(ELITE pitch vs FADE lineup)",
    "elite_power_fade_pitcher__tb_higher":    "Total Bases High (power vs FADE pitcher)",
}

SIGNAL_ORDER = {"PRESS": 0, "HOLD": 1, "DROP": 2, "WAIT": 3}


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _bar(wins: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "░" * width
    filled = round(wins / total * width)
    return "█" * filled + "░" * (width - filled)


def compute_edge_status(n_window: int = WINDOW, all_time: bool = False) -> list[dict]:
    conn = connect()

    if all_time:
        rows = conn.execute(
            """
            SELECT edge_key,
                   COUNT(*) as total,
                   SUM(hit) as wins
            FROM edge_performance
            WHERE hit IS NOT NULL
            GROUP BY edge_key
            ORDER BY edge_key
            """
        ).fetchall()
        results = []
        for r in rows:
            total = r["total"]
            wins = r["wins"] or 0
            hit_pct = round(100.0 * wins / total, 1) if total else 0.0
            signal = _signal(hit_pct, total)
            results.append({
                "edge_key": r["edge_key"],
                "total": total,
                "wins": wins,
                "hit_pct": hit_pct,
                "signal": signal,
                "window": "all-time",
            })
    else:
        # Get all edge_keys with any settled picks
        keys = conn.execute(
            "SELECT DISTINCT edge_key FROM edge_performance WHERE hit IS NOT NULL"
        ).fetchall()
        results = []
        for krow in keys:
            ek = krow["edge_key"]
            recent = conn.execute(
                """
                SELECT hit FROM edge_performance
                WHERE edge_key = ? AND hit IS NOT NULL
                ORDER BY logged_at DESC
                LIMIT ?
                """,
                (ek, n_window),
            ).fetchall()
            total = len(recent)
            wins = sum(1 for r in recent if r["hit"] == 1)
            hit_pct = round(100.0 * wins / total, 1) if total else 0.0
            signal = _signal(hit_pct, total)
            results.append({
                "edge_key": ek,
                "total": total,
                "wins": wins,
                "hit_pct": hit_pct,
                "signal": signal,
                "window": n_window,
            })

    conn.close()

    # Add WAIT rows for edge_keys that have zero settled picks
    seen_keys = {r["edge_key"] for r in results}
    for ek in EDGE_KEY_LABELS:
        if ek not in seen_keys:
            results.append({
                "edge_key": ek,
                "total": 0,
                "wins": 0,
                "hit_pct": 0.0,
                "signal": "WAIT",
                "window": n_window if not all_time else "all-time",
            })

    return sorted(results, key=lambda x: (SIGNAL_ORDER.get(x["signal"], 99), -x["hit_pct"]))


def _signal(hit_pct: float, total: int) -> str:
    if total < WAIT_MIN:
        return "WAIT"
    if hit_pct >= PRESS_THRESHOLD:
        return "PRESS"
    if hit_pct < DROP_THRESHOLD:
        return "DROP"
    return "HOLD"


def _signal_emoji(signal: str) -> str:
    return {"PRESS": "🟢 PRESS", "HOLD": "🟡 HOLD", "DROP": "🔴 DROP", "WAIT": "⚪ WAIT"}.get(signal, signal)


def print_status(results: list[dict]) -> None:
    window_label = results[0]["window"] if results else WINDOW
    print(f"\n{'─'*70}")
    print(f"  EDGE TRACKER  (rolling last {window_label})")
    print(f"{'─'*70}")
    print(f"  {'Signal':<10}  {'Edge Key / Category':<48}  {'n':>4}  {'%':>6}  Bar")
    print(f"  {'─'*9}  {'─'*48}  {'─'*4}  {'─'*6}  {'─'*10}")

    for r in results:
        label = EDGE_KEY_LABELS.get(r["edge_key"], r["edge_key"])
        sig   = _signal_emoji(r["signal"])
        total = r["total"]
        wins  = r["wins"]
        pct   = r["hit_pct"]
        bar   = _bar(wins, total)
        n_str = f"{wins}/{total}" if total else "0/0"
        pct_s = f"{pct:.1f}%" if total else "  -- "
        print(f"  {sig:<12}  {label:<48}  {n_str:>5}  {pct_s:>6}  {bar}")

    # Actionable summary
    press_keys = [r for r in results if r["signal"] == "PRESS"]
    drop_keys  = [r for r in results if r["signal"] == "DROP"]
    if press_keys:
        print(f"\n  🟢 PRESS:  {', '.join(EDGE_KEY_LABELS.get(r['edge_key'], r['edge_key']) for r in press_keys)}")
    if drop_keys:
        print(f"  🔴 DROP:   {', '.join(EDGE_KEY_LABELS.get(r['edge_key'], r['edge_key']) for r in drop_keys)}")
    if not press_keys and not drop_keys:
        print("\n  No PRESS or DROP signals — all edges in HOLD/WAIT phase.")
    print(f"{'─'*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Edge performance tracker")
    parser.add_argument("--n", type=int, default=WINDOW, help=f"Rolling window size (default: {WINDOW})")
    parser.add_argument("--all", dest="all_time", action="store_true", help="Lifetime totals (ignore window)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    results = compute_edge_status(n_window=args.n, all_time=args.all_time)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_status(results)


if __name__ == "__main__":
    main()
