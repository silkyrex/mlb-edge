#!/usr/bin/env python3
"""
prescan.py -- Rank today's MLB games by pre-line edge, before any Underdog scrape.

Pulls probable pitchers + venue + team season stats from MLB Stats API.
Scores each game on 4 factors, prints ranked table, persists to pre_scan_scores.

Usage:
    python prescan.py                        # today, top 3
    python prescan.py --date 2026-05-18
    python prescan.py --top 5
    python prescan.py backtest               # rank-vs-survivor correlation (needs 7+ days)
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
ESPN_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb"
    "/statistics/byathlete?category=pitching&season={season}&seasontype=2&limit=300"
)
FIP_CONSTANT = 3.1

PITCHER_FRIENDLY = {"Petco Park", "T-Mobile Park", "loanDepot park", "Tropicana Field", "Oracle Park", "Comerica Park"}
HITTER_FRIENDLY = {"Coors Field", "Yankee Stadium", "Great American Ball Park", "Citizens Bank Park", "Globe Life Field"}

# Dome / fixed-roof or retractable-roof venues -- weather is irrelevant.
# T-Mobile Park is intentionally listed in BOTH this set AND PITCHER_FRIENDLY:
# its retractable roof is usually closed for night games, so weather skip + venue
# bonus is the right call ~90% of the time. Day games with open roof are an
# accepted edge case we don't model.
DOME_OR_RETRACTABLE = {
    "Tropicana Field", "Rogers Centre", "Chase Field", "Minute Maid Park",
    "Globe Life Field", "loanDepot park", "American Family Field", "T-Mobile Park",
}

WEATHER_UA = "mlb-edge/1.0 (raymond@silkyrex.dev)"
BACKTEST_MIN_DAYS = 7


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
            reason          TEXT,
            UNIQUE(date, game)
        );
    """)
    # Migration: add reason column to existing tables
    try:
        conn.execute("ALTER TABLE pre_scan_scores ADD COLUMN reason TEXT")
        conn.commit()
    except Exception:
        pass  # column already exists


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


def fetch_team_k_pct(team_id: int, season: str) -> float | None:
    """Return team K% (strikeouts / plate appearances * 100) or None on miss."""
    if not team_id:
        return None
    url = f"{BASE}/teams/{team_id}/stats?stats=season&group=hitting&season={season}"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        s = splits[0].get("stat", {})
        pa = int(s.get("plateAppearances", 0) or 0)
        so = int(s.get("strikeOuts", 0) or 0)
        return (so / pa * 100) if pa else None
    except Exception:
        return None


def fip_proxy(stat: dict) -> float:
    """(13*HR + 3*BB - 2*K) / IP + 3.1 -- rough FIP. Fallback when ESPN FIP unavailable."""
    ip = stat.get("ip", 0)
    if ip < 5:
        return 5.0  # not enough innings, conservative middle
    return (13 * stat.get("hr", 0) + 3 * stat.get("bb", 0) - 2 * stat.get("k", 0)) / ip + FIP_CONSTANT


def fetch_espn_pitchers(season: str) -> dict[str, dict]:
    """Pull all qualified pitchers' stats from ESPN in one call.
    Returns {lowercase_name: {fip, era, k_per_9, ip, k, bb, hr}}.
    Saves ~28 redundant per-pitcher MLB-API calls per session when ESPN has the data."""
    try:
        r = requests.get(ESPN_URL.format(season=season), timeout=12)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}
    cats = data.get("categories", [])
    pitch_cat = next((c for c in cats if c["name"] == "pitching"), None)
    if not pitch_cat:
        return {}
    labels = pitch_cat["labels"]
    result: dict[str, dict] = {}
    for ath in data.get("athletes", []):
        name = ath.get("athlete", {}).get("displayName", "")
        if not name:
            continue
        stats_cat = next((c for c in ath.get("categories", []) if c.get("name") == "pitching"), {})
        values = stats_cat.get("values", [])
        row = dict(zip(labels, values))
        ip = row.get("IP") or 0
        if ip <= 0:
            continue
        k = row.get("K") or 0
        bb = row.get("BB") or 0
        hr = row.get("HR") or 0
        era = row.get("ERA") or 0
        # ESPN labels K/9 as "K/9" usually
        k_per_9 = row.get("K/9") or (k * 9 / ip if ip else 0)
        result[name.lower()] = {
            "fip": round((13 * hr + 3 * bb - 2 * k) / ip + FIP_CONSTANT, 2),
            "era": float(era),
            "k_per_9": float(k_per_9),
            "ip": float(ip),
            "k": int(k),
            "bb": int(bb),
            "hr": int(hr),
        }
    return result


def lookup_espn(pitcher_name: str, espn_map: dict[str, dict]) -> dict | None:
    """Match by full name first, then last name. Returns full stat dict or None."""
    if not pitcher_name:
        return None
    key = pitcher_name.lower()
    if key in espn_map:
        return espn_map[key]
    last = pitcher_name.split()[-1].lower()
    for espn_name, stats in espn_map.items():
        if espn_name.split()[-1] == last:
            return stats
    return None


def grade_pitcher(stat: dict, espn_fip: float | None = None) -> int:
    """0-10 grade. ELITE=9-10, GOOD=7-8, MID=5-6, FADE=2-3, UNKNOWN=4-5.
    ERA thresholds aligned with ~/mlb-edge/CLAUDE.md: ELITE <= 3.00, FADE >= 4.50.
    Captures three flavors of ELITE: K-stuff (McClanahan), contact-suppression FIP (Valdez),
    high-stuff debut (Painter-style prospect with K/9 >= 10 in limited innings).
    Uses ESPN FIP if provided (Mark II), else falls back to fip_proxy from MLB API totals."""
    if not stat:
        return 4
    ip = stat.get("ip", 0)
    era = stat.get("era", 0)
    k9 = stat.get("k_per_9", 0)

    # Debut / prospect bucket: <15 IP. K-stuff shows up early; reward it.
    if ip < 15:
        if k9 >= 10:
            return 8  # high-stuff debut, market mispricing edge
        return 5  # unknown but not dismissed -- volatility itself can be edge

    fip = espn_fip if espn_fip is not None else fip_proxy(stat)
    # ELITE by K-stuff (high-K aces like McClanahan)
    if k9 >= 9.5 and era <= 3.00 and fip <= 3.20:
        return 10
    # ELITE by FIP (ground-ball / contact-suppression aces like Valdez when in form)
    if fip <= 3.00 and era <= 3.20:
        return 9
    if k9 >= 8.5 and era <= 3.60:
        return 8
    if k9 >= 7.0 and era <= 4.20:
        return 6
    if era >= 4.50 or k9 <= 5.5:
        return 2
    return 5


def fetch_venue_coords(venue_id: int) -> tuple[float, float] | None:
    if not venue_id:
        return None
    try:
        r = requests.get(f"{BASE}/venues/{venue_id}?hydrate=location", timeout=6)
        r.raise_for_status()
        v = (r.json().get("venues") or [{}])[0]
        coords = (v.get("location") or {}).get("defaultCoordinates") or {}
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        if lat is None or lon is None:
            return None
        return (round(float(lat), 4), round(float(lon), 4))
    except Exception:
        return None


def fetch_weather(lat: float, lon: float, game_iso: str) -> dict:
    """Return {precip_pct, temp_f} for the hour matching game_iso, or {} on miss."""
    try:
        pt = requests.get(f"https://api.weather.gov/points/{lat},{lon}",
                          headers={"User-Agent": WEATHER_UA}, timeout=6)
        pt.raise_for_status()
        hourly_url = (pt.json().get("properties") or {}).get("forecastHourly")
        if not hourly_url:
            return {}
        fc = requests.get(hourly_url, headers={"User-Agent": WEATHER_UA}, timeout=8)
        fc.raise_for_status()
        periods = ((fc.json().get("properties") or {}).get("periods") or [])
        if not periods or not game_iso:
            return {}
        # Match the period covering game_iso. periods sorted by startTime.
        game_iso_trim = game_iso[:13]  # YYYY-MM-DDTHH
        match = next((p for p in periods if (p.get("startTime") or "")[:13] == game_iso_trim), None)
        if not match:
            match = periods[0]  # fallback to nearest period
        return {
            "precip_pct": int((match.get("probabilityOfPrecipitation") or {}).get("value") or 0),
            "temp_f": match.get("temperature"),
        }
    except Exception:
        return {}


def score_game(game: dict, season: str, espn_map: dict[str, dict]) -> dict:
    teams = game.get("teams", {})
    away_t = teams.get("away", {}).get("team", {})
    home_t = teams.get("home", {}).get("team", {})
    away_pp = teams.get("away", {}).get("probablePitcher") or {}
    home_pp = teams.get("home", {}).get("probablePitcher") or {}
    venue = (game.get("venue") or {}).get("name", "?")

    # Always use full team names for cross-table join stability with mlb_game_lines
    game_str = f"{away_t.get('name', '?')} @ {home_t.get('name', '?')}"

    # ESPN-first pitcher lookup; only hit MLB API when ESPN doesn't have the pitcher
    # (debutants, low-IP swingmen). Saves ~28 redundant API calls per session.
    away_espn = lookup_espn(away_pp.get("fullName"), espn_map)
    home_espn = lookup_espn(home_pp.get("fullName"), espn_map)
    away_pid = away_pp.get("id")
    home_pid = home_pp.get("id")
    away_tid = away_t.get("id")
    home_tid = home_t.get("id")

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_ap = None if away_espn else pool.submit(fetch_pitcher_stats, away_pid, season)
        f_hp = None if home_espn else pool.submit(fetch_pitcher_stats, home_pid, season)
        f_at = pool.submit(fetch_team_k_pct, away_tid, season)
        f_ht = pool.submit(fetch_team_k_pct, home_tid, season)
        ap_stat = away_espn or (f_ap.result() if f_ap else {})
        hp_stat = home_espn or (f_hp.result() if f_hp else {})
        at_k_pct = f_at.result()
        ht_k_pct = f_ht.result()

    # 1. Pitcher edge (35%): rewards both-quality AND asymmetry.
    # max() drives the base (both elite = high), asymmetry adds bonus for elite-vs-fade.
    # Old formula (min(...) + asymmetry) penalized two-elite K-shootouts -- fixed in Mark III review.
    ap_fip = ap_stat.get("fip") if away_espn else None
    hp_fip = hp_stat.get("fip") if home_espn else None
    g1 = grade_pitcher(ap_stat, ap_fip)
    g2 = grade_pitcher(hp_stat, hp_fip)
    pitcher_edge = min(10, max(g1, g2) * 0.7 + abs(g1 - g2) * 0.5)

    # 2. Team K-rate gap (25%): how different the two lineups' K% are
    k_pct_diff = abs((at_k_pct or 22) - (ht_k_pct or 22))
    k_gap = min(10, k_pct_diff * 1.5)  # 6.7pp diff -> 10

    # 3. Venue (20%): pitcher-friendly = 8 (K Over edges), hitter-friendly = 7 (HRR edges),
    # neutral = 5. Asymmetric weights reflect that K-prop edges are sharper in pitcher parks.
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

    # Weather check (Mark III). Skip domes/retractables. Penalize heavy rainout risk.
    weather = {}
    if venue not in DOME_OR_RETRACTABLE:
        venue_id = (game.get("venue") or {}).get("id")
        coords = fetch_venue_coords(venue_id)
        if coords:
            weather = fetch_weather(*coords, game.get("gameDate", ""))
    precip = weather.get("precip_pct", 0)
    rainout_risk = precip >= 70
    if rainout_risk:
        certainty = min(certainty, 2)  # collapse certainty

    total = pitcher_edge * 0.35 + k_gap * 0.25 + venue_score * 0.20 + certainty * 0.20
    if rainout_risk:
        total *= 0.3  # rainout dampener -- skip these games

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
            "away_fip_espn": ap_fip,
            "home_fip_espn": hp_fip,
            "away_team_k_pct": round(at_k_pct, 1) if at_k_pct is not None else None,
            "home_team_k_pct": round(ht_k_pct, 1) if ht_k_pct is not None else None,
            "venue": venue,
            "postponed": postponed,
            "weather": weather,
            "rainout_risk": rainout_risk,
        },
    }


def _tier(grade: int) -> str:
    if grade >= 9: return "ELITE"
    if grade >= 7: return "GOOD"
    if grade >= 5: return "MID"
    if grade >= 2: return "FADE"
    return "?"


def generate_reason(row: dict) -> str:
    """Build a plain-English reason string from prescan data. No new API calls."""
    c = row["components"]
    away_p = c.get("away_pitcher") or "TBD"
    home_p = c.get("home_pitcher") or "TBD"
    away_tier = _tier(c.get("away_grade", 4))
    home_tier = _tier(c.get("home_grade", 4))

    at_k = c.get("away_team_k_pct")
    ht_k = c.get("home_team_k_pct")
    k_gap_str = f"K-gap {abs(at_k - ht_k):.1f}%" if (at_k is not None and ht_k is not None) else "K-gap ?"

    venue = c.get("venue", "")
    if venue in PITCHER_FRIENDLY:
        venue_str = "pitcher-friendly park"
    elif venue in HITTER_FRIENDLY:
        venue_str = "hitter-friendly park"
    else:
        venue_str = "neutral park"

    rain = " | ⚠️ rain risk" if c.get("rainout_risk") else ""
    return f"{away_p} ({away_tier}) vs {home_p} ({home_tier}) | {k_gap_str} | {venue_str}{rain}"


def persist(target_date: str, results: list[dict]):
    conn = sqlite3.connect(MLB_DB)
    ensure_table(conn)
    for r in results:
        conn.execute("""
            INSERT OR REPLACE INTO pre_scan_scores
            (date, game, score, pitcher_edge, k_gap, venue_score, certainty, components_json, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (target_date, r["game"], r["score"], r["pitcher_edge"], r["k_gap"],
              r["venue_score"], r["certainty"], json.dumps(r["components"]),
              generate_reason(r)))
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


def run_rank(target_date: str, top_n: int):
    season = target_date.split("-")[0]
    games = fetch_schedule(target_date)
    if not games:
        print(f"No games found for {target_date}")
        return

    print(f"Scoring {len(games)} games for {target_date}...")
    espn_map = fetch_espn_pitchers(season)
    if espn_map:
        print(f"  ESPN loaded for {len(espn_map)} qualified pitchers (saves per-pitcher MLB API calls)")
    else:
        print("  ESPN unavailable -- falling back to per-pitcher MLB API calls")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda g: score_game(g, season, espn_map), games))

    ranked = sorted(results, key=lambda r: r["score"], reverse=True)
    persist(target_date, ranked)
    print_table(target_date, ranked, top_n)


def run_backtest(since: str | None):
    """Rank-vs-survivor correlation. Needs >=7 days of pre_scan_scores history."""
    conn = sqlite3.connect(MLB_DB)
    conn.row_factory = sqlite3.Row
    where_sql = "WHERE date >= ?" if since else ""
    params = (since,) if since else ()
    dates = [r[0] for r in conn.execute(
        f"SELECT DISTINCT date FROM pre_scan_scores {where_sql} ORDER BY date", params
    ).fetchall()]
    if len(dates) < BACKTEST_MIN_DAYS:
        print(f"Insufficient data: {len(dates)} day(s) in pre_scan_scores, need {BACKTEST_MIN_DAYS}+.")
        print(f"Run `python prescan.py` daily; backtest unlocks once history reaches {BACKTEST_MIN_DAYS} days.")
        conn.close()
        return

    # When data accrues: join pre_scan_scores with sliplog.db slip_picks to find which
    # games had closer-survivor picks placed, compute avg rank for survivors vs all.
    # Stub for now: report shape only.
    counts = conn.execute(f"""
        SELECT date, COUNT(*) as games, ROUND(AVG(score),2) as avg_score, ROUND(MAX(score),2) as max_score
        FROM pre_scan_scores {where_sql}
        GROUP BY date ORDER BY date
    """, params).fetchall()
    print(f"\nBacktest summary ({len(dates)} days):\n")
    print(f"{'Date':<12} {'Games':>6} {'Avg':>6} {'Max':>6}")
    for r in counts:
        print(f"{r['date']:<12} {r['games']:>6} {r['avg_score']:>6} {r['max_score']:>6}")
    print("\nFull rank-vs-survivor correlation not yet implemented (Mark IV).")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    bt = sub.add_parser("backtest", help="Rank-vs-survivor analysis (needs 7+ days)")
    bt.add_argument("--since", default=None, help="YYYY-MM-DD start date")

    ap.add_argument("--date", default=date_cls.today().isoformat())
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    if args.cmd == "backtest":
        run_backtest(args.since)
    else:
        run_rank(args.date, args.top)


if __name__ == "__main__":
    main()
