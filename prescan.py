#!/usr/bin/env python3
"""
prescan.py -- Rank today's MLB games by pre-line edge, before any Underdog scrape.

Pulls probable pitchers + venue + team season stats from MLB Stats API.
Scores each game on 4 factors, prints ranked table, persists to pre_scan_scores.

Usage:
    python prescan.py                        # today, top 3
    python prescan.py --date 2026-05-18
    python prescan.py --top 5
"""

import argparse
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_cls
from pathlib import Path

import requests

MLB_DB = Path(__file__).parent / "mlb.db"
BASE = "https://statsapi.mlb.com/api/v1"

PITCHER_FRIENDLY = {"Petco Park", "T-Mobile Park", "loanDepot park", "Tropicana Field", "Oracle Park", "Comerica Park"}
HITTER_FRIENDLY = {"Coors Field", "Yankee Stadium", "Great American Ball Park", "Citizens Bank Park", "Globe Life Field"}


def ensure_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pre_scan_scores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL,
            game            TEXT NOT NULL,
            score           REAL NOT NULL,
            pitcher_edge    REAL,
            k_gap           REAL,
            venue_score     REAL,
            certainty       REAL,
            components_json TEXT,
            UNIQUE(date, game)
        );
    """)


def fetch_schedule(target_date: str) -> list[dict]:
    url = f"{BASE}/schedule?sportId=1&date={target_date}&hydrate=probablePitcher,venue"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data.get("dates"):
        return []
    return data["dates"][0].get("games", [])


def fetch_pitcher_stats(pitcher_id: int, season: str) -> dict:
    """Return {era, k_per_9, ip, k, bb, hr} or {} on miss."""
    if not pitcher_id:
        return {}
    url = f"{BASE}/people/{pitcher_id}/stats?stats=season&group=pitching&season={season}"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0].get("stat", {})
        return {
            "era": float(s.get("era", 0) or 0),
            "k_per_9": float(s.get("strikeoutsPer9Inn", 0) or 0),
            "ip": float(s.get("inningsPitched", 0) or 0),
            "k": int(s.get("strikeOuts", 0) or 0),
            "bb": int(s.get("baseOnBalls", 0) or 0),
            "hr": int(s.get("homeRuns", 0) or 0),
        }
    except Exception:
        return {}


def fetch_team_stats(team_id: int, season: str) -> dict:
    """Return {ops, k_pct, avg} or {} on miss."""
    url = f"{BASE}/teams/{team_id}/stats?stats=season&group=hitting&season={season}"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0].get("stat", {})
        pa = int(s.get("plateAppearances", 0) or 0)
        so = int(s.get("strikeOuts", 0) or 0)
        k_pct = (so / pa * 100) if pa else 0.0
        return {
            "ops": float(s.get("ops", 0) or 0),
            "k_pct": k_pct,
            "avg": float(s.get("avg", 0) or 0),
        }
    except Exception:
        return {}


def fip_proxy(stat: dict) -> float:
    """(13*HR + 3*BB - 2*K) / IP + 3.1 -- rough FIP."""
    ip = stat.get("ip", 0)
    if ip < 5:
        return 5.0  # not enough innings, conservative middle
    return (13 * stat.get("hr", 0) + 3 * stat.get("bb", 0) - 2 * stat.get("k", 0)) / ip + 3.1


def grade_pitcher(stat: dict) -> int:
    """0-10 grade. ELITE=9-10, GOOD=7-8, MID=5-6, FADE=2-3, UNKNOWN=4-5.
    Captures three flavors of ELITE: K-stuff (McClanahan), contact-suppression FIP (Valdez),
    and high-stuff debut (Painter-style prospect with K/9 >= 10 in limited innings)."""
    if not stat:
        return 4
    ip = stat.get("ip", 0)
    era = stat.get("era", 0)
    k9 = stat.get("k_per_9", 0)

    # Debut / prospect bucket: <15 IP. If K-stuff is showing up, treat as high-upside.
    if ip < 15:
        if k9 >= 10:
            return 8  # high-stuff debut, market mispricing edge
        return 5  # unknown but not dismissed -- volatility itself can be edge

    fip = fip_proxy(stat)
    # ELITE by K-stuff (high-K aces)
    if k9 >= 9.5 and era <= 3.30 and fip <= 3.40:
        return 10
    # ELITE by FIP (ground-ball / contact-suppression aces like Valdez)
    if fip <= 3.20 and era <= 3.50:
        return 9
    if k9 >= 8.5 and era <= 3.80:
        return 8
    if k9 >= 7.0 and era <= 4.30:
        return 6
    if era >= 5.00 or k9 <= 5.5:
        return 2
    return 5


def score_game(game: dict, season: str) -> dict:
    teams = game.get("teams", {})
    away_t = teams.get("away", {}).get("team", {})
    home_t = teams.get("home", {}).get("team", {})
    away_pp = teams.get("away", {}).get("probablePitcher") or {}
    home_pp = teams.get("home", {}).get("probablePitcher") or {}
    venue = (game.get("venue") or {}).get("name", "?")

    game_str = f"{away_t.get('abbreviation') or away_t.get('teamCode') or away_t.get('name','?')} @ {home_t.get('abbreviation') or home_t.get('teamCode') or home_t.get('name','?')}"

    # Parallel fetch
    away_pid = away_pp.get("id")
    home_pid = home_pp.get("id")
    away_tid = away_t.get("id")
    home_tid = home_t.get("id")

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_ap = pool.submit(fetch_pitcher_stats, away_pid, season)
        f_hp = pool.submit(fetch_pitcher_stats, home_pid, season)
        f_at = pool.submit(fetch_team_stats, away_tid, season)
        f_ht = pool.submit(fetch_team_stats, home_tid, season)
        ap_stat = f_ap.result()
        hp_stat = f_hp.result()
        at_stat = f_at.result()
        ht_stat = f_ht.result()

    # 1. Pitcher grade asymmetry (35%): max 10 when one elite + one fade
    g1 = grade_pitcher(ap_stat)
    g2 = grade_pitcher(hp_stat)
    pitcher_edge = min(10, abs(g1 - g2) + min(g1, g2) * 0.2)  # asymmetry + floor for "both quality"

    # 2. Team K-rate gap (25%): how different the two lineups' K% are
    k_pct_diff = abs(at_stat.get("k_pct", 22) - ht_stat.get("k_pct", 22))
    k_gap = min(10, k_pct_diff * 1.5)  # 6.7pp diff -> 10

    # 3. Venue (20%)
    if venue in PITCHER_FRIENDLY:
        venue_score = 8
    elif venue in HITTER_FRIENDLY:
        venue_score = 7
    else:
        venue_score = 5

    # 4. Lineup certainty (20%): both probable pitchers named + game not postponed
    has_both = bool(away_pid) and bool(home_pid)
    status = (game.get("status") or {}).get("detailedState", "")
    postponed = "postponed" in status.lower() or "cancelled" in status.lower()
    certainty = 9 if (has_both and not postponed) else (3 if not has_both else 0)

    total = pitcher_edge * 0.35 + k_gap * 0.25 + venue_score * 0.20 + certainty * 0.20

    return {
        "game": game_str,
        "score": round(total, 2),
        "pitcher_edge": round(pitcher_edge, 2),
        "k_gap": round(k_gap, 2),
        "venue_score": venue_score,
        "certainty": certainty,
        "components": {
            "away_pitcher": away_pp.get("fullName"),
            "home_pitcher": home_pp.get("fullName"),
            "away_grade": g1,
            "home_grade": g2,
            "away_team_k_pct": round(at_stat.get("k_pct", 0), 1),
            "home_team_k_pct": round(ht_stat.get("k_pct", 0), 1),
            "venue": venue,
            "postponed": postponed,
        },
    }


def persist(target_date: str, results: list[dict]):
    conn = sqlite3.connect(MLB_DB)
    ensure_table(conn)
    for r in results:
        conn.execute("""
            INSERT OR REPLACE INTO pre_scan_scores
            (date, game, score, pitcher_edge, k_gap, venue_score, certainty, components_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (target_date, r["game"], r["score"], r["pitcher_edge"], r["k_gap"],
              r["venue_score"], r["certainty"], json.dumps(r["components"])))
    conn.commit()
    conn.close()


def print_table(date_str: str, ranked: list[dict], top_n: int):
    print(f"\nPre-scan {date_str}  ({len(ranked)} games)\n")
    print(f"{'Rank':<5} {'Game':<14} {'Score':>6} {'PE':>5} {'KGap':>5} {'Ven':>4} {'Cert':>5}  Why")
    print("─" * 90)
    for i, r in enumerate(ranked, 1):
        marker = " ★" if i <= top_n else "  "
        c = r["components"]
        why = f"{c.get('away_pitcher','?')} vs {c.get('home_pitcher','?')}"
        if c.get("postponed"):
            why = "POSTPONED -- " + why
        print(f"{i:<3}{marker} {r['game']:<14} {r['score']:>6.2f} {r['pitcher_edge']:>5.1f} "
              f"{r['k_gap']:>5.1f} {r['venue_score']:>4} {r['certainty']:>5}  {why}")
    print("─" * 90)
    top = [r["game"] for r in ranked[:top_n]]
    print(f"\nTop {top_n} to scrape: {', '.join(top)}")
    print(f"Run: /underdog-mlb {top[0]}    (then repeat for remaining {top_n - 1})\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=date_cls.today().isoformat())
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    season = args.date.split("-")[0]
    games = fetch_schedule(args.date)
    if not games:
        print(f"No games found for {args.date}")
        return

    print(f"Scoring {len(games)} games for {args.date}...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda g: score_game(g, season), games))

    ranked = sorted(results, key=lambda r: r["score"], reverse=True)
    persist(args.date, ranked)
    print_table(args.date, ranked, args.top)


if __name__ == "__main__":
    main()
