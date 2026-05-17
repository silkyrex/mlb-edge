"""
notion_sync.py -- Sync betlog.db to the MLB Edge Bet Log Notion database.

Usage:
  python notion_sync.py             # sync all unsynced bets + update settled ones
  python notion_sync.py --bet 3     # sync a single bet by ID
  python notion_sync.py --summary   # update the P&L summary page
  python notion_sync.py --bet 3 --and-summary  # sync bet + update summary

Requires:
  NOTION_TOKEN  -- Notion integration secret (notion.so/my-integrations)
  The "MLB Edge Bet Log" database must be shared with the integration.

Setup (one-time):
  1. Go to https://www.notion.so/my-integrations
  2. New integration -> name "mlb-edge" -> submit
  3. Copy the Internal Integration Secret -> add to .env as NOTION_TOKEN=secret_xxx
  4. In Notion, open MLB Edge Bet Log -> ... menu -> Connections -> add mlb-edge

Database ID: 362437cfddcb807183ebc0b51724831c
"""

import argparse
import json
import os
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "betlog.db"
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "362437cfddcb807183ebc0b51724831c")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"

_env_file = Path(__file__).parent / ".env"

# Load .env if present
def _load_env():
    env = {}
    if _env_file.exists():
        for line in _env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

_env = _load_env()
if not NOTION_TOKEN:
    NOTION_TOKEN = _env.get("NOTION_TOKEN", "")

_SUMMARY_PAGE_ID = os.environ.get("NOTION_SUMMARY_PAGE_ID", "") or _env.get("NOTION_SUMMARY_PAGE_ID", "")


def _save_env_key(key: str, val: str) -> None:
    with open(_env_file, "a") as f:
        f.write(f"\n{key}={val}\n")


# ── schema migration ───────────────────────────────────────────────────────────

MIGRATION = """
ALTER TABLE bets ADD COLUMN notion_page_id TEXT;
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(bets)").fetchall()]
    if "notion_page_id" not in cols:
        conn.execute("ALTER TABLE bets ADD COLUMN notion_page_id TEXT")
        conn.commit()
    slip_cols = [r[1] for r in conn.execute("PRAGMA table_info(slips)").fetchall()]
    if "notion_page_id" not in slip_cols:
        conn.execute("ALTER TABLE slips ADD COLUMN notion_page_id TEXT")
        conn.commit()
    return conn


# ── Notion API helpers ─────────────────────────────────────────────────────────

def _notion_request(method: str, path: str, body: dict | None = None) -> dict:
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN not set. See module docstring for setup.")
    url = f"https://api.notion.com/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _build_properties(row: sqlite3.Row) -> dict:
    props: dict = {
        "Bet": {"title": [{"text": {"content": row["bet_on"]}}]},
        "Matchup": {"rich_text": [{"text": {"content": row["matchup"] or ""}}]},
        "Line": {"number": row["line"]},
        "Stake": {"number": row["stake"]},
    }
    if row["date"]:
        props["Date"] = {"date": {"start": row["date"]}}
    if row["signal"]:
        props["Signal"] = {"rich_text": [{"text": {"content": row["signal"]}}]}
    if row["notes"]:
        props["Notes"] = {"rich_text": [{"text": {"content": row["notes"]}}]}
    if row["result"]:
        props["Result"] = {"select": {"name": row["result"]}}
    else:
        props["Result"] = {"select": {"name": "Open"}}
    if row["profit"] is not None:
        props["Profit"] = {"number": row["profit"]}
    return props


def create_page(row: sqlite3.Row) -> str:
    body = {
        "parent": {"database_id": DATABASE_ID},
        "properties": _build_properties(row),
    }
    result = _notion_request("POST", "/pages", body)
    return result["id"]


def update_page(page_id: str, row: sqlite3.Row) -> None:
    _notion_request("PATCH", f"/pages/{page_id}", {"properties": _build_properties(row)})


# ── slip sync ─────────────────────────────────────────────────────────────────

def _build_slip_properties(row: sqlite3.Row) -> dict:
    players = json.loads(row["players"]) if row["players"] else []
    label = ", ".join(players)
    boost = row["boost"] or ""
    result_map = {"win": "W", "loss": "L", "open": None}
    result = result_map.get(row["status"])
    mult = row["multiplier"] or f"{row['payout'] / row['entry']:.2f}x" if row["entry"] else ""
    signal = f"slip {row['picks_count']}-pick" + (f" [{boost}]" if boost else "")
    props: dict = {
        "Bet": {"title": [{"text": {"content": label}}]},
        "Matchup": {"rich_text": [{"text": {"content": f"Underdog slip {mult}"}}]},
        "Line": {"number": 0},
        "Stake": {"number": row["entry"]},
        "Signal": {"rich_text": [{"text": {"content": signal}}]},
    }
    if row["date"]:
        props["Date"] = {"date": {"start": row["date"]}}
    if result:
        props["Result"] = {"select": {"name": result}}
    else:
        props["Result"] = {"select": {"name": "Open"}}
    if row["profit"] is not None:
        props["Profit"] = {"number": row["profit"]}
    return props


def sync_slip(slip_id: int):
    conn = connect()
    row = conn.execute("SELECT * FROM slips WHERE id = ?", (slip_id,)).fetchone()
    if not row:
        print(f"Slip #{slip_id} not found")
        conn.close()
        return
    props = _build_slip_properties(row)
    if not row["notion_page_id"]:
        body = {"parent": {"database_id": DATABASE_ID}, "properties": props}
        result = _notion_request("POST", "/pages", body)
        page_id = result["id"]
        conn.execute("UPDATE slips SET notion_page_id = ? WHERE id = ?", (page_id, slip_id))
        conn.commit()
        print(f"created slip #{slip_id} -> {page_id[:8]}...")
    else:
        _notion_request("PATCH", f"/pages/{row['notion_page_id']}", {"properties": props})
        print(f"updated slip #{slip_id} ({row['status']})")
    conn.close()


def sync_all_slips():
    conn = connect()
    rows = conn.execute("SELECT * FROM slips ORDER BY id").fetchall()
    created = updated = 0
    for row in rows:
        props = _build_slip_properties(row)
        if not row["notion_page_id"]:
            body = {"parent": {"database_id": DATABASE_ID}, "properties": props}
            result = _notion_request("POST", "/pages", body)
            page_id = result["id"]
            conn.execute("UPDATE slips SET notion_page_id = ? WHERE id = ?", (page_id, row["id"]))
            conn.commit()
            print(f"  created slip #{row['id']} -> {page_id[:8]}...")
            created += 1
        else:
            _notion_request("PATCH", f"/pages/{row['notion_page_id']}", {"properties": props})
            print(f"  updated slip #{row['id']} ({row['status']})")
            updated += 1
    conn.close()
    print(f"slips done -- {created} created, {updated} updated")


# ── sync commands ──────────────────────────────────────────────────────────────

def sync_all():
    conn = connect()
    rows = conn.execute("SELECT * FROM bets ORDER BY id").fetchall()
    created = updated = 0
    for row in rows:
        if not row["notion_page_id"]:
            page_id = create_page(row)
            conn.execute("UPDATE bets SET notion_page_id = ? WHERE id = ?", (page_id, row["id"]))
            conn.commit()
            print(f"  created #{row['id']} -> {page_id[:8]}...")
            created += 1
        else:
            update_page(row["notion_page_id"], row)
            print(f"  updated #{row['id']} ({row['result'] or 'open'})")
            updated += 1
    conn.close()
    print(f"done -- {created} created, {updated} updated")


def sync_one(bet_id: int):
    conn = connect()
    row = conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
    if not row:
        print(f"Bet #{bet_id} not found")
        conn.close()
        return
    if not row["notion_page_id"]:
        page_id = create_page(row)
        conn.execute("UPDATE bets SET notion_page_id = ? WHERE id = ?", (page_id, bet_id))
        conn.commit()
        print(f"created #{bet_id} -> {page_id[:8]}...")
    else:
        update_page(row["notion_page_id"], row)
        print(f"updated #{bet_id} ({row['result'] or 'open'})")
    conn.close()


# ── P&L summary ───────────────────────────────────────────────────────────────

def _get_or_create_summary_page() -> str:
    global _SUMMARY_PAGE_ID
    if _SUMMARY_PAGE_ID:
        return _SUMMARY_PAGE_ID
    result = _notion_request("POST", "/pages", {
        "parent": {"page_id": "9d23fc7feed448a994c7543ce29a593d"},
        "properties": {"title": [{"text": {"content": "P&L Summary"}}]},
    })
    _SUMMARY_PAGE_ID = result["id"]
    _save_env_key("NOTION_SUMMARY_PAGE_ID", _SUMMARY_PAGE_ID)
    print(f"Created summary page: {_SUMMARY_PAGE_ID[:8]}...")
    return _SUMMARY_PAGE_ID


def _clear_page(page_id: str) -> None:
    result = _notion_request("GET", f"/blocks/{page_id}/children")
    for block in result.get("results", []):
        _notion_request("DELETE", f"/blocks/{block['id']}")


def _append_blocks(page_id: str, blocks: list) -> None:
    # Notion API max 100 blocks per request
    for i in range(0, len(blocks), 100):
        _notion_request("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i:i+100]})


def post_summary() -> None:
    page_id = _get_or_create_summary_page()

    conn = connect()
    settled_bets = conn.execute("SELECT * FROM bets WHERE result IS NOT NULL ORDER BY date").fetchall()
    open_bets    = conn.execute("SELECT * FROM bets WHERE result IS NULL ORDER BY date").fetchall()
    settled_slips = conn.execute("SELECT * FROM slips WHERE status IN ('win','loss') ORDER BY date").fetchall()
    open_slips    = conn.execute("SELECT * FROM slips WHERE status = 'open' ORDER BY date").fetchall()
    conn.close()

    # ── bets stats ──
    b_wins   = sum(1 for r in settled_bets if r["result"] == "W")
    b_losses = sum(1 for r in settled_bets if r["result"] == "L")
    b_pushes = sum(1 for r in settled_bets if r["result"] == "P")
    b_stake  = sum(r["stake"] for r in settled_bets) if settled_bets else 0.0
    b_pnl    = sum(r["profit"] for r in settled_bets) if settled_bets else 0.0

    # ── slips stats ──
    s_wins   = sum(1 for r in settled_slips if r["status"] == "win")
    s_losses = sum(1 for r in settled_slips if r["status"] == "loss")
    s_stake  = sum(r["entry"] for r in settled_slips) if settled_slips else 0.0
    s_pnl    = sum(r["profit"] for r in settled_slips) if settled_slips else 0.0

    # ── combined ──
    total_wins   = b_wins + s_wins
    total_losses = b_losses + s_losses
    total_stake  = b_stake + s_stake
    net_pnl      = b_pnl + s_pnl
    win_pct = total_wins / (total_wins + total_losses) * 100 if (total_wins + total_losses) else 0
    roi     = net_pnl / total_stake * 100 if total_stake else 0

    by_signal: dict = {}
    for r in settled_bets:
        sig = (r["signal"] or "no signal").split("--")[0].strip().lower()
        if sig not in by_signal:
            by_signal[sig] = {"w": 0, "l": 0, "p": 0, "profit": 0.0}
        by_signal[sig][r["result"].lower()] += 1
        by_signal[sig]["profit"] += r["profit"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M PT")
    pnl_sign = "+" if net_pnl >= 0 else ""
    b_sign   = "+" if b_pnl >= 0 else ""
    s_sign   = "+" if s_pnl >= 0 else ""

    def h2(t):
        return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": t}}]}}
    def p(t):
        return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": t}}]}}
    def bullet(t):
        return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": t}}]}}
    def divider():
        return {"object": "block", "type": "divider", "divider": {}}
    def callout(t, emoji="📊"):
        return {"object": "block", "type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": t}}], "icon": {"type": "emoji", "emoji": emoji}}}

    total_settled = len(settled_bets) + len(settled_slips)
    total_open    = len(open_bets) + len(open_slips)

    blocks = [
        callout(f"Updated {now}  --  {total_settled} settled  |  {total_open} open"),
        h2("Overall (Bets + Slips)"),
        p(f"Record:  {total_wins}W - {total_losses}L  ({win_pct:.0f}% win rate)"),
        p(f"Staked:  ${total_stake:.2f}"),
        p(f"Net P&L: {pnl_sign}${net_pnl:.2f}"),
        p(f"ROI:     {roi:+.1f}%"),
        divider(),
        h2("Bets"),
        p(f"Record:  {b_wins}W - {b_losses}L - {b_pushes}P"),
        p(f"Staked:  ${b_stake:.2f}  |  P&L: {b_sign}${b_pnl:.2f}"),
        h2("Underdog Slips"),
        p(f"Record:  {s_wins}W - {s_losses}L"),
        p(f"Staked:  ${s_stake:.2f}  |  P&L: {s_sign}${s_pnl:.2f}"),
        divider(),
        h2("Bets by Signal"),
    ]

    if by_signal:
        for sig, s in sorted(by_signal.items(), key=lambda x: -x[1]["profit"]):
            total = s["w"] + s["l"]
            wp = s["w"] / total * 100 if total else 0
            sign = "+" if s["profit"] >= 0 else ""
            blocks.append(bullet(f"{sig:<45} {s['w']}W-{s['l']}L  {wp:.0f}%  {sign}${s['profit']:.2f}"))
    else:
        blocks.append(p("No settled bets yet."))

    blocks.append(divider())
    blocks.append(h2("Open"))

    if open_bets:
        blocks.append(p("Bets:"))
        for r in open_bets:
            blocks.append(bullet(f"#{r['id']}  {r['date']}  {r['matchup']}  |  {r['bet_on']}  @{r['line']:+d}  ${r['stake']:.0f}"))
    if open_slips:
        blocks.append(p("Slips:"))
        for r in open_slips:
            players = ", ".join(json.loads(r["players"]))
            blocks.append(bullet(f"slip#{r['id']}  {r['date']}  {players}  ${r['entry']:.0f}"))
    if not open_bets and not open_slips:
        blocks.append(p("None."))

    _clear_page(page_id)
    _append_blocks(page_id, blocks)
    print(f"Summary updated -> {page_id[:8]}...")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync betlog.db to Notion")
    parser.add_argument("--bet", type=int, default=None, help="Sync a single bet ID")
    parser.add_argument("--slip", type=int, default=None, help="Sync a single slip ID")
    parser.add_argument("--slips", action="store_true", help="Sync all slips")
    parser.add_argument("--summary", action="store_true", help="Update P&L summary page (bets + slips)")
    parser.add_argument("--and-summary", action="store_true", help="Also update summary after sync")
    args = parser.parse_args()

    if args.summary:
        post_summary()
    elif args.slip:
        sync_slip(args.slip)
        if args.and_summary:
            post_summary()
    elif args.slips:
        sync_all_slips()
        if args.and_summary:
            post_summary()
    elif args.bet:
        sync_one(args.bet)
        if args.and_summary:
            post_summary()
    else:
        sync_all()
        sync_all_slips()


if __name__ == "__main__":
    main()
