"""
query.py -- slice mlb.db for betting research

Usage:
  python query.py hitters [--stat hits|hr|rbi|avg|ops] [--days 14] [--min-ab 30]
  python query.py pitchers [--stat era|k|ip] [--days 14] [--min-ip 10]
  python query.py player "Aaron Judge" [--days 14]
  python query.py streaks [--min 5]
  python query.py team "New York Yankees" [--days 14]
"""

import argparse
from db import connect


def hitters(stat: str, days: int, min_ab: int, limit: int):
    col_map = {
        "hits": "SUM(l.hits)",
        "hr":   "SUM(l.hr)",
        "rbi":  "SUM(l.rbi)",
        "avg":  "ROUND(CAST(SUM(l.hits) AS REAL) / NULLIF(SUM(l.ab), 0), 3)",
        "ops":  "ROUND(AVG(CASE WHEN l.ops IS NOT NULL THEN l.ops END), 3)",
    }
    col = col_map.get(stat, col_map["hits"])
    date_filter = f"AND g.date >= date('now', '-{days} days')" if days else ""
    conn = connect()
    rows = conn.execute(f"""
        SELECT p.name, p.team,
               SUM(l.ab) as ab, SUM(l.hits) as hits,
               SUM(l.hr) as hr, SUM(l.rbi) as rbi,
               {col} as sort_val
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE l.stat_type = 'hitting' {date_filter}
        GROUP BY l.player_id
        HAVING SUM(l.ab) >= {min_ab}
        ORDER BY sort_val DESC LIMIT {limit}
    """).fetchall()
    conn.close()
    period = f"last {days}d" if days else "season"
    print(f"\nTop hitters by {stat} ({period}, min {min_ab} AB)\n")
    print(f"{'Name':<25} {'Team':<25} {'AB':>4} {'H':>4} {'HR':>4} {'RBI':>4} {stat.upper():>6}")
    print("-" * 80)
    for r in rows:
        print(f"{r['name']:<25} {r['team']:<25} {r['ab']:>4} {r['hits']:>4} {r['hr']:>4} {r['rbi']:>4} {r['sort_val']:>6}")


def pitchers(stat: str, days: int, min_ip: float, limit: int):
    col_map = {
        "k":   "SUM(l.k)",
        "era":  "ROUND(SUM(l.er) * 9.0 / NULLIF(SUM(l.ip), 0), 2)",
        "ip":   "ROUND(SUM(l.ip), 1)",
    }
    col = col_map.get(stat, col_map["k"])
    order = "ASC" if stat == "era" else "DESC"
    date_filter = f"AND g.date >= date('now', '-{days} days')" if days else ""
    conn = connect()
    rows = conn.execute(f"""
        SELECT p.name, p.team,
               ROUND(SUM(l.ip), 1) as ip, SUM(l.k) as k, SUM(l.er) as er,
               ROUND(SUM(l.er) * 9.0 / NULLIF(SUM(l.ip), 0), 2) as era,
               {col} as sort_val
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE l.stat_type = 'pitching' AND l.ip IS NOT NULL {date_filter}
        GROUP BY l.player_id
        HAVING SUM(l.ip) >= {min_ip}
        ORDER BY sort_val {order} LIMIT {limit}
    """).fetchall()
    conn.close()
    period = f"last {days}d" if days else "season"
    print(f"\nTop pitchers by {stat} ({period}, min {min_ip} IP)\n")
    print(f"{'Name':<25} {'Team':<25} {'IP':>6} {'K':>4} {'ER':>4} {'ERA':>6}")
    print("-" * 75)
    for r in rows:
        print(f"{r['name']:<25} {r['team']:<25} {r['ip']:>6} {r['k']:>4} {r['er']:>4} {r['era']:>6}")


def player(name: str, days: int):
    date_filter = f"AND g.date >= date('now', '-{days} days')" if days else ""
    conn = connect()
    rows = conn.execute(f"""
        SELECT g.date, g.home_team, g.away_team,
               l.stat_type, l.ab, l.hits, l.hr, l.rbi, l.bb, l.so, l.avg, l.ops,
               l.ip, l.er, l.k, l.era
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE p.name LIKE ? {date_filter}
        ORDER BY g.date DESC
    """, (f"%{name}%",)).fetchall()
    conn.close()
    if not rows:
        print(f"No results for '{name}'")
        return
    period = f"last {days}d" if days else "season"
    print(f"\n{name} -- game log ({period})\n")
    for r in rows:
        matchup = f"{r['away_team']} @ {r['home_team']}"
        if r["stat_type"] == "hitting":
            print(f"  {r['date']}  {matchup:<45}  {r['ab']}AB {r['hits']}H {r['hr']}HR {r['rbi']}RBI {r['bb']}BB {r['so']}K  AVG:{r['avg'] or '-'} OPS:{r['ops'] or '-'}")
        else:
            print(f"  {r['date']}  {matchup:<45}  {r['ip']}IP {r['er']}ER {r['k']}K {r['bb']}BB  ERA:{r['era'] or '-'}")


def streaks(min_streak: int):
    conn = connect()
    rows = conn.execute("""
        SELECT p.name, p.team, g.date, l.hits
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE l.stat_type = 'hitting' AND l.ab > 0
        ORDER BY l.player_id, g.date
    """).fetchall()
    conn.close()
    player_games: dict = {}
    for r in rows:
        key = (r["name"], r["team"])
        player_games.setdefault(key, []).append((r["date"], r["hits"]))
    results = []
    for (name, team), games in player_games.items():
        cur = 0
        for _, hits in games:
            if (hits or 0) > 0:
                cur += 1
            else:
                cur = 0
        if cur >= min_streak:
            results.append((name, team, cur))
    results.sort(key=lambda x: -x[2])
    print(f"\nActive hitting streaks (min {min_streak} games)\n")
    print(f"{'Name':<25} {'Team':<25} {'Streak':>6}")
    print("-" * 60)
    for name, team, streak in results:
        print(f"{name:<25} {team:<25} {streak:>6}g")


def team(name: str, days: int):
    date_filter = f"AND g.date >= date('now', '-{days} days')" if days else ""
    conn = connect()
    rows = conn.execute(f"""
        SELECT g.date, g.home_team, g.away_team, g.home_score, g.away_score,
               CASE WHEN g.home_team LIKE ? THEN 'home' ELSE 'away' END as side
        FROM games g
        WHERE (g.home_team LIKE ? OR g.away_team LIKE ?) {date_filter}
        ORDER BY g.date DESC
    """, (f"%{name}%", f"%{name}%", f"%{name}%")).fetchall()
    conn.close()
    if not rows:
        print(f"No results for '{name}'")
        return
    wins = losses = runs_for = runs_against = 0
    period = f"last {days}d" if days else "season"
    print(f"\n{name} -- team results ({period})\n")
    print(f"{'Date':<12} {'Matchup':<45} {'Score':<10} {'W/L'}")
    print("-" * 75)
    for r in rows:
        if r["side"] == "home":
            rf, ra = r["home_score"] or 0, r["away_score"] or 0
        else:
            rf, ra = r["away_score"] or 0, r["home_score"] or 0
        matchup = f"{r['away_team']} @ {r['home_team']}"
        wl = "W" if rf > ra else "L"
        runs_for += rf; runs_against += ra
        wins += 1 if wl == "W" else 0
        losses += 0 if wl == "W" else 1
        print(f"{r['date']:<12} {matchup:<45} {rf}-{ra:<7}  {wl}")
    g = wins + losses
    print(f"\n  Record: {wins}-{losses}  |  RS/G: {runs_for/g:.1f}  |  RA/G: {runs_against/g:.1f}")


def main():
    parser = argparse.ArgumentParser(description="Query mlb-edge database")
    sub = parser.add_subparsers(dest="cmd")

    p_hit = sub.add_parser("hitters")
    p_hit.add_argument("--stat", default="hits", choices=["hits", "hr", "rbi", "avg", "ops"])
    p_hit.add_argument("--days", type=int, default=0)
    p_hit.add_argument("--min-ab", type=int, default=30)
    p_hit.add_argument("--limit", type=int, default=20)

    p_pit = sub.add_parser("pitchers")
    p_pit.add_argument("--stat", default="k", choices=["k", "era", "ip"])
    p_pit.add_argument("--days", type=int, default=0)
    p_pit.add_argument("--min-ip", type=float, default=10.0)
    p_pit.add_argument("--limit", type=int, default=20)

    p_pl = sub.add_parser("player")
    p_pl.add_argument("name")
    p_pl.add_argument("--days", type=int, default=0)

    p_str = sub.add_parser("streaks")
    p_str.add_argument("--min", type=int, default=5, dest="min_streak")

    p_tm = sub.add_parser("team")
    p_tm.add_argument("name")
    p_tm.add_argument("--days", type=int, default=0)

    args = parser.parse_args()
    if args.cmd == "hitters":
        hitters(args.stat, args.days, args.min_ab, args.limit)
    elif args.cmd == "pitchers":
        pitchers(args.stat, args.days, args.min_ip, args.limit)
    elif args.cmd == "player":
        player(args.name, args.days)
    elif args.cmd == "streaks":
        streaks(args.min_streak)
    elif args.cmd == "team":
        team(args.name, args.days)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
