"""
notion_sync.py -- Sync betlog.db to the MLB Edge Bet Log Notion database.

Usage:
  python notion_sync.py          # sync all unsynced bets + update settled ones
  python notion_sync.py --bet 3  # sync a single bet by ID

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
from pathlib import Path

DB_PATH = Path(__file__).parent / "betlog.db"
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "362437cfddcb807183ebc0b51724831c")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"

# Load .env if present
_env_file = Path(__file__).parent / ".env"
if _env_file.exists() and not NOTION_TOKEN:
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("NOTION_TOKEN="):
            NOTION_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
            break


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


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync betlog.db to Notion")
    parser.add_argument("--bet", type=int, default=None, help="Sync a single bet ID")
    args = parser.parse_args()
    if args.bet:
        sync_one(args.bet)
    else:
        sync_all()


if __name__ == "__main__":
    main()
