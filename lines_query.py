"""
lines_query.py -- query mlb_game_lines from picks.db

Usage:
    python lines_query.py --game "SF Giants @ Athletics"
    python lines_query.py --game "SF Giants @ Athletics" --type pitcher
    python lines_query.py --game "SF Giants @ Athletics" --stat "Strikeouts"
    python lines_query.py --game "SF Giants @ Athletics" --player "Aaron Civale"
    python lines_query.py --list-games
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "sports/dfs/picks.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_games(conn):
    rows = conn.execute(
        "SELECT DISTINCT scraped_date, game, game_time, COUNT(*) as n "
        "FROM mlb_game_lines GROUP BY scraped_date, game ORDER BY scraped_date DESC, game"
    ).fetchall()
    print(f"{'Date':<12} {'Game':<35} {'Time':<10} {'Lines':>5}")
    print("-" * 65)
    for r in rows:
        print(f"{r['scraped_date']:<12} {r['game']:<35} {r['game_time'] or '':<10} {r['n']:>5}")


def query_lines(conn, game, player_type=None, stat=None, player=None, date=None):
    sql = "SELECT * FROM mlb_game_lines WHERE game=?"
    params = [game]

    if date:
        sql += " AND scraped_date=?"
        params.append(date)
    else:
        sql += " AND scraped_date=(SELECT MAX(scraped_date) FROM mlb_game_lines WHERE game=?)"
        params.append(game)

    if player_type:
        sql += " AND player_type=?"
        params.append(player_type)
    if stat:
        sql += " AND stat LIKE ?"
        params.append(f"%{stat}%")
    if player:
        sql += " AND player LIKE ?"
        params.append(f"%{player}%")

    sql += " ORDER BY player_type, player, stat"
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print(f"No lines found for '{game}'. Run /underdog-mlb first.")
        return

    print(f"\n{rows[0]['game']} -- {rows[0]['game_time']} -- {rows[0]['scraped_date']}")
    print(f"{'Player':<25} {'Team':<5} {'Type':<8} {'Stat':<25} {'Line':>6}  {'Higher':>7}  {'Lower':>7}")
    print("-" * 90)
    for r in rows:
        line = f"{r['line']:.1f}" if r['line'] is not None else "  --"
        hi = r['higher_mult'] or "--"
        lo = r['lower_mult'] or "--"
        print(f"{r['player']:<25} {r['team'] or '':<5} {r['player_type'] or '':<8} {r['stat']:<25} {line:>6}  {hi:>7}  {lo:>7}")

    print(f"\n{len(rows)} lines")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", help="Game name, e.g. 'SF Giants @ Athletics'")
    parser.add_argument("--type", dest="player_type", choices=["pitcher", "batter", "team"])
    parser.add_argument("--stat", help="Filter by stat (partial match)")
    parser.add_argument("--player", help="Filter by player name (partial match)")
    parser.add_argument("--date", help="Specific date YYYY-MM-DD (default: most recent)")
    parser.add_argument("--list-games", action="store_true")
    args = parser.parse_args()

    conn = get_conn()

    if args.list_games:
        list_games(conn)
    elif args.game:
        query_lines(conn, args.game, args.player_type, args.stat, args.player, args.date)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
