"""
cache_news.py -- cache injury status and IL transactions for game players

Pulls official IL data from MLB Stats API. Flags:
  - Players currently on IL (skip or discount pick)
  - Players who just returned from IL within last 7 days (rehab rust)
  - Players activated today (first game back)

Usage:
    python cache_news.py --game "SF Giants @ Athletics"             # requires /underdog-mlb first
    python cache_news.py --game "SF Giants @ Athletics" --roster    # uses team rosters, no scrape needed
    python cache_news.py --game "SF Giants @ Athletics" --date 2026-05-16 --roster  # tomorrow's game
    python cache_news.py --game "SF Giants @ Athletics" --query
"""

import argparse
import re
import requests
import sqlite3
from datetime import date as date_cls, timedelta
from pathlib import Path
from dotenv import load_dotenv

from ob1 import ob1_push as _ob1_push

load_dotenv(Path(__file__).parent / ".env")
BASE = "https://statsapi.mlb.com/api/v1"
SEASON = str(date_cls.today().year)
PICKS_DB = Path(__file__).parent / "mlb.db"

IL_PLACED_RE = re.compile(r"placed .+ on the (\d+)-day injured list", re.I)
IL_TRANSFERRED_RE = re.compile(r"transferred .+ to the (\d+)-day injured list", re.I)
IL_ACTIVATED_RE = re.compile(r"activated .+ from the (\d+)-day injured list", re.I)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(PICKS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS player_news (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            news_date   TEXT NOT NULL,
            player      TEXT NOT NULL,
            team        TEXT,
            status      TEXT,
            note        TEXT,
            source      TEXT DEFAULT 'mlb-api',
            UNIQUE(news_date, player)
        );
        CREATE INDEX IF NOT EXISTS idx_news_player ON player_news(player, news_date);
        CREATE INDEX IF NOT EXISTS idx_news_date ON player_news(news_date);
    """)
    conn.commit()


def find_game_pk_and_teams(game_name: str, game_date: str) -> tuple[int | None, list[int]]:
    parts = game_name.lower().split(" @ ")
    if len(parts) != 2:
        return None, []
    away_kw, home_kw = parts
    away_words = [w for w in away_kw.split() if len(w) > 2]
    home_words = [w for w in home_kw.split() if len(w) > 2]

    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "date": game_date}, timeout=10)
    r.raise_for_status()
    for d in r.json().get("dates", []):
        for game in d.get("games", []):
            teams = game["teams"]
            away_name = teams["away"]["team"]["name"].lower()
            home_name = teams["home"]["team"]["name"].lower()
            if any(w in away_name for w in away_words) and any(w in home_name for w in home_words):
                team_ids = [
                    teams["away"]["team"]["id"],
                    teams["home"]["team"]["id"],
                ]
                return game["gamePk"], team_ids
    return None, []


def fetch_transactions(team_id: int, start: str, end: str) -> list[dict]:
    r = requests.get(f"{BASE}/transactions", params={
        "sportId": 1,
        "teamId": team_id,
        "startDate": start,
        "endDate": end,
    }, timeout=10)
    if r.status_code != 200:
        return []
    return [t for t in r.json().get("transactions", []) if t.get("typeCode") == "SC"]


def classify_transaction(desc: str) -> tuple[str | None, str | None]:
    """Returns (status, il_days) or (None, None) if not IL-related."""
    m = IL_PLACED_RE.search(desc) or IL_TRANSFERRED_RE.search(desc)
    if m:
        return "placed", m.group(1)
    m = IL_ACTIVATED_RE.search(desc)
    if m:
        return "activated", m.group(1)
    return None, None


def build_il_status(player_name: str, transactions: list[dict], as_of: date_cls) -> dict | None:
    """
    Find the most recent IL-related transaction for this player.
    Returns status dict or None if no IL history.
    """
    player_txns = [
        t for t in transactions
        if t["person"]["fullName"].lower() == player_name.lower()
    ]
    player_txns.sort(key=lambda t: t["date"], reverse=True)

    for txn in player_txns:
        action, il_days = classify_transaction(txn.get("description", ""))
        if action is None:
            continue

        txn_date = date_cls.fromisoformat(txn["date"])
        days_ago = (as_of - txn_date).days

        if action == "placed":
            return {
                "status": f"IL-{il_days}",
                "note": txn["description"],
            }
        elif action == "activated":
            if days_ago == 0:
                note = f"Activated today from {il_days}-day IL. First game back -- expect rust."
                return {"status": "IL-return-today", "note": note}
            elif days_ago <= 7:
                note = f"Returned from {il_days}-day IL {days_ago}d ago ({txn_date}). May still be rusty."
                return {"status": f"IL-return-{days_ago}d", "note": note}
            else:
                return {"status": "active", "note": f"Returned from IL {days_ago}d ago -- cleared."}

    return None


def upsert_news(conn: sqlite3.Connection, player: str, team: str, news_date: str, status: str, note: str, source: str = "mlb-api"):
    conn.execute("""
        INSERT INTO player_news (news_date, player, team, status, note, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(news_date, player) DO UPDATE SET
            status=excluded.status,
            note=excluded.note,
            source=excluded.source
    """, (news_date, player, team, status, note, source))


def get_roster_players(team_ids: list[int]) -> list[dict]:
    """Return list of {player, team} dicts from active rosters."""
    players: list[dict] = []
    for tid in team_ids:
        r = requests.get(f"{BASE}/teams/{tid}/roster", params={"season": SEASON}, timeout=10)
        if r.status_code != 200:
            continue
        team_name = ""
        for entry in r.json().get("roster", []):
            person = entry.get("person", {})
            name = person.get("fullName", "")
            if not team_name:
                team_name = entry.get("team", {}).get("abbreviation", "")
            players.append({"player": name, "team": team_name, "player_type": entry.get("position", {}).get("type", "")})
    return players


def cache_game(game_name: str, game_date: str, use_roster: bool = False):
    conn = get_conn()
    ensure_table(conn)

    game_pk, team_ids = find_game_pk_and_teams(game_name, game_date)
    if not team_ids:
        print(f"ERROR: Could not find team IDs for '{game_name}' on {game_date}")
        conn.close()
        return

    print(f"Game PK: {game_pk} | Teams: {team_ids}")

    if use_roster:
        roster = get_roster_players(team_ids)
        rows = roster
        print(f"Using roster mode: {len(rows)} players from active rosters")
    else:
        rows = conn.execute("""
            SELECT DISTINCT player, team, player_type
            FROM mlb_game_lines
            WHERE game=? AND scraped_date=? AND player_type IN ('pitcher', 'batter')
            ORDER BY player_type DESC, player
        """, (game_name, game_date)).fetchall()

        if not rows:
            print(f"No lines found for '{game_name}' on {game_date}.")
            print("Tip: use --roster to cache from team rosters without needing /underdog-mlb.")
            conn.close()
            return

    # Fetch 60 days of transactions to catch long-term IL placements
    start = str(date_cls.fromisoformat(game_date) - timedelta(days=60))
    all_txns: list[dict] = []
    for tid in team_ids:
        txns = fetch_transactions(tid, start, game_date)
        all_txns.extend(txns)
        print(f"  Team {tid}: {len(txns)} SC transactions since {start}")

    as_of = date_cls.fromisoformat(game_date)
    flags: list[tuple[str, str, str, str]] = []  # (player, team, status, note)

    for row in rows:
        player = row["player"]
        team = row["team"] or ""
        il_data = build_il_status(player, all_txns, as_of)

        if il_data:
            status = il_data["status"]
            note = il_data["note"]
        else:
            status = "active"
            note = "No IL activity in last 60 days."

        upsert_news(conn, player, team, game_date, status, note)
        flags.append((player, team, status, note))

    conn.commit()
    conn.close()

    print(f"\nINJURY STATUS -- {game_name}\n")
    flagged = [(p, t, s, n) for p, t, s, n in flags if s != "active"]
    clean = [(p, t, s, n) for p, t, s, n in flags if s == "active"]

    if flagged:
        print("FLAGGED:")
        for player, team, status, note in sorted(flagged, key=lambda x: x[0]):
            print(f"  [{status}] {player} ({team})")
            print(f"    {note[:100]}")
    else:
        print("No IL flags.")

    print(f"\nCLEAN ({len(clean)} players): {', '.join(p for p, *_ in clean)}")
    print(f'\nQuery: python cache_news.py --game "{game_name}" --query')

    for player, team, status, note in flagged:
        _ob1_push(
            f"mlb injury flag: {player} ({team}) | date={game_date} game={game_name} "
            f"status={status} note={note[:120]}",
            {"type": "mlb_injury_flag", "player": player, "team": team,
             "status": status, "date": game_date, "game": game_name, "agent": "cache_news"},
        )


def query_cache(game_name: str, game_date: str):
    conn = get_conn()

    rows = conn.execute("""
        SELECT DISTINCT l.player, l.team, l.player_type,
               n.status, n.note, n.source, n.news_date
        FROM mlb_game_lines l
        LEFT JOIN player_news n ON n.player=l.player AND n.news_date=?
        WHERE l.game=? AND l.scraped_date=? AND l.player_type IN ('pitcher', 'batter')
        ORDER BY l.player_type DESC, l.player
    """, (game_date, game_name, game_date)).fetchall()

    conn.close()

    print(f"\nINJURY CACHE -- {game_name} -- {game_date}\n")

    flagged = [r for r in rows if r["status"] and r["status"] != "active"]
    clean = [r for r in rows if r["status"] == "active"]
    uncached = [r for r in rows if not r["status"]]

    if flagged:
        print("FLAGGED:")
        for r in sorted(flagged, key=lambda x: x["player"]):
            print(f"  [{r['status']}] {r['player']} ({r['team']}) [{r['source']}]")
            if r["note"]:
                print(f"    {r['note'][:120]}")
        print()

    if uncached:
        print(f"NOT CACHED ({len(uncached)}): {', '.join(r['player'] for r in uncached)}")
        print()

    print(f"CLEAN ({len(clean)}): {', '.join(r['player'] for r in clean)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--date", default=str(date_cls.today()))
    parser.add_argument("--query", action="store_true")
    parser.add_argument("--roster", action="store_true", help="Use team rosters instead of mlb_game_lines (no scrape needed)")
    args = parser.parse_args()

    if args.query:
        query_cache(args.game, args.date)
    else:
        cache_game(args.game, args.date, use_roster=args.roster)


if __name__ == "__main__":
    main()
