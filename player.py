"""
player.py -- individual pitcher/batter drill-down and head-to-head

Usage:
    python player.py pitcher "Aaron Civale"
    python player.py pitcher "Aaron Civale" --starts 8
    python player.py batter "Matt Chapman"
    python player.py batter "Matt Chapman" --games 20
    python player.py matchup "Matt Chapman" "Aaron Civale"
"""

import argparse
import requests
import sqlite3
from pathlib import Path

PICKS_DB = Path(__file__).parent / "mlb.db"
BASE = "https://statsapi.mlb.com/api/v1"
SEASON = "2026"


def get_conn():
    conn = sqlite3.connect(PICKS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _float(val):
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def resolve_player_id(name: str) -> tuple[int | None, str]:
    """Look up player ID from picks.db cache, then roster search."""
    conn = get_conn()
    row = conn.execute(
        "SELECT mlb_player_id, player FROM player_recent_stats WHERE player LIKE ? LIMIT 1",
        (f"%{name.split()[-1]}%",)
    ).fetchone()
    conn.close()
    if row and row["mlb_player_id"]:
        return row["mlb_player_id"], row["player"]

    # Fall back to schedule-based roster search
    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "date": "2026-05-16"}, timeout=10)
    if r.status_code != 200:
        return None, name
    team_ids = set()
    for d in r.json().get("dates", []):
        for game in d.get("games", []):
            team_ids.add(game["teams"]["home"]["team"]["id"])
            team_ids.add(game["teams"]["away"]["team"]["id"])

    last = name.split()[-1].lower()
    for tid in list(team_ids)[:15]:
        r2 = requests.get(f"{BASE}/teams/{tid}/roster", params={"season": SEASON}, timeout=10)
        if r2.status_code != 200:
            continue
        for entry in r2.json().get("roster", []):
            person = entry["person"]
            if last in person["fullName"].lower():
                return person["id"], person["fullName"]
    return None, name


def pitcher_log(name: str, n_starts: int = 5):
    pid, full_name = resolve_player_id(name)
    if not pid:
        print(f"Player not found: {name}")
        return

    r = requests.get(f"{BASE}/people/{pid}/stats", params={
        "stats": "gameLog",
        "group": "pitching",
        "season": SEASON,
        "limit": n_starts,
    }, timeout=10)
    r.raise_for_status()
    splits = r.json().get("stats", [{}])[0].get("splits", [])

    # Season totals
    rs = requests.get(f"{BASE}/people/{pid}/stats", params={
        "stats": "season",
        "group": "pitching",
        "season": SEASON,
    }, timeout=10)
    season = {}
    if rs.status_code == 200:
        ss = rs.json().get("stats", [{}])[0].get("splits", [])
        season = ss[0].get("stat", {}) if ss else {}

    # Home/away splits
    rha = requests.get(f"{BASE}/people/{pid}/stats", params={
        "stats": "statSplits",
        "group": "pitching",
        "season": SEASON,
        "sitCodes": "h,a",
    }, timeout=10)
    home_era = away_era = None
    if rha.status_code == 200:
        for stat in rha.json().get("stats", []):
            for split in stat.get("splits", []):
                code = split.get("split", {}).get("code", "")
                era = _float(split["stat"].get("era"))
                if code == "h":
                    home_era = era
                elif code == "a":
                    away_era = era

    print(f"\n{'='*65}")
    print(f"PITCHER: {full_name}")
    s = season
    ip = _float(s.get("inningsPitched")) or 0
    k9 = round(s.get("strikeOuts", 0) * 9 / ip, 1) if ip > 0 else None
    print(f"Season:  ERA={s.get('era','?')}  WHIP={s.get('whip','?')}  K/9={k9 or '?'}  IP={s.get('inningsPitched','?')}")
    home_str = f"{home_era:.2f}" if home_era is not None else "?"
    away_str = f"{away_era:.2f}" if away_era is not None else "?"
    print(f"Splits:  Home ERA={home_str}  Away ERA={away_str}")
    print()

    print(f"LAST {len(splits)} STARTS")
    print(f"{'Date':<12} {'H/A':<5} {'Opponent':<26} {'IP':>4} {'K':>3} {'ER':>3} {'BB':>3} {'H':>3}  ERA")
    print("-" * 65)

    k_list = []
    er_list = []
    for split in splits:
        st = split.get("stat", {})
        opp = split.get("opponent", {}).get("name", "?")[:25]
        is_home = split.get("isHome", False)
        date = split.get("date", "?")
        ip = st.get("inningsPitched", "?")
        k = st.get("strikeOuts", 0)
        er = st.get("earnedRuns", 0)
        bb = st.get("baseOnBalls", 0)
        h = st.get("hits", 0)
        era = st.get("era", "?")
        ha = "HOME" if is_home else "AWAY"
        k_list.append(k)
        er_list.append(er)
        print(f"{date:<12} {ha:<5} {opp:<26} {ip:>4} {k:>3} {er:>3} {bb:>3} {h:>3}  {era}")

    if len(k_list) >= 3:
        recent3_k = sum(k_list[:3]) / 3
        all_k = sum(k_list) / len(k_list)
        trend = "HOT" if recent3_k > all_k + 0.5 else ("COLD" if recent3_k < all_k - 0.5 else "FLAT")
        print(f"\nK trend (last 3 vs all): {recent3_k:.1f}/start vs {all_k:.1f}/start -- {trend}")
        recent3_er = sum(er_list[:3]) / 3
        all_er = sum(er_list) / len(er_list)
        er_trend = "SHARP" if recent3_er < all_er - 0.3 else ("SOFT" if recent3_er > all_er + 0.3 else "STEADY")
        print(f"ER trend (last 3 vs all): {recent3_er:.1f}/start vs {all_er:.1f}/start -- {er_trend}")


def batter_log(name: str, n_games: int = 15):
    pid, full_name = resolve_player_id(name)
    if not pid:
        print(f"Player not found: {name}")
        return

    r = requests.get(f"{BASE}/people/{pid}/stats", params={
        "stats": "gameLog",
        "group": "hitting",
        "season": SEASON,
        "limit": n_games,
    }, timeout=10)
    r.raise_for_status()
    splits = r.json().get("stats", [{}])[0].get("splits", [])

    # L/R splits
    rlr = requests.get(f"{BASE}/people/{pid}/stats", params={
        "stats": "statSplits",
        "group": "hitting",
        "season": SEASON,
        "sitCodes": "vl,vr",
    }, timeout=10)
    vs_l = vs_r = {}
    if rlr.status_code == 200:
        for stat in rlr.json().get("stats", []):
            for split in stat.get("splits", []):
                code = split.get("split", {}).get("code", "")
                if code == "vl":
                    vs_l = split["stat"]
                elif code == "vr":
                    vs_r = split["stat"]

    # Season totals
    rs = requests.get(f"{BASE}/people/{pid}/stats", params={
        "stats": "season",
        "group": "hitting",
        "season": SEASON,
    }, timeout=10)
    season = {}
    if rs.status_code == 200:
        ss = rs.json().get("stats", [{}])[0].get("splits", [])
        season = ss[0].get("stat", {}) if ss else {}

    print(f"\n{'='*70}")
    print(f"BATTER: {full_name}")
    print(f"Season:  AVG={season.get('avg','?')}  OPS={season.get('ops','?')}  "
          f"HR={season.get('homeRuns','?')}  RBI={season.get('rbi','?')}")
    print()

    print(f"L/R SPLITS (season)")
    print(f"{'':10} {'AVG':>6} {'OPS':>6} {'AB':>4} {'H':>3} {'HR':>3} {'RBI':>4} {'K':>3} {'BB':>3}")
    print("-" * 55)
    for label, st in [("vs LHP", vs_l), ("vs RHP", vs_r)]:
        if st:
            ab = st.get("atBats", 0)
            h = st.get("hits", 0)
            hr = st.get("homeRuns", 0)
            rbi = st.get("rbi", 0)
            k = st.get("strikeOuts", 0)
            bb = st.get("baseOnBalls", 0)
            avg = st.get("avg", "?")
            ops = st.get("ops", "?")
            print(f"{label:<10} {avg:>6} {ops:>6} {ab:>4} {h:>3} {hr:>3} {rbi:>4} {k:>3} {bb:>3}")
    print()

    print(f"LAST {len(splits)} GAMES")
    print(f"{'Date':<12} {'Opponent':<26} {'H':>3} {'R':>3} {'RBI':>4} {'H+R+RBI':>8} {'K':>3} {'BB':>3} {'HR':>3} {'TB':>3}")
    print("-" * 70)

    hrbi_list = []
    k_list = []
    h_list = []
    for split in splits:
        st = split.get("stat", {})
        opp = split.get("opponent", {}).get("name", "?")[:25]
        date = split.get("date", "?")
        h = st.get("hits", 0)
        r = st.get("runs", 0)
        rbi = st.get("rbi", 0)
        k = st.get("strikeOuts", 0)
        bb = st.get("baseOnBalls", 0)
        hr = st.get("homeRuns", 0)
        tb = st.get("totalBases", 0)
        hrbi = h + r + rbi
        hrbi_list.append(hrbi)
        k_list.append(k)
        h_list.append(h)
        print(f"{date:<12} {opp:<26} {h:>3} {r:>3} {rbi:>4} {hrbi:>8} {k:>3} {bb:>3} {hr:>3} {tb:>3}")

    if hrbi_list:
        n = len(hrbi_list)
        avg15 = sum(hrbi_list) / n
        avg5 = sum(hrbi_list[:5]) / min(5, n)
        k_avg = sum(k_list) / n
        h_avg = sum(h_list) / n
        trend = "HOT" if avg5 > avg15 + 0.3 else ("COLD" if avg5 < avg15 - 0.3 else "FLAT")
        print(f"\nH+R+RBI: last 5 avg={avg5:.2f}  last {n} avg={avg15:.2f}  -- {trend}")
        print(f"Hits/g={h_avg:.2f}  Ks/g={k_avg:.2f}")


def head_to_head(batter_name: str, pitcher_name: str):
    batter_id, batter_full = resolve_player_id(batter_name)
    pitcher_id, pitcher_full = resolve_player_id(pitcher_name)

    if not batter_id or not pitcher_id:
        print(f"Could not resolve: {batter_name} or {pitcher_name}")
        return

    r = requests.get(f"{BASE}/people/{batter_id}/stats", params={
        "stats": "vsPlayer",
        "group": "hitting",
        "opposingPlayerId": pitcher_id,
    }, timeout=10)
    r.raise_for_status()

    print(f"\n{'='*60}")
    print(f"HEAD-TO-HEAD: {batter_full} vs {pitcher_full}")
    print()

    all_splits = []
    for stat in r.json().get("stats", []):
        all_splits.extend(stat.get("splits", []))

    if not all_splits:
        print("No head-to-head history found.")
        return

    # Group by season
    by_season: dict[str, dict] = {}
    for split in all_splits:
        season = split.get("season") or split.get("stat", {}).get("season") or "career"
        st = split.get("stat", {})
        by_season[season] = st

    print(f"{'Season':<8} {'AB':>4} {'H':>3} {'HR':>3} {'RBI':>4} {'K':>3} {'BB':>3} {'AVG':>6}")
    print("-" * 45)
    total_ab = total_h = total_hr = total_rbi = total_k = total_bb = 0

    for season_key in sorted(by_season.keys(), reverse=True):
        st = by_season[season_key]
        ab = st.get("atBats", 0)
        h = st.get("hits", 0)
        hr = st.get("homeRuns", 0)
        rbi = st.get("rbi", 0)
        k = st.get("strikeOuts", 0)
        bb = st.get("baseOnBalls", 0)
        avg = st.get("avg", ".---")
        if ab == 0 and h == 0:
            continue
        total_ab += ab; total_h += h; total_hr += hr
        total_rbi += rbi; total_k += k; total_bb += bb
        print(f"{season_key:<8} {ab:>4} {h:>3} {hr:>3} {rbi:>4} {k:>3} {bb:>3} {avg:>6}")

    if total_ab > 0:
        career_avg = f".{int(total_h/total_ab*1000):03d}"
        print("-" * 45)
        print(f"{'CAREER':<8} {total_ab:>4} {total_h:>3} {total_hr:>3} {total_rbi:>4} {total_k:>3} {total_bb:>3} {career_avg:>6}")
        k_rate = round(total_k / total_ab * 100, 1)
        h_rate = round(total_h / total_ab * 100, 1)
        print(f"\nK rate: {k_rate}%  |  Hit rate: {h_rate}%  |  Sample: {total_ab} AB")
        if total_k >= 3:
            verdict = "FADE batter" if k_rate >= 33 else ("TRUST batter" if h_rate >= 30 else "neutral")
            print(f"Signal: {verdict}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    p_pit = sub.add_parser("pitcher", help="Pitcher per-start log")
    p_pit.add_argument("name", help="Pitcher name (partial ok)")
    p_pit.add_argument("--starts", type=int, default=5, help="Number of starts (default: 5)")

    p_bat = sub.add_parser("batter", help="Batter per-game log + L/R splits")
    p_bat.add_argument("name", help="Batter name (partial ok)")
    p_bat.add_argument("--games", type=int, default=15, help="Number of games (default: 15)")

    p_mu = sub.add_parser("matchup", help="Head-to-head batter vs pitcher")
    p_mu.add_argument("batter", help="Batter name")
    p_mu.add_argument("pitcher", help="Pitcher name")

    args = parser.parse_args()

    if args.cmd == "pitcher":
        pitcher_log(args.name, args.starts)
    elif args.cmd == "batter":
        batter_log(args.name, args.games)
    elif args.cmd == "matchup":
        head_to_head(args.batter, args.pitcher)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
