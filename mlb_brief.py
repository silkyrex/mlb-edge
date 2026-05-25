"""
mlb_brief.py -- daily 9am MLB brief

Pulls today's MLB schedule, grades probable starters, flags IL returns,
ranks top matchups by edge score (K-edge + mismatch bonus), outputs
edge_track PRESS/HOLD/DROP, and posts a summary to Discord.

Usage:
    python mlb_brief.py                # print to terminal
    python mlb_brief.py --post         # post to Discord
    python mlb_brief.py --date 2026-05-16  # specific date
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import requests
from datetime import date as date_cls, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from cache_stats import compute_pitcher_cache, upsert_cache
from cache_espn import fetch_espn_pitchers, match_name as espn_match
from notion_sync import notion_request, ntn_create, NOTION_TOKEN as _NS_NOTION_TOKEN

BASE = "https://statsapi.mlb.com/api/v1"
SEASON = str(date_cls.today().year)
PICKS_DB = Path(__file__).parent / "mlb.db"
WEBHOOK_URL = os.getenv("SPORTS_WEBHOOK_URL", "")
QA_WEBHOOK = os.getenv("DISCORD_QA_WEBHOOK", "")

# ── Notion config ──────────────────────────────────────────────────────────────
_SETUP_PAGE_ID = "9d23fc7feed448a994c7543ce29a593d"
_BRIEFS_PAGE_KEY = "NOTION_BRIEFS_PAGE_ID"
_env_file = Path(__file__).parent / ".env"

def _load_env_key(key: str) -> str:
    if _env_file.exists():
        for line in _env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

def _save_env_key(key: str, val: str) -> None:
    with open(_env_file, "a") as f:
        f.write(f"\n{key}={val}\n")

_BRIEFS_PAGE_ID = os.getenv(_BRIEFS_PAGE_KEY, "") or _load_env_key(_BRIEFS_PAGE_KEY)


def _get_or_create_briefs_page() -> str:
    global _BRIEFS_PAGE_ID
    if _BRIEFS_PAGE_ID:
        return _BRIEFS_PAGE_ID
    result = notion_request("POST", "/pages", {
        "parent": {"page_id": _SETUP_PAGE_ID},
        "properties": {"title": [{"text": {"content": "Game Day Briefs"}}]},
    })
    _BRIEFS_PAGE_ID = result["id"]
    _save_env_key(_BRIEFS_PAGE_KEY, _BRIEFS_PAGE_ID)
    print(f"Created Game Day Briefs page: {_BRIEFS_PAGE_ID[:8]}...")
    return _BRIEFS_PAGE_ID


def _brief_to_markdown(brief_text: str, game_date: str) -> str:
    """Convert Discord-formatted brief text to Notion markdown."""
    out = [f"# Brief -- {game_date}", ""]
    for line in brief_text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append("")
        elif stripped.startswith("**MLB Brief"):
            out.append(f"## {stripped.replace('**', '').strip()}")
        elif stripped.startswith("**Recent IL"):
            out.append(f"### {stripped.replace('**', '').strip()}")
        elif stripped.startswith("**") and "@" in stripped:
            out.append(f"## {stripped.replace('**', '').strip()}")
        elif line.startswith("  "):
            out.append(f"- {stripped}")
        else:
            out.append(stripped)
    return "\n".join(out)


def post_to_notion(brief_text: str, game_date: str) -> None:
    if not _NS_NOTION_TOKEN:
        print("NOTION_TOKEN not set -- skipping Notion post.")
        return
    parent_id = _get_or_create_briefs_page()
    markdown = _brief_to_markdown(brief_text, game_date)
    page_id = ntn_create(parent_id, markdown)
    tag = f"{page_id[:8]}..." if page_id else "(id unavailable)"
    print(f"Brief posted to Notion -> {tag}")

ERA_ELITE = 3.00
ERA_FADE  = 4.50


def grade_era(era: float | None) -> str:
    if era is None:
        return "?"
    if era <= ERA_ELITE:
        return "ELITE"
    if era >= ERA_FADE:
        return "FADE"
    return "mid"


def _float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def get_schedule(game_date: str) -> list[dict]:
    r = requests.get(f"{BASE}/schedule", params={
        "sportId": 1,
        "date": game_date,
        "hydrate": "probablePitcher",
    }, timeout=10)
    r.raise_for_status()
    games = []
    for d in r.json().get("dates", []):
        games.extend(d.get("games", []))
    return games


def get_pitcher_stats(player_id: int) -> dict:
    r = requests.get(f"{BASE}/people/{player_id}/stats", params={
        "stats": "season",
        "group": "pitching",
        "season": SEASON,
    }, timeout=10)
    if r.status_code != 200:
        return {}
    stats = r.json().get("stats") or [{}]
    splits = stats[0].get("splits", []) if stats else []
    return splits[0].get("stat", {}) if splits else {}


def get_recent_il_returns(team_ids: list[int], game_date: str) -> list[str]:
    """Players who returned from IL in the last 7 days."""
    start = str(date_cls.fromisoformat(game_date) - timedelta(days=7))
    as_of = date_cls.fromisoformat(game_date)
    activated = []
    il_re = re.compile(r"activated .+ from the (\d+)-day injured list", re.I)

    for tid in team_ids:
        r = requests.get(f"{BASE}/transactions", params={
            "sportId": 1, "teamId": tid,
            "startDate": start, "endDate": game_date,
        }, timeout=10)
        if r.status_code != 200:
            continue
        for t in r.json().get("transactions", []):
            if t.get("typeCode") != "SC":
                continue
            desc = t.get("description", "")
            if il_re.search(desc):
                txn_date = date_cls.fromisoformat(t["date"])
                days_ago = (as_of - txn_date).days
                name = t["person"]["fullName"]
                tag = "today" if days_ago == 0 else f"{days_ago}d ago"
                activated.append(f"{name} (returned {tag})")
    return activated


def utc_to_pt(utc_str: str) -> str:
    h, m = int(utc_str[:2]), int(utc_str[3:5])
    h = (h - 7) % 24
    ampm = "am" if h < 12 else "pm"
    h12 = h if h <= 12 else h - 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{m:02d}{ampm} PT"


def _build_prescan_block(game_date: str, conn_path: Path) -> str:
    """Return a formatted top-5 prescan block for Discord. DB-first; subprocess fallback."""
    conn = sqlite3.connect(conn_path)
    rows = conn.execute(
        "SELECT game, score, reason FROM pre_scan_scores WHERE date=? ORDER BY score DESC LIMIT 5",
        (game_date,)
    ).fetchall()
    conn.close()

    if not rows:
        # Prescan hasn't run yet for this date — run it now
        try:
            script = Path(__file__).parent / "prescan.py"
            subprocess.run(
                [sys.executable, str(script), "--date", game_date, "--top", "5"],
                timeout=120, check=True, capture_output=True,
            )
        except Exception as e:
            return f"\n_Prescan unavailable for {game_date}: {e}_"
        conn = sqlite3.connect(conn_path)
        rows = conn.execute(
            "SELECT game, score, reason FROM pre_scan_scores WHERE date=? ORDER BY score DESC LIMIT 5",
            (game_date,)
        ).fetchall()
        conn.close()

    if not rows:
        return ""

    lines = [f"\n**⚾ Top {len(rows)} Matchups by Edge — {game_date}**"]
    for i, (game, score, reason) in enumerate(rows, 1):
        reason_str = reason or "—"
        lines.append(f"  {i}. {game} ({score:.2f}) — {reason_str}")
    lines.append("  _Scrape top 2-3 only via /underdog-mlb._")
    return "\n".join(lines)


def _build_edge_track_block() -> str:
    """Return a compact PRESS/HOLD/DROP edge status block for Discord."""
    try:
        from edge_track import compute_edge_status, EDGE_KEY_LABELS, _signal_emoji
        results = compute_edge_status()
        if not any(r["total"] > 0 for r in results):
            return ""
        lines = ["\n**📊 Edge Tracker**"]
        for r in results:
            label = EDGE_KEY_LABELS.get(r["edge_key"], r["edge_key"])[:40]
            sig = _signal_emoji(r["signal"])
            n_str = f"{r['wins']}/{r['total']}" if r["total"] else "0/0"
            pct = f"{r['hit_pct']:.0f}%" if r["total"] else "--"
            lines.append(f"  {sig}  {label:<40}  {n_str:>5}  {pct}")
        return "\n".join(lines)
    except Exception as e:
        return f"\n_Edge tracker unavailable: {e}_"


def build_brief(game_date: str) -> str:
    games = get_schedule(game_date)
    if not games:
        return f"No games found for {game_date}."

    lines = [f"**MLB Brief -- {game_date}**  ({len(games)} games)\n"]

    team_ids_all: list[int] = []
    game_blocks: list[str] = []
    pitchers_cached: list[tuple[str, int]] = []  # (name, player_id)

    # Pre-fetch ESPN map once for all starters
    try:
        espn_map = fetch_espn_pitchers()
    except Exception:
        espn_map = {}

    conn = sqlite3.connect(PICKS_DB)
    conn.row_factory = sqlite3.Row

    for g in games:
        teams = g["teams"]
        away_team = teams["away"]["team"]["name"]
        home_team = teams["home"]["team"]["name"]
        away_id = teams["away"]["team"]["id"]
        home_id = teams["home"]["team"]["id"]
        team_ids_all.extend([away_id, home_id])

        away_p = teams["away"].get("probablePitcher", {})
        home_p = teams["home"].get("probablePitcher", {})

        game_time = utc_to_pt(g.get("gameDate", "00:00")[11:16])

        block = [f"**{away_team} @ {home_team}**  {game_time}"]

        for label, pitcher in [("Away", away_p), ("Home", home_p)]:
            if not pitcher:
                block.append(f"  {label}: TBD")
                continue
            pid = pitcher.get("id")
            name = pitcher.get("fullName", "TBD")

            # Full cache (last 5 starts + splits + xStats) → player_recent_stats
            fip = war = None
            if pid:
                try:
                    data = compute_pitcher_cache(pid)
                    upsert_cache(conn, name, game_date, data)
                    pitchers_cached.append((name, pid))

                    # Overlay ESPN stats on the same row
                    espn = espn_match(name, espn_map)
                    if espn:
                        conn.execute("""
                            UPDATE player_recent_stats
                            SET espn_war=?, espn_fip=?, espn_k_bb=?
                            WHERE player=? AND cache_date=? AND player_type='pitcher'
                        """, (espn["espn_war"], espn["espn_fip"], espn["espn_k_bb"], name, game_date))
                        fip = espn["espn_fip"]
                        war = espn["espn_war"]

                    era = data.get("season_era")
                    whip = data.get("season_whip")
                    k9 = data.get("season_k9")
                except Exception:
                    stats = get_pitcher_stats(pid)
                    era = _float(stats.get("era"))
                    whip = _float(stats.get("whip"))
                    ip = _float(stats.get("inningsPitched")) or 0
                    ks = stats.get("strikeOuts", 0)
                    k9 = round(ks * 9 / ip, 1) if ip > 0 else None
            else:
                era = whip = k9 = None

            grade = grade_era(era)
            era_str = f"{era:.2f}" if era is not None else "?"
            whip_str = f"{whip:.2f}" if whip is not None else "?"
            k9_str = f"{k9}" if k9 is not None else "?"
            fip_str = f" FIP {fip:.2f}" if fip is not None else ""
            war_str = f" WAR {war:.1f}" if war is not None else ""
            block.append(f"  {label}: {name} [{grade}] ERA {era_str} WHIP {whip_str} K/9 {k9_str}{fip_str}{war_str}")

        game_blocks.append("\n".join(block))

    conn.commit()
    conn.close()

    lines.append("\n\n".join(game_blocks))

    # IL returns across all teams in today's slate
    il_returns = get_recent_il_returns(list(set(team_ids_all)), game_date)
    if il_returns:
        lines.append(f"\n**Recent IL Returns (last 7 days):**")
        for r in il_returns:
            lines.append(f"  {r}")

    if pitchers_cached:
        lines.append(f"\n_{len(pitchers_cached)} starters pre-cached in picks.db (ERA/FIP/WAR ready for /underdog-mlb-analyze)_")

    lines.append(f"\n_Lean signal arrives 12pm PT via Discord._")

    # Prescan: top-5 matchups by edge score
    prescan_block = _build_prescan_block(game_date, conn_path=PICKS_DB)
    if prescan_block:
        lines.append(prescan_block)

    # Edge tracker: PRESS / HOLD / DROP per edge key
    edge_block = _build_edge_track_block()
    if edge_block:
        lines.append(edge_block)

    brief_text = "\n".join(lines)

    # Capture starter grades to OB1
    try:
        ob1_url = os.getenv("OB1_MCP_URL", "http://134.199.137.81:8000/mcp?key=7iQ3-Wqv41JK60Hav2GdWHCOQZbfYrFYayj3l2TCzy0")
        import urllib.request as _ur2
        elite = [n for n, _ in pitchers_cached if any(
            g in brief_text for g in [f"{n} [ELITE]"]
        )]
        grade_lines = [l.strip() for l in brief_text.splitlines() if "[ELITE]" in l or "[FADE]" in l or "[mid]" in l]
        thought = (
            f"MLB morning brief [{game_date}]: {len(games)} games, {len(pitchers_cached)} starters cached.\n"
            + "\n".join(grade_lines[:20])
            + "\nAgent: mlb-edge morning-brief"
        )
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": "capture_thought", "arguments": {"content": thought}},
            "id": 1,
        }).encode()
        req = _ur2.Request(ob1_url, data=payload, method="POST",
                           headers={"Content-Type": "application/json",
                                    "Accept": "application/json, text/event-stream",
                                    "User-Agent": "mlb-edge/1.0"})
        _ur2.urlopen(req, timeout=15)
    except Exception:
        pass

    return brief_text


def post_to_discord(message: str):
    try:
        from discord_manager import send as _dm_send
        _dm_send("mlb_edge_alert", message)
        print("Posted to Discord.")
    except Exception as e:
        print(f"Discord post failed: {e}")


def _post_qa_status(message: str) -> None:
    try:
        from discord_manager import send as _dm_send
        _dm_send("qa_gate_status", message)
    except Exception as e:
        print(f"QA webhook post failed: {e}")


def validate_brief(brief: str) -> tuple[bool, str, bool]:
    """Return (hard_fail, reason, soft_warn).

    hard_fail=True means don't post -- output is broken.
    soft_warn=True means post with a warning prefix.
    """
    if len(brief) <= 200:
        return True, "brief too short -- likely empty output", False
    if "No games found" in brief:
        return True, "no games found for date", False
    if "Away:" not in brief:
        return True, "no game sections found in brief", False

    lines = brief.splitlines()
    missing = brief.count("[?]")
    if lines and missing / len(lines) > 0.30:
        return False, f"{missing} missing stat markers ({missing / len(lines):.0%} of lines)", True

    return False, "", False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date_cls.today() + timedelta(days=1)),
                        help="Date to brief (default: tomorrow)")
    parser.add_argument("--post", action="store_true", help="Post to Discord")
    parser.add_argument("--notion", action="store_true", help="Post to Notion Game Day Briefs")
    args = parser.parse_args()

    brief = build_brief(args.date)
    print(brief)

    hard_fail, reason, soft_warn = validate_brief(brief)
    if hard_fail:
        msg = f"[QA FAIL mlb-brief: {reason}]"
        print(msg)
        _post_qa_status(msg)
        if args.post:
            post_to_discord(msg)
        sys.exit(1)
    if soft_warn:
        warn_msg = f"[QA WARN mlb-brief: {reason}]"
        print(warn_msg)
        _post_qa_status(warn_msg)

    if args.post:
        post_to_discord(brief)
    if args.notion:
        post_to_notion(brief, args.date)


if __name__ == "__main__":
    main()
