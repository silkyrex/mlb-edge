"""
scout.py -- tier rankings for betting: ELITE (bet on) / FADE (bet against)

Usage:
  python scout.py teams
  python scout.py pitchers [--starters-only]
  python scout.py hitters [--hot-only]
  python scout.py all [--starters-only] [--hot-only]
"""

import argparse
from db import connect

ELITE_PCT   = 0.20
FADE_PCT    = 0.20
STARTER_IP  = 30.0  # min IP to count as a starter


def trend(season_rank: int, recent_rank: int, total: int) -> str:
    delta = season_rank - recent_rank
    threshold = max(3, int(total * 0.15))
    if delta >= threshold:
        return "UP"
    elif delta <= -threshold:
        return "DN"
    return "--"


def tier_label(rank: int, total: int) -> str:
    if rank <= int(total * ELITE_PCT):
        return "ELITE"
    elif rank > total - int(total * FADE_PCT):
        return "FADE"
    return "mid"


def teams(hot_only: bool = False):
    conn = connect()
    season = conn.execute("""
        SELECT t.name,
               COUNT(*) as g,
               SUM(CASE WHEN t.rf > t.ra THEN 1 ELSE 0 END) as wins,
               ROUND(AVG(t.rf), 2) as rsg
        FROM (
            SELECT home_team as name, home_score as rf, away_score as ra FROM games WHERE home_score IS NOT NULL
            UNION ALL
            SELECT away_team, away_score, home_score FROM games WHERE away_score IS NOT NULL
        ) t
        GROUP BY t.name
        ORDER BY rsg DESC
    """).fetchall()

    recent = conn.execute("""
        SELECT t.name, ROUND(AVG(t.rf), 2) as rsg_recent
        FROM (
            SELECT home_team as name, home_score as rf FROM games
            WHERE home_score IS NOT NULL AND date >= date('now', '-14 days')
            UNION ALL
            SELECT away_team, away_score FROM games
            WHERE away_score IS NOT NULL AND date >= date('now', '-14 days')
        ) t
        GROUP BY t.name
        ORDER BY rsg_recent DESC
    """).fetchall()
    conn.close()

    recent_rank = {r["name"]: i + 1 for i, r in enumerate(recent)}
    recent_rsg  = {r["name"]: r["rsg_recent"] for r in recent}
    total = len(season)

    print(f"\nTEAM TIERS  (season RS/G + last-14d trend)  --  ELITE = bet on | FADE = bet against\n")
    print(f"{'Tier':<6} {'Team':<28} {'W-L':>5} {'RS/G':>5} {'RS/G 14d':>8} {'Trend':>5}")
    print("-" * 65)

    for i, r in enumerate(season):
        rank = i + 1
        t = tier_label(rank, total)
        if t == "mid":
            continue
        rr   = recent_rank.get(r["name"], rank)
        tr   = trend(rank, rr, total)
        if hot_only and t == "ELITE" and tr == "DN":
            continue
        wl   = f"{r['wins']}-{r['g'] - r['wins']}"
        rsg14 = f"{recent_rsg[r['name']]:.2f}" if r["name"] in recent_rsg else "-"
        print(f"{t:<6} {r['name']:<28} {wl:>5} {r['rsg']:>5} {rsg14:>8} {tr:>5}")


def pitchers(starters_only: bool = False, hot_only: bool = False):
    min_ip = STARTER_IP if starters_only else 15.0
    label  = "STARTERS" if starters_only else "PITCHERS"

    conn = connect()
    season = conn.execute(f"""
        SELECT p.player_id, p.name, p.team,
               ROUND(SUM(l.ip), 1) as ip,
               SUM(l.k) as k,
               SUM(l.er) as er,
               ROUND(SUM(l.er) * 9.0 / NULLIF(SUM(l.ip), 0), 2) as era,
               ROUND(SUM(l.k) * 9.0 / NULLIF(SUM(l.ip), 0), 2) as k9
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        WHERE l.stat_type = 'pitching' AND l.ip IS NOT NULL
        GROUP BY l.player_id
        HAVING SUM(l.ip) >= {min_ip}
        ORDER BY era ASC
    """).fetchall()

    recent = conn.execute("""
        SELECT p.player_id,
               ROUND(SUM(l.er) * 9.0 / NULLIF(SUM(l.ip), 0), 2) as era_recent
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE l.stat_type = 'pitching' AND l.ip IS NOT NULL
          AND g.date >= date('now', '-14 days')
        GROUP BY l.player_id
        HAVING SUM(l.ip) >= 3
        ORDER BY era_recent ASC
    """).fetchall()
    conn.close()

    sorted_recent    = sorted(recent, key=lambda x: x["era_recent"] or 99)
    recent_rank_map  = {r["player_id"]: i + 1 for i, r in enumerate(sorted_recent)}
    recent_era_map   = {r["player_id"]: r["era_recent"] for r in recent}
    total = len(season)

    print(f"\n{label} TIERS  (season ERA + last-14d trend, min {min_ip} IP)  --  ELITE = low ERA | FADE = high ERA\n")
    print(f"{'Tier':<6} {'Name':<25} {'Team':<25} {'IP':>5} {'ERA':>5} {'ERA 14d':>7} {'K/9':>5} {'Trend':>5}")
    print("-" * 87)

    for i, r in enumerate(season):
        rank = i + 1
        t    = tier_label(rank, total)
        if t == "mid":
            continue
        pid  = r["player_id"]
        rr   = recent_rank_map.get(pid, rank)
        tr   = trend(rank, rr, total)
        if hot_only and t == "ELITE" and tr == "DN":
            continue
        era14 = f"{recent_era_map[pid]:.2f}" if pid in recent_era_map else "-"
        print(f"{t:<6} {r['name']:<25} {r['team']:<25} {r['ip']:>5} {r['era']:>5} {era14:>7} {r['k9']:>5} {tr:>5}")


def hitters(hot_only: bool = False):
    conn = connect()
    season = conn.execute("""
        SELECT p.player_id, p.name, p.team,
               SUM(l.ab) as ab, SUM(l.hits) as hits,
               SUM(l.hr) as hr, SUM(l.rbi) as rbi,
               ROUND(CAST(SUM(l.hits) AS REAL) / NULLIF(SUM(l.ab), 0), 3) as avg
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        WHERE l.stat_type = 'hitting'
        GROUP BY l.player_id
        HAVING SUM(l.ab) >= 60
        ORDER BY avg DESC
    """).fetchall()

    recent = conn.execute("""
        SELECT p.player_id,
               ROUND(CAST(SUM(l.hits) AS REAL) / NULLIF(SUM(l.ab), 0), 3) as avg_recent
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        JOIN games g ON g.game_pk = l.game_pk
        WHERE l.stat_type = 'hitting'
          AND g.date >= date('now', '-14 days')
        GROUP BY l.player_id
        HAVING SUM(l.ab) >= 15
        ORDER BY avg_recent DESC
    """).fetchall()
    conn.close()

    sorted_recent   = sorted(recent, key=lambda x: -(x["avg_recent"] or 0))
    recent_rank_map = {r["player_id"]: i + 1 for i, r in enumerate(sorted_recent)}
    recent_avg_map  = {r["player_id"]: r["avg_recent"] for r in recent}
    total = len(season)

    flag = "  (hot-only: ELITE with UP or stable trend)" if hot_only else ""
    print(f"\nHITTER TIERS  (season AVG + last-14d trend){flag}  --  ELITE = bet on | FADE = bet against\n")
    print(f"{'Tier':<6} {'Name':<25} {'Team':<25} {'AB':>4} {'AVG':>5} {'AVG 14d':>7} {'HR':>4} {'RBI':>4} {'Trend':>5}")
    print("-" * 90)

    for i, r in enumerate(season):
        rank = i + 1
        t    = tier_label(rank, total)
        if t == "mid":
            continue
        pid  = r["player_id"]
        rr   = recent_rank_map.get(pid, rank)
        tr   = trend(rank, rr, total)
        if hot_only and t == "ELITE" and tr == "DN":
            continue
        avg14 = f"{recent_avg_map[pid]:.3f}" if pid in recent_avg_map else "-"
        print(f"{t:<6} {r['name']:<25} {r['team']:<25} {r['ab']:>4} {r['avg']:>5} {avg14:>7} {r['hr']:>4} {r['rbi']:>4} {tr:>5}")


def main():
    parser = argparse.ArgumentParser(description="Scout tiers -- ELITE vs FADE")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("teams").add_argument("--hot-only", action="store_true",
        help="ELITE only if trending UP or stable")

    p_pit = sub.add_parser("pitchers")
    p_pit.add_argument("--starters-only", action="store_true",
        help=f"Only pitchers with {STARTER_IP}+ IP (filters out relievers)")
    p_pit.add_argument("--hot-only", action="store_true",
        help="ELITE only if trending UP or stable")

    p_hit = sub.add_parser("hitters")
    p_hit.add_argument("--hot-only", action="store_true",
        help="ELITE only if trending UP or stable")

    p_all = sub.add_parser("all")
    p_all.add_argument("--starters-only", action="store_true")
    p_all.add_argument("--hot-only", action="store_true")

    args = parser.parse_args()

    if args.cmd == "teams":
        teams(hot_only=args.hot_only)
    elif args.cmd == "pitchers":
        pitchers(starters_only=args.starters_only, hot_only=args.hot_only)
    elif args.cmd == "hitters":
        hitters(hot_only=args.hot_only)
    elif args.cmd == "all":
        teams(hot_only=args.hot_only)
        pitchers(starters_only=args.starters_only, hot_only=args.hot_only)
        hitters(hot_only=args.hot_only)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
