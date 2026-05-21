"""
Sync Underdog slip summary from sliplog.db → ~/second-brain/sports/context.md.
Called by sliplog.py result after every settle. Also runnable standalone.
"""
import re
import sqlite3
from pathlib import Path

SLIPLOG_DB = Path(__file__).parent / "sliplog.db"
CONTEXT_MD = Path.home() / "second-brain" / "sports" / "context.md"

BLOCK_START = "## Underdog Fantasy"
BLOCK_END   = "## Reference"


def _fetch_summary() -> dict:
    conn = sqlite3.connect(SLIPLOG_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT
            COUNT(*)                                                              AS total,
            SUM(status = 'win')                                                  AS wins,
            SUM(status = 'loss')                                                 AS losses,
            SUM(status = 'open')                                                 AS open_slips,
            ROUND(SUM(entry), 2)                                                 AS staked,
            ROUND(SUM(CASE WHEN status IN ('win','loss') THEN entry ELSE 0 END), 2) AS settled_staked,
            ROUND(SUM(CASE WHEN status = 'open'         THEN entry ELSE 0 END), 2) AS open_staked,
            ROUND(SUM(COALESCE(profit, 0)), 2)                                   AS pnl,
            MAX(CASE WHEN status IN ('win','loss') THEN date END)                AS last_date
        FROM slips
    """).fetchone()

    net_deposited = conn.execute(
        "SELECT ROUND(SUM(CASE WHEN kind='deposit' THEN amount ELSE -amount END), 2) FROM cash_txns"
    ).fetchone()[0] or 0
    conn.close()

    pnl           = row["pnl"] or 0
    settled_staked = row["settled_staked"] or 0
    roi = round(pnl / settled_staked * 100, 1) if settled_staked else 0.0
    balance = round(net_deposited + pnl - (row["open_staked"] or 0), 2)
    sign = "+" if pnl >= 0 else ""

    return {
        "total":     row["total"] or 0,
        "wins":      row["wins"] or 0,
        "losses":    row["losses"] or 0,
        "open":      row["open_slips"] or 0,
        "staked":    row["staked"] or 0,
        "pnl":       pnl,
        "sign":      sign,
        "roi":       roi,
        "balance":   balance,
        "last_date": row["last_date"] or "—",
    }


def _build_block(s: dict) -> str:
    open_tag = f" / {s['open']} open" if s["open"] else ""
    return (
        "## Underdog Fantasy\n\n"
        "**Platform:** Underdog Champions (pick'em) + Drafts (DFS + Best Ball)\n"
        "**Format:** Higher/Lower props. Standard (all hit) or Flex (1-2 misses ok).\n"
        f"**Slips:** {s['total']} placed ({s['wins']}W / {s['losses']}L{open_tag})"
        f" | Staked: ${s['staked']:.2f} | P&L: {s['sign']}${s['pnl']:.2f}"
        f" | ROI: {s['sign']}{s['roi']}% | Balance: ${s['balance']:.2f}\n"
        f"**Last activity:** {s['last_date']}\n"
        "**Source of truth:** `python ~/mlb-edge/sliplog.py summary` -- do not trust context.md count alone\n"
    )


def sync() -> None:
    if not CONTEXT_MD.exists():
        print(f"sync_sb: context.md not found at {CONTEXT_MD} -- skipped")
        return

    text = CONTEXT_MD.read_text(encoding="utf-8")
    s = _fetch_summary()
    new_block = _build_block(s)

    pattern = re.compile(
        rf"{re.escape(BLOCK_START)}.*?(?={re.escape(BLOCK_END)})",
        re.DOTALL,
    )
    if not pattern.search(text):
        print("sync_sb: Underdog Fantasy block not found in context.md -- skipped")
        return

    CONTEXT_MD.write_text(pattern.sub(new_block + "\n", text), encoding="utf-8")
    print(
        f"SB synced: {s['total']} slips ({s['wins']}W/{s['losses']}L) | "
        f"P&L: {s['sign']}${s['pnl']:.2f} | Balance: ${s['balance']:.2f}"
    )


if __name__ == "__main__":
    sync()
