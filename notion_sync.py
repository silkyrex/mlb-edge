"""
notion_sync.py -- Sync betlog.db to the MLB Edge Bet Log Notion database.

Usage:
  python notion_sync.py             # sync all unsynced bets + update settled ones
  python notion_sync.py --bet 3     # sync a single bet by ID
  python notion_sync.py --summary   # update the P&L summary page
  python notion_sync.py --slip 5    # sync a single slip
  python notion_sync.py --slips     # sync all slips

Requires:
  NOTION_API_TOKEN  in ~/.config/credentials/notion.env  (or NOTION_TOKEN in .env)
  The "MLB Edge Bet Log" database must be shared with the integration.

Block content (pick blocks, summary body) uses ntn CLI.
Properties (database fields) use the Notion REST API directly.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "betlog.db"
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "362437cfddcb807183ebc0b51724831c")
NOTION_VERSION = "2022-06-28"

_env_file = Path(__file__).parent / ".env"
_creds_file = Path.home() / ".config/credentials/notion.env"


def _load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_env = _load_env(_env_file)
_creds = _load_env(_creds_file)

NOTION_TOKEN = (
    os.environ.get("NOTION_TOKEN")
    or _env.get("NOTION_TOKEN")
    or _creds.get("NOTION_API_TOKEN")
    or ""
)
_NTN_TOKEN = (
    os.environ.get("NOTION_API_TOKEN")
    or _creds.get("NOTION_API_TOKEN")
    or NOTION_TOKEN
)

_SUMMARY_PAGE_ID = os.environ.get("NOTION_SUMMARY_PAGE_ID", "") or _env.get("NOTION_SUMMARY_PAGE_ID", "")


def _save_env_key(key: str, val: str) -> None:
    with open(_env_file, "a") as f:
        f.write(f"\n{key}={val}\n")


# ── ntn block helper ───────────────────────────────────────────────────────────

def _ntn_update(page_id: str, markdown: str) -> None:
    """Replace all page block content with markdown via ntn CLI."""
    env = {**os.environ, "NOTION_API_TOKEN": _NTN_TOKEN}
    subprocess.run(
        ["ntn", "pages", "update", page_id, "--content", markdown or " "],
        env=env, check=True, capture_output=True,
    )


# ── schema migration ───────────────────────────────────────────────────────────

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


# ── Notion API helpers (properties only) ──────────────────────────────────────

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


def _write_pick_blocks(page_id: str, conn: sqlite3.Connection, slip_id: int) -> None:
    picks = conn.execute(
        "SELECT * FROM slip_picks WHERE slip_id=? ORDER BY id", (slip_id,)
    ).fetchall()
    if not picks:
        return
    lines = ["## Picks"]
    for sp in picks:
        actual = f"  ->  actual: {sp['actual']}" if sp["actual"] is not None else ""
        hit_tag = "  HIT" if sp["hit"] == 1 else ("  MISS" if sp["hit"] == 0 else "")
        reason = f"  [{sp['reason']}]" if sp["reason"] else ""
        game = f"  ({sp['game']})" if sp["game"] else ""
        line = f"{sp['side']} {sp['line']}"
        lines.append(f"- {sp['player']}  |  {sp['stat']}  |  {line}{game}{actual}{hit_tag}{reason}")
    _ntn_update(page_id, "\n".join(lines))


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
        page_id = row["notion_page_id"]
        _notion_request("PATCH", f"/pages/{page_id}", {"properties": props})
        print(f"updated slip #{slip_id} ({row['status']})")
    _write_pick_blocks(page_id, conn, slip_id)
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
            page_id = row["notion_page_id"]
            _notion_request("PATCH", f"/pages/{page_id}", {"properties": props})
            print(f"  updated slip #{row['id']} ({row['status']})")
            updated += 1
        _write_pick_blocks(page_id, conn, row["id"])
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


def post_summary() -> None:
    page_id = _get_or_create_summary_page()

    conn = connect()
    settled = conn.execute("SELECT * FROM slips WHERE status IN ('win','loss') ORDER BY date").fetchall()
    open_s  = conn.execute("SELECT * FROM slips WHERE status = 'open' ORDER BY date").fetchall()
    conn.close()

    wins    = sum(1 for r in settled if r["status"] == "win")
    losses  = sum(1 for r in settled if r["status"] == "loss")
    staked  = sum(r["entry"] for r in settled) if settled else 0.0
    pnl     = sum(r["profit"] for r in settled) if settled else 0.0
    win_pct = wins / (wins + losses) * 100 if (wins + losses) else 0
    roi     = pnl / staked * 100 if staked else 0
    now     = datetime.now().strftime("%Y-%m-%d %H:%M PT")
    pnl_sign = "+" if pnl >= 0 else ""

    lines = [
        f"Updated {now} -- {len(settled)} settled | {len(open_s)} open",
        "",
        "## Overall",
        f"Record: {wins}W - {losses}L ({win_pct:.0f}% win rate)",
        f"Staked: ${staked:.2f}",
        f"Net P&L: {pnl_sign}${pnl:.2f}",
        f"ROI: {roi:+.1f}%",
        "",
        "---",
        "",
        "## Slips",
    ]
    for r in settled:
        players = ", ".join(json.loads(r["players"]))
        sign = "+" if r["profit"] >= 0 else ""
        mult = r["multiplier"] or f"{r['payout']/r['entry']:.2f}x" if r["entry"] else ""
        boost = f" [{r['boost']}]" if r["boost"] else ""
        lines.append(
            f"- {r['date']}  {players}{boost}  {mult}  ${r['entry']:.0f} -> "
            f"{'WIN' if r['status'] == 'win' else 'LOSS'}  {sign}${r['profit']:.2f}"
        )

    lines += ["", "---", "", "## Open"]
    if open_s:
        for r in open_s:
            players = ", ".join(json.loads(r["players"]))
            lines.append(f"- slip#{r['id']}  {r['date']}  {players}  ${r['entry']:.0f}")
    else:
        lines.append("None.")

    _ntn_update(page_id, "\n".join(lines))
    print(f"Summary updated -> {page_id[:8]}...")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync betlog.db to Notion")
    parser.add_argument("--bet", type=int, default=None, help="Sync a single bet ID")
    parser.add_argument("--slip", type=int, default=None, help="Sync a single slip ID")
    parser.add_argument("--slips", action="store_true", help="Sync all slips")
    parser.add_argument("--summary", action="store_true", help="Update P&L summary page")
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
