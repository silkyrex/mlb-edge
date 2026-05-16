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
    # add notion_page_id column if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(bets)").fetchall()]
    if "notion_page_id" not in cols:
        conn.execute("ALTER TABLE bets ADD COLUMN notion_page_id TEXT")
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
    settled = conn.execute("SELECT * FROM bets WHERE result IS NOT NULL ORDER BY date").fetchall()
    open_bets = conn.execute("SELECT * FROM bets WHERE result IS NULL ORDER BY date").fetchall()
    conn.close()

    wins   = sum(1 for r in settled if r["result"] == "W")
    losses = sum(1 for r in settled if r["result"] == "L")
    pushes = sum(1 for r in settled if r["result"] == "P")
    total_stake = sum(r["stake"] for r in settled) if settled else 0.0
    net_pnl = sum(r["profit"] for r in settled) if settled else 0.0
    win_pct = wins / (wins + losses) * 100 if (wins + losses) else 0
    roi = net_pnl / total_stake * 100 if total_stake else 0

    by_signal: dict = {}
    for r in settled:
        sig = (r["signal"] or "no signal").split("--")[0].strip().lower()
        if sig not in by_signal:
            by_signal[sig] = {"w": 0, "l": 0, "p": 0, "profit": 0.0}
        by_signal[sig][r["result"].lower()] += 1
        by_signal[sig]["profit"] += r["profit"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M PT")
    pnl_sign = "+" if net_pnl >= 0 else ""

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

    blocks = [
        callout(f"Updated {now}  --  {len(settled)} settled  |  {len(open_bets)} open"),
        h2("Overall"),
        p(f"Record:  {wins}W - {losses}L - {pushes}P  ({win_pct:.0f}% win rate)"),
        p(f"Stake:   ${total_stake:.2f}"),
        p(f"Net P&L: {pnl_sign}${net_pnl:.2f}"),
        p(f"ROI:     {roi:+.1f}%"),
        divider(),
        h2("By Signal"),
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
    blocks.append(h2("Open Bets"))

    if open_bets:
        for r in open_bets:
            blocks.append(bullet(f"#{r['id']}  {r['date']}  {r['matchup']}  |  {r['bet_on']}  @{r['line']:+d}  ${r['stake']:.0f}"))
    else:
        blocks.append(p("None."))

    _clear_page(page_id)
    _append_blocks(page_id, blocks)
    print(f"Summary updated -> {page_id[:8]}...")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync betlog.db to Notion")
    parser.add_argument("--bet", type=int, default=None, help="Sync a single bet ID")
    parser.add_argument("--summary", action="store_true", help="Update P&L summary page")
    parser.add_argument("--and-summary", action="store_true", help="Also update summary after bet sync")
    args = parser.parse_args()

    if args.summary:
        post_summary()
    elif args.bet:
        sync_one(args.bet)
        if args.and_summary:
            post_summary()
    else:
        sync_all()


if __name__ == "__main__":
    main()
