"""
fetch.py -- ingest MLB games and player stats from MLB Stats API

Usage:
  python fetch.py --date 2026-05-15
  python fetch.py --date 2026-05-15 --team NYY
  python fetch.py --range 2026-05-01 2026-05-15
"""

import argparse
import requests
from db import connect, init

BASE = "https://statsapi.mlb.com/api/v1"


def get_schedule(date: str) -> list[dict]:
    r = requests.get(f"{BASE}/schedule", params={
        "sportId": 1,
        "date": date,
        "hydrate": "linescore"
    })
    r.raise_for_status()
    return r.json().get("dates", [])


def get_boxscore(game_pk: int) -> dict:
    r = requests.get(f"{BASE}/game/{game_pk}/boxscore")
    r.raise_for_status()
    return r.json()


def upsert_game(conn, game: dict):
    teams = game.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    conn.execute("""
        INSERT INTO games (game_pk, date, home_team, away_team, home_score, away_score, status, venue)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_pk) DO UPDATE SET
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            status     = excluded.status
    """, (
        game["gamePk"],
        game["gameDate"][:10],
        home.get("team", {}).get("name", ""),
        away.get("team", {}).get("name", ""),
        home.get("score"),
        away.get("score"),
        game.get("status", {}).get("detailedState", ""),
        game.get("venue", {}).get("name", ""),
    ))


def upsert_player(conn, player_id: int, name: str, position: str, team: str):
    conn.execute("""
        INSERT INTO players (player_id, name, position, team)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            team = excluded.team,
            updated_at = datetime('now')
    """, (player_id, name, position, team))


def upsert_hitting(conn, game_pk: int, player_id: int, team: str, s: dict):
    conn.execute("""
        INSERT INTO player_game_logs
            (game_pk, player_id, team, stat_type, ab, hits, rbi, hr, bb, so, avg, ops)
        VALUES (?, ?, ?, 'hitting', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_pk, player_id, stat_type) DO UPDATE SET
            ab=excluded.ab, hits=excluded.hits, rbi=excluded.rbi,
            hr=excluded.hr, bb=excluded.bb, so=excluded.so,
            avg=excluded.avg, ops=excluded.ops
    """, (
        game_pk, player_id, team,
        s.get("atBats"), s.get("hits"), s.get("rbi"),
        s.get("homeRuns"), s.get("baseOnBalls"), s.get("strikeOuts"),
        _float(s.get("avg")), _float(s.get("ops")),
    ))


def upsert_pitching(conn, game_pk: int, player_id: int, team: str, s: dict):
    conn.execute("""
        INSERT INTO player_game_logs
            (game_pk, player_id, team, stat_type, ip, er, k, era, bb, so)
        VALUES (?, ?, ?, 'pitching', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_pk, player_id, stat_type) DO UPDATE SET
            ip=excluded.ip, er=excluded.er, k=excluded.k,
            era=excluded.era, bb=excluded.bb, so=excluded.so
    """, (
        game_pk, player_id, team,
        _float(s.get("inningsPitched")), s.get("earnedRuns"),
        s.get("strikeOuts"), _float(s.get("era")),
        s.get("baseOnBalls"), s.get("strikeOuts"),
    ))


def _float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def ingest_date(date: str, team_filter: str | None = None):
    init()
    conn = connect()
    dates = get_schedule(date)

    games_ingested = 0
    players_ingested = 0

    for d in dates:
        for game in d.get("games", []):
            game_pk = game["gamePk"]
            status = game.get("status", {}).get("detailedState", "")

            teams = game.get("teams", {})
            home_name = teams.get("home", {}).get("team", {}).get("name", "")
            away_name = teams.get("away", {}).get("team", {}).get("name", "")

            if team_filter and team_filter.lower() not in (home_name + away_name).lower():
                continue

            if status not in ("Final", "Game Over"):
                print(f"  skip {game_pk} ({home_name} vs {away_name}): {status}")
                continue

            upsert_game(conn, game)
            games_ingested += 1

            box = get_boxscore(game_pk)
            for side in ("home", "away"):
                side_data = box.get("teams", {}).get(side, {})
                team_name = side_data.get("team", {}).get("name", "")
                for pid, pdata in side_data.get("players", {}).items():
                    info = pdata.get("person", {})
                    pos = pdata.get("position", {}).get("abbreviation", "")
                    player_id = info.get("id")
                    name = info.get("fullName", "")
                    if not player_id:
                        continue
                    upsert_player(conn, player_id, name, pos, team_name)
                    players_ingested += 1

                    stats = pdata.get("stats", {})
                    if stats.get("batting"):
                        upsert_hitting(conn, game_pk, player_id, team_name, stats["batting"])
                    if stats.get("pitching"):
                        upsert_pitching(conn, game_pk, player_id, team_name, stats["pitching"])

    conn.commit()
    conn.close()
    print(f"Done -- {games_ingested} games, {players_ingested} players for {date}")


def main():
    parser = argparse.ArgumentParser(description="Ingest MLB game + player data")
    sub = parser.add_subparsers(dest="cmd")

    p_date = sub.add_parser("date", help="Ingest a single date")
    p_date.add_argument("date", help="YYYY-MM-DD")
    p_date.add_argument("--team", help="Filter by team name substring")

    p_range = sub.add_parser("range", help="Ingest a date range")
    p_range.add_argument("start", help="YYYY-MM-DD")
    p_range.add_argument("end", help="YYYY-MM-DD")
    p_range.add_argument("--team", help="Filter by team name substring")

    args = parser.parse_args()

    if args.cmd == "date":
        ingest_date(args.date, args.team)
    elif args.cmd == "range":
        from datetime import date, timedelta
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        d = start
        while d <= end:
            print(f"-- {d}")
            ingest_date(str(d), args.team)
            d += timedelta(days=1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
