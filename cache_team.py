"""
cache_team.py -- cache team-level stats and venue info for a game

Pulls: bullpen ERA/WHIP/K9, team offense OPS/K%, venue roof type and dimensions.
Run after /underdog-mlb, before /underdog-mlb-analyze.

Usage:
    python cache_team.py --game "SF Giants @ Athletics"
    python cache_team.py --game "SF Giants @ Athletics" --query
"""

import argparse
import requests
import sqlite3
from datetime import date as date_cls
from pathlib import Path

PICKS_DB = Path.home() / "sports/dfs/picks.db"
BASE = "https://statsapi.mlb.com/api/v1"
SEASON = str(date_cls.today().year)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(PICKS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS team_game_stats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_date      TEXT NOT NULL,
            game            TEXT NOT NULL,
            team_name       TEXT NOT NULL,
            team_id         INTEGER,
            side            TEXT,           -- 'home' or 'away'
            bullpen_era     REAL,
            bullpen_whip    REAL,
            bullpen_k9      REAL,
            starter_era     REAL,
            team_avg        REAL,
            team_ops        REAL,
            team_k_pct      REAL,
            venue_name      TEXT,
            venue_roof      TEXT,
            venue_left      INTEGER,
            venue_center    INTEGER,
            venue_right     INTEGER,
            UNIQUE(cache_date, game, team_name)
        );
        CREATE INDEX IF NOT EXISTS idx_tgs_game ON team_game_stats(game, cache_date);
    """)
    conn.commit()


def _float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def find_game_info(game_name: str, game_date: str) -> dict:
    """Return {game_pk, away_id, away_name, home_id, home_name, venue_id}."""
    parts = game_name.lower().split(" @ ")
    if len(parts) != 2:
        return {}
    away_kw = [w for w in parts[0].split() if len(w) > 2]
    home_kw = [w for w in parts[1].split() if len(w) > 2]

    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "date": game_date}, timeout=10)
    r.raise_for_status()
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            teams = g["teams"]
            away_name = teams["away"]["team"]["name"].lower()
            home_name = teams["home"]["team"]["name"].lower()
            if any(w in away_name for w in away_kw) and any(w in home_name for w in home_kw):
                return {
                    "game_pk": g["gamePk"],
                    "away_id": teams["away"]["team"]["id"],
                    "away_name": teams["away"]["team"]["name"],
                    "home_id": teams["home"]["team"]["id"],
                    "home_name": teams["home"]["team"]["name"],
                    "venue_id": g.get("venue", {}).get("id"),
                    "venue_name": g.get("venue", {}).get("name"),
                }
    return {}


def fetch_team_pitching_splits(team_id: int) -> dict:
    """Returns {starter_era, bullpen_era, bullpen_whip, bullpen_k9}."""
    r = requests.get(f"{BASE}/teams/{team_id}/stats", params={
        "stats": "statSplits",
        "group": "pitching",
        "season": SEASON,
        "sitCodes": "sp,rp",
    }, timeout=10)
    result = {}
    if r.status_code != 200:
        return result
    for stat in r.json().get("stats", []):
        for split in stat.get("splits", []):
            code = split.get("split", {}).get("code", "")
            s = split.get("stat", {})
            if code == "sp":
                result["starter_era"] = _float(s.get("era"))
            elif code == "rp":
                result["bullpen_era"] = _float(s.get("era"))
                result["bullpen_whip"] = _float(s.get("whip"))
                ip = _float(s.get("inningsPitched")) or 0
                ks = s.get("strikeOuts", 0)
                result["bullpen_k9"] = round(ks * 9 / ip, 2) if ip > 0 else None
    return result


def fetch_team_offense(team_id: int) -> dict:
    """Returns {team_avg, team_ops, team_k_pct}."""
    r = requests.get(f"{BASE}/teams/{team_id}/stats", params={
        "stats": "season",
        "group": "hitting",
        "season": SEASON,
    }, timeout=10)
    if r.status_code != 200:
        return {}
    splits = r.json().get("stats", [{}])[0].get("splits", [])
    if not splits:
        return {}
    s = splits[0].get("stat", {})
    pa = s.get("plateAppearances", 0)
    so = s.get("strikeOuts", 0)
    k_pct = round(so / pa * 100, 1) if pa > 0 else None
    return {
        "team_avg": _float(s.get("avg")),
        "team_ops": _float(s.get("ops")),
        "team_k_pct": k_pct,
    }


def fetch_venue_info(venue_id: int) -> dict:
    if not venue_id:
        return {}
    r = requests.get(f"{BASE}/venues/{venue_id}", params={"hydrate": "fieldInfo"}, timeout=10)
    if r.status_code != 200:
        return {}
    venues = r.json().get("venues", [])
    if not venues:
        return {}
    fi = venues[0].get("fieldInfo", {})
    return {
        "venue_roof": fi.get("roofType"),
        "venue_left": fi.get("leftLine"),
        "venue_center": fi.get("center"),
        "venue_right": fi.get("rightLine"),
    }


def upsert_team(conn: sqlite3.Connection, cache_date: str, game: str,
                team_name: str, team_id: int, side: str, data: dict):
    fields = [
        "cache_date", "game", "team_name", "team_id", "side",
        "bullpen_era", "bullpen_whip", "bullpen_k9", "starter_era",
        "team_avg", "team_ops", "team_k_pct",
        "venue_name", "venue_roof", "venue_left", "venue_center", "venue_right",
    ]
    values = [
        cache_date, game, team_name, team_id, side,
        data.get("bullpen_era"), data.get("bullpen_whip"), data.get("bullpen_k9"),
        data.get("starter_era"), data.get("team_avg"), data.get("team_ops"),
        data.get("team_k_pct"), data.get("venue_name"), data.get("venue_roof"),
        data.get("venue_left"), data.get("venue_center"), data.get("venue_right"),
    ]
    placeholders = ",".join("?" * len(fields))
    updates = ", ".join(f"{f}=excluded.{f}" for f in fields if f not in ("cache_date", "game", "team_name"))
    conn.execute(
        f"INSERT INTO team_game_stats ({','.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(cache_date, game, team_name) DO UPDATE SET {updates}",
        values,
    )


def cache_game(game_name: str, game_date: str):
    conn = get_conn()
    ensure_table(conn)

    info = find_game_info(game_name, game_date)
    if not info:
        print(f"Game not found: {game_name} on {game_date}")
        conn.close()
        return

    print(f"Game PK: {info['game_pk']} | {info['away_name']} @ {info['home_name']}")
    print(f"Venue: {info.get('venue_name', '?')} (ID {info.get('venue_id')})")

    venue = fetch_venue_info(info.get("venue_id"))
    print(f"  Roof: {venue.get('venue_roof','?')}  Dims: L{venue.get('venue_left','?')}-C{venue.get('venue_center','?')}-R{venue.get('venue_right','?')}")

    for side, team_id, team_name in [
        ("away", info["away_id"], info["away_name"]),
        ("home", info["home_id"], info["home_name"]),
    ]:
        print(f"\n{team_name} ({side.upper()})")
        pitching = fetch_team_pitching_splits(team_id)
        offense  = fetch_team_offense(team_id)

        print(f"  Bullpen: ERA={pitching.get('bullpen_era','?')} WHIP={pitching.get('bullpen_whip','?')} K/9={pitching.get('bullpen_k9','?')}")
        print(f"  Rotation ERA: {pitching.get('starter_era','?')}")
        print(f"  Offense: AVG={offense.get('team_avg','?')} OPS={offense.get('team_ops','?')} K%={offense.get('team_k_pct','?')}%")

        data = {**pitching, **offense, **venue, "venue_name": info.get("venue_name")}
        upsert_team(conn, game_date, game_name, team_name, team_id, side, data)

    conn.commit()
    conn.close()
    print(f'\nCached. Query: python cache_team.py --game "{game_name}" --query')


def query_cache(game_name: str, game_date: str):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM team_game_stats
        WHERE game=? AND cache_date=?
        ORDER BY side DESC
    """, (game_name, game_date)).fetchall()
    conn.close()

    if not rows:
        print(f"No team data cached for '{game_name}' on {game_date}")
        return

    r0 = rows[0]
    print(f"\nTEAM STATS -- {game_name} -- {game_date}")
    print(f"Venue: {r0['venue_name']}  Roof: {r0['venue_roof']}  "
          f"Dims: L{r0['venue_left']}-C{r0['venue_center']}-R{r0['venue_right']}\n")

    print(f"{'Team':<28} {'Side':<5} {'Bull ERA':>8} {'Bull K/9':>8} {'Rot ERA':>7} {'OPS':>6} {'K%':>5}")
    print("-" * 70)
    for r in rows:
        print(f"{r['team_name']:<28} {r['side']:<5} "
              f"{r['bullpen_era'] or '--':>8} {r['bullpen_k9'] or '--':>8} "
              f"{r['starter_era'] or '--':>7} {r['team_ops'] or '--':>6} "
              f"{str(r['team_k_pct'])+'%' if r['team_k_pct'] else '--':>5}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--date", default=str(date_cls.today()))
    parser.add_argument("--query", action="store_true")
    args = parser.parse_args()

    if args.query:
        query_cache(args.game, args.date)
    else:
        cache_game(args.game, args.date)


if __name__ == "__main__":
    main()
