"""
query.py -- slice mlb.db for betting research

Usage:
  python query.py hitters [--stat hits|hr|rbi|avg|ops|krate] [--days N] [--min-ab 30]
  python query.py pitchers [--stat era|k|ip] [--days N] [--min-ip 10]
  python query.py player "Aaron Judge" [--days N] [--last5]
  python query.py splits "Riley Greene"
  python query.py streaks [--min 5]
  python query.py totals [--team "Yankees"] [--days N] [--line 8.5]
  python query.py team "New York Yankees" [--days N]
"""

import argparse
from db import connect

# Venues known to inflate or suppress run scoring
HITTER_PARKS  = {"Coors Field"}
PITCHER_PARKS = {"Petco Park", "Oracle Park", "Dodger Stadium", "T-Mobile Park"}


def park_flag(venue: str) -> str:
    if venue in HITTER_PARKS:
        return "HITTER PARK"
    if venue in PITCHER_PARKS:
        return "PITCHER PARK"
    return ""


def hitters(stat: str, days: int, min_ab: int, limit: int):
    col_map = {
        "hits":  "SUM(l.hits)",
        "hr":    "SUM(l.hr)",
        "rbi":   "SUM(l.rbi)",
        "avg":   "ROUND(CAST(SUM(l.hits) AS REAL) / NULLIF(SUM(l.ab), 0), 3)",
        "ops":   "ROUND(AVG(CASE WHEN l.ops IS NOT NULL THEN l.ops END), 3)",
        "krate": "ROUND(CAST(SUM(l.so) AS REAL) / NULLIF(SUM(l.ab), 0), 3)",
    }
    col = col_map.get(stat, col_map["hits"])
    asc = stat == "krate"  # lower K% is better for hitters; sort ascending to show worst contact first
    order = "ASC" if asc else "DESC"
    date_filter = f"AND g.date >= date('now', '-{days} days')" if days else ""

    conn = connect()
    rows = conn.execute(f"""
        SELECT p.name, p.team,
               SUM(l.ab) as ab, SUM(l.hits) as hits,
               SUM(l.hr) as hr, SUM(l.rbi) as rbi,
               SUM(l.so) as so,
               ROUND(CAST(SUM(l.so) AS REAL) / NULLIF(SUM(l.ab), 0), 3) as krate,
               {col} as sort_val
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE l.stat_type = 'hitting' {date_filter}
        GROUP BY l.player_id
        HAVING SUM(l.ab) >= {min_ab}
        ORDER BY sort_val {order}
        LIMIT {limit}
    """).fetchall()
    conn.close()

    period = f"last {days}d" if days else "season"
    label  = "K% (fade for hit props)" if stat == "krate" else stat.upper()
    print(f"\nTop hitters by {label} ({period}, min {min_ab} AB)\n")
    print(f"{'Name':<25} {'Team':<25} {'AB':>4} {'H':>4} {'HR':>4} {'RBI':>4} {'K%':>6} {stat.upper():>7}")
    print("-" * 86)
    for r in rows:
        print(f"{r['name']:<25} {r['team']:<25} {r['ab']:>4} {r['hits']:>4} {r['hr']:>4} {r['rbi']:>4} {r['krate']:>6} {r['sort_val']:>7}")


def pitchers(stat: str, days: int, min_ip: float, limit: int):
    col_map = {
        "k":   "SUM(l.k)",
        "era": "ROUND(SUM(l.er) * 9.0 / NULLIF(SUM(l.ip), 0), 2)",
        "ip":  "ROUND(SUM(l.ip), 1)",
    }
    col   = col_map.get(stat, col_map["k"])
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
        ORDER BY sort_val {order}
        LIMIT {limit}
    """).fetchall()
    conn.close()

    period = f"last {days}d" if days else "season"
    print(f"\nTop pitchers by {stat} ({period}, min {min_ip} IP)\n")
    print(f"{'Name':<25} {'Team':<25} {'IP':>6} {'K':>4} {'ER':>4} {'ERA':>6}")
    print("-" * 75)
    for r in rows:
        print(f"{r['name']:<25} {r['team']:<25} {r['ip']:>6} {r['k']:>4} {r['er']:>4} {r['era']:>6}")


def player(name: str, days: int, last5: bool):
    if last5:
        date_filter = ""
        limit_clause = """
            AND g.game_pk IN (
                SELECT DISTINCT g2.game_pk
                FROM player_game_logs l2
                JOIN players p2 ON p2.player_id = l2.player_id
                JOIN games g2 ON g2.game_pk = l2.game_pk
                WHERE p2.name LIKE ?
                ORDER BY g2.date DESC
                LIMIT 5
            )
        """
    else:
        date_filter  = f"AND g.date >= date('now', '-{days} days')" if days else ""
        limit_clause = ""

    params = [f"%{name}%"] * (2 if last5 else 1)

    conn = connect()
    rows = conn.execute(f"""
        SELECT g.date, g.home_team, g.away_team,
               l.stat_type, l.ab, l.hits, l.hr, l.rbi, l.bb, l.so, l.avg, l.ops,
               l.ip, l.er, l.k, l.era
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE p.name LIKE ? {date_filter} {limit_clause}
        ORDER BY g.date DESC
    """, params).fetchall()
    conn.close()

    if not rows:
        print(f"No results for '{name}'")
        return

    period = "last 5 games" if last5 else (f"last {days}d" if days else "season")
    print(f"\n{name} -- game log ({period})\n")
    for r in rows:
        matchup = f"{r['away_team']} @ {r['home_team']}"
        if r["stat_type"] == "hitting":
            print(f"  {r['date']}  {matchup:<45}  {r['ab']}AB {r['hits']}H {r['hr']}HR {r['rbi']}RBI {r['bb']}BB {r['so']}K  AVG:{r['avg'] or '-'} OPS:{r['ops'] or '-'}")
        else:
            print(f"  {r['date']}  {matchup:<45}  {r['ip']}IP {r['er']}ER {r['k']}K {r['bb']}BB  ERA:{r['era'] or '-'}")


def splits(name: str):
    conn = connect()
    rows = conn.execute("""
        SELECT
            CASE WHEN l.team = g.home_team THEN 'home' ELSE 'away' END as loc,
            SUM(l.ab) as ab, SUM(l.hits) as hits,
            SUM(l.hr) as hr, SUM(l.rbi) as rbi, SUM(l.so) as so,
            ROUND(CAST(SUM(l.hits) AS REAL) / NULLIF(SUM(l.ab), 0), 3) as avg,
            ROUND(CAST(SUM(l.so) AS REAL) / NULLIF(SUM(l.ab), 0), 3) as krate
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE p.name LIKE ? AND l.stat_type = 'hitting'
        GROUP BY loc
        ORDER BY loc DESC
    """, (f"%{name}%",)).fetchall()
    conn.close()

    if not rows:
        print(f"No results for '{name}'")
        return

    print(f"\n{name} -- home vs away splits (season)\n")
    print(f"{'Loc':<6} {'AB':>4} {'H':>4} {'HR':>4} {'RBI':>4} {'SO':>4} {'AVG':>6} {'K%':>6}")
    print("-" * 45)
    for r in rows:
        print(f"{r['loc']:<6} {r['ab']:>4} {r['hits']:>4} {r['hr']:>4} {r['rbi']:>4} {r['so']:>4} {r['avg']:>6} {r['krate']:>6}")

    if len(rows) == 2:
        home = next((r for r in rows if r["loc"] == "home"), None)
        away = next((r for r in rows if r["loc"] == "away"), None)
        if home and away and home["avg"] and away["avg"]:
            diff = round(home["avg"] - away["avg"], 3)
            edge = "home edge" if diff > 0 else "road edge"
            print(f"\n  AVG split: {abs(diff):+.3f} {edge}")


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


def totals(team_filter: str | None, days: int, line: float):
    date_filter = f"AND date >= date('now', '-{days} days')" if days else ""
    team_filter_sql = f"AND (home_team LIKE ? OR away_team LIKE ?)" if team_filter else ""
    params = ([f"%{team_filter}%", f"%{team_filter}%"] if team_filter else [])

    conn = connect()
    rows = conn.execute(f"""
        SELECT date, home_team, away_team, home_score, away_score, venue,
               (home_score + away_score) as total
        FROM games
        WHERE home_score IS NOT NULL AND away_score IS NOT NULL
          {date_filter} {team_filter_sql}
        ORDER BY date DESC
    """, params).fetchall()
    conn.close()

    if not rows:
        print("No results.")
        return

    totals_list = [r["total"] for r in rows]
    avg_total   = sum(totals_list) / len(totals_list)
    over_count  = sum(1 for t in totals_list if t > line)
    under_count = sum(1 for t in totals_list if t < line)
    push_count  = sum(1 for t in totals_list if t == line)

    period = f"last {days}d" if days else "season"
    label  = f" -- {team_filter}" if team_filter else ""
    print(f"\nGame totals{label} ({period})\n")

    # show park flag if team filter and consistent venue
    venues = set(r["venue"] for r in rows if r["venue"])
    if len(venues) == 1:
        flag = park_flag(list(venues)[0])
        if flag:
            print(f"  *** {flag} -- {list(venues)[0]} ***\n")

    print(f"  Games:       {len(rows)}")
    print(f"  Avg total:   {avg_total:.2f} runs/game")
    print(f"  Line ({line}):  OVER {over_count} ({over_count/len(rows)*100:.0f}%)  |  UNDER {under_count} ({under_count/len(rows)*100:.0f}%)  |  Push {push_count}")
    print()
    print(f"{'Date':<12} {'Matchup':<45} {'Total':>6} {'O/U':>5} {'Park'}")
    print("-" * 78)
    for r in rows[:20]:
        matchup = f"{r['away_team']} @ {r['home_team']}"
        ou      = "OVER" if r["total"] > line else ("PUSH" if r["total"] == line else "UNDR")
        flag    = park_flag(r["venue"] or "")
        print(f"{r['date']:<12} {matchup:<45} {r['total']:>6} {ou:>5}  {flag}")

    if len(rows) > 20:
        print(f"  ... {len(rows) - 20} more games")


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
        rf = (r["home_score"] if r["side"] == "home" else r["away_score"]) or 0
        ra = (r["away_score"] if r["side"] == "home" else r["home_score"]) or 0
        matchup = f"{r['away_team']} @ {r['home_team']}"
        wl = "W" if rf > ra else "L"
        runs_for += rf; runs_against += ra
        wins += 1 if wl == "W" else 0
        losses += 0 if wl == "W" else 1
        print(f"{r['date']:<12} {matchup:<45} {rf}-{ra:<7}  {wl}")

    g = wins + losses
    print(f"\n  Record: {wins}-{losses}  |  RS/G: {runs_for/g:.1f}  |  RA/G: {runs_against/g:.1f}  |  Diff: {(runs_for-runs_against)/g:+.1f}")


def main():
    parser = argparse.ArgumentParser(description="Query mlb-edge database")
    sub = parser.add_subparsers(dest="cmd")

    p_hit = sub.add_parser("hitters")
    p_hit.add_argument("--stat", default="hits", choices=["hits", "hr", "rbi", "avg", "ops", "krate"],
        help="krate = strikeout rate; highest = worst contact (fade for hit props)")
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
    p_pl.add_argument("--last5", action="store_true", help="Show last 5 games only")

    p_sp = sub.add_parser("splits")
    p_sp.add_argument("name", help="Player name (partial match)")

    p_str = sub.add_parser("streaks")
    p_str.add_argument("--min", type=int, default=5, dest="min_streak")

    p_tot = sub.add_parser("totals")
    p_tot.add_argument("--team", default=None, help="Filter to a team's games")
    p_tot.add_argument("--days", type=int, default=0)
    p_tot.add_argument("--line", type=float, default=8.5, help="O/U line to compare against")

    p_tm = sub.add_parser("team")
    p_tm.add_argument("name")
    p_tm.add_argument("--days", type=int, default=0)

    args = parser.parse_args()

    if args.cmd == "hitters":
        hitters(args.stat, args.days, args.min_ab, args.limit)
    elif args.cmd == "pitchers":
        pitchers(args.stat, args.days, args.min_ip, args.limit)
    elif args.cmd == "player":
        player(args.name, args.days, args.last5)
    elif args.cmd == "splits":
        splits(args.name)
    elif args.cmd == "streaks":
        streaks(args.min_streak)
    elif args.cmd == "totals":
        totals(args.team, args.days, args.line)
    elif args.cmd == "team":
        team(args.name, args.days)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
