"""
cache_stats.py -- pre-cache player recent stats from MLB Stats API

Run before /underdog-mlb-analyze to populate player_recent_stats in picks.db.
The analyze skill reads from this table instead of doing web searches.

Usage:
    python cache_stats.py --game "SF Giants @ Athletics"
    python cache_stats.py --game "SF Giants @ Athletics" --date 2026-05-15
    python cache_stats.py --game "SF Giants @ Athletics" --query
"""

import argparse
import json
import requests
import sqlite3
from datetime import date as date_cls
from pathlib import Path

PICKS_DB = Path(__file__).parent / "mlb.db"
BASE = "https://statsapi.mlb.com/api/v1"
SEASON = str(date_cls.today().year)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(PICKS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def find_game_pk(game_name: str, game_date: str) -> int | None:
    parts = game_name.lower().split(" @ ")
    if len(parts) != 2:
        return None
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
                return game["gamePk"]
    return None


def get_player_ids_from_boxscore(game_pk: int) -> dict[str, int]:
    r = requests.get(f"{BASE}/game/{game_pk}/boxscore", timeout=10)
    if r.status_code != 200:
        return {}
    name_to_id: dict[str, int] = {}
    for side in ("home", "away"):
        for pdata in r.json().get("teams", {}).get(side, {}).get("players", {}).values():
            person = pdata.get("person", {})
            name = person.get("fullName", "")
            pid = person.get("id")
            if name and pid:
                name_to_id[name] = pid
    return name_to_id


def get_player_ids_from_rosters(game_pk: int, game_date: str) -> dict[str, int]:
    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "date": game_date}, timeout=10)
    r.raise_for_status()
    team_ids: list[int] = []
    for d in r.json().get("dates", []):
        for game in d.get("games", []):
            if game["gamePk"] == game_pk:
                teams = game["teams"]
                team_ids.append(teams["home"]["team"]["id"])
                team_ids.append(teams["away"]["team"]["id"])

    name_to_id: dict[str, int] = {}
    for tid in team_ids:
        r2 = requests.get(f"{BASE}/teams/{tid}/roster", params={"season": SEASON}, timeout=10)
        if r2.status_code != 200:
            continue
        for entry in r2.json().get("roster", []):
            person = entry.get("person", {})
            name = person.get("fullName", "")
            pid = person.get("id")
            if name and pid:
                name_to_id[name] = pid
    return name_to_id


def _fetch_stats(player_id: int, group: str, stat_type: str, limit: int | None = None, extra: dict | None = None) -> list[dict]:
    params: dict = {"stats": stat_type, "group": group, "season": SEASON}
    if limit:
        params["limit"] = limit
    if extra:
        params.update(extra)
    r = requests.get(f"{BASE}/people/{player_id}/stats", params=params, timeout=10)
    if r.status_code != 200:
        return []
    return r.json().get("stats", [{}])[0].get("splits", [])


def _float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def compute_pitcher_cache(player_id: int) -> dict:
    logs = _fetch_stats(player_id, "pitching", "gameLog", limit=5)
    season_splits = _fetch_stats(player_id, "pitching", "season")
    season = season_splits[0].get("stat", {}) if season_splits else {}

    last5_ks = [s["stat"].get("strikeOuts", 0) for s in logs]
    last5_ip = [_float(s["stat"].get("inningsPitched", 0)) or 0.0 for s in logs]

    # pitcher_role: starter if avg IP >= 4.0, reliever if < 3.0, mixed otherwise
    avg_ip = sum(last5_ip) / len(last5_ip) if last5_ip else 0
    pitcher_role = "starter" if avg_ip >= 4.0 else ("reliever" if avg_ip < 3.0 else "mixed")

    total_ip = sum(last5_ip)
    total_er = sum(s["stat"].get("earnedRuns", 0) for s in logs)
    recent_era = round(total_er * 9 / total_ip, 2) if total_ip > 0 else None

    season_ip = _float(season.get("inningsPitched", 0)) or 0
    season_k = season.get("strikeOuts", 0)
    season_k9 = round(season_k * 9 / season_ip, 2) if season_ip > 0 else None

    # Home/away ERA splits
    ha_splits = _fetch_stats(player_id, "pitching", "statSplits", extra={"sitCodes": "h,a"})
    home_era = away_era = None
    for split in ha_splits:
        code = split.get("split", {}).get("code", "")
        if code == "h":
            home_era = _float(split["stat"].get("era"))
        elif code == "a":
            away_era = _float(split["stat"].get("era"))

    # Expected stats (xBA, xSLG, xwOBA against -- regression signal)
    x_splits = _fetch_stats(player_id, "pitching", "expectedStatistics")
    x_stat = x_splits[0].get("stat", {}) if x_splits else {}
    x_woba_against = _float(x_stat.get("woba"))
    x_avg_against  = _float(x_stat.get("avg"))

    return {
        "player_type": "pitcher",
        "mlb_player_id": player_id,
        "games_lookback": len(logs),
        "last5_ks": json.dumps(last5_ks),
        "last5_ip": json.dumps(last5_ip),
        "pitcher_role": pitcher_role,
        "season_k9": season_k9,
        "season_era": _float(season.get("era")),
        "season_whip": _float(season.get("whip")),
        "recent_era": recent_era,
        "split_home_era": home_era,
        "split_away_era": away_era,
        "x_woba_against": x_woba_against,
        "x_avg_against": x_avg_against,
    }


def compute_batter_cache(player_id: int) -> dict:
    logs = _fetch_stats(player_id, "hitting", "gameLog", limit=15)
    season_splits = _fetch_stats(player_id, "hitting", "season")
    season = season_splits[0].get("stat", {}) if season_splits else {}

    n = len(logs)
    if n == 0:
        return {"player_type": "batter", "mlb_player_id": player_id, "games_lookback": 0}

    def avg(vals: list) -> float:
        return round(sum(vals) / n, 2)

    h_vals = [s["stat"].get("hits", 0) for s in logs]
    r_vals = [s["stat"].get("runs", 0) for s in logs]
    rbi_vals = [s["stat"].get("rbi", 0) for s in logs]
    k_vals = [s["stat"].get("strikeOuts", 0) for s in logs]
    hr_vals = [s["stat"].get("homeRuns", 0) for s in logs]
    tb_vals = [s["stat"].get("totalBases", 0) for s in logs]

    # L/R and home/away splits
    lr_splits = _fetch_stats(player_id, "hitting", "statSplits", extra={"sitCodes": "vl,vr,h,a"})
    vs_lhp_avg = vs_lhp_ops = vs_lhp_ab = None
    vs_rhp_avg = vs_rhp_ops = vs_rhp_ab = None
    split_home_avg = split_home_ops = split_home_ab = None
    split_away_avg = split_away_ops = split_away_ab = None
    for split in lr_splits:
        code = split.get("split", {}).get("code", "")
        st = split.get("stat", {})
        if code == "vl":
            vs_lhp_avg = _float(st.get("avg"))
            vs_lhp_ops = _float(st.get("ops"))
            vs_lhp_ab  = st.get("atBats")
        elif code == "vr":
            vs_rhp_avg = _float(st.get("avg"))
            vs_rhp_ops = _float(st.get("ops"))
            vs_rhp_ab  = st.get("atBats")
        elif code == "h":
            split_home_avg = _float(st.get("avg"))
            split_home_ops = _float(st.get("ops"))
            split_home_ab  = st.get("atBats")
        elif code == "a":
            split_away_avg = _float(st.get("avg"))
            split_away_ops = _float(st.get("ops"))
            split_away_ab  = st.get("atBats")

    # Expected stats (xBA, xwOBA -- regression signal)
    x_splits = _fetch_stats(player_id, "hitting", "expectedStatistics")
    x_stat = x_splits[0].get("stat", {}) if x_splits else {}
    x_woba = _float(x_stat.get("woba"))
    x_avg  = _float(x_stat.get("avg"))
    x_slg  = _float(x_stat.get("slg"))

    # wOBA delta: positive = outperforming contact quality (regression risk)
    actual_woba = _float(season.get("obp"))  # approximation; real wOBA not in season stats
    woba_delta = None  # computed in player.py display layer

    return {
        "player_type": "batter",
        "mlb_player_id": player_id,
        "games_lookback": n,
        "last15_h_r_rbi": avg([h + r + rbi for h, r, rbi in zip(h_vals, r_vals, rbi_vals)]),
        "last15_hits": avg(h_vals),
        "last15_ks_batter": avg(k_vals),
        "last15_hr": avg(hr_vals),
        "last15_tb": avg(tb_vals),
        "season_avg": _float(season.get("avg")),
        "season_ops": _float(season.get("ops")),
        "vs_lhp_avg": vs_lhp_avg,
        "vs_lhp_ops": vs_lhp_ops,
        "vs_lhp_ab": vs_lhp_ab,
        "vs_rhp_avg": vs_rhp_avg,
        "vs_rhp_ops": vs_rhp_ops,
        "vs_rhp_ab": vs_rhp_ab,
        "split_home_avg": split_home_avg,
        "split_home_ops": split_home_ops,
        "split_home_ab":  split_home_ab,
        "split_away_avg": split_away_avg,
        "split_away_ops": split_away_ops,
        "split_away_ab":  split_away_ab,
        "x_avg": x_avg,
        "x_slg": x_slg,
        "x_woba": x_woba,
    }


def upsert_cache(conn: sqlite3.Connection, player: str, cache_date: str, data: dict):
    fields = [
        "player", "player_type", "cache_date", "mlb_player_id", "games_lookback",
        "last5_ks", "last5_ip", "pitcher_role",
        "season_k9", "season_era", "season_whip", "recent_era",
        "split_home_era", "split_away_era",
        "x_woba_against", "x_avg_against",
        "last15_h_r_rbi", "last15_hits", "last15_ks_batter", "last15_hr", "last15_tb",
        "season_avg", "season_ops",
        "vs_lhp_avg", "vs_lhp_ops", "vs_lhp_ab",
        "vs_rhp_avg", "vs_rhp_ops", "vs_rhp_ab",
        "split_home_avg", "split_home_ops", "split_home_ab",
        "split_away_avg", "split_away_ops", "split_away_ab",
        "x_avg", "x_slg", "x_woba",
    ]
    values = [
        player, data.get("player_type"), cache_date,
        data.get("mlb_player_id"), data.get("games_lookback"),
        data.get("last5_ks"), data.get("last5_ip"), data.get("pitcher_role"),
        data.get("season_k9"), data.get("season_era"),
        data.get("season_whip"), data.get("recent_era"),
        data.get("split_home_era"), data.get("split_away_era"),
        data.get("x_woba_against"), data.get("x_avg_against"),
        data.get("last15_h_r_rbi"), data.get("last15_hits"), data.get("last15_ks_batter"),
        data.get("last15_hr"), data.get("last15_tb"),
        data.get("season_avg"), data.get("season_ops"),
        data.get("vs_lhp_avg"), data.get("vs_lhp_ops"), data.get("vs_lhp_ab"),
        data.get("vs_rhp_avg"), data.get("vs_rhp_ops"), data.get("vs_rhp_ab"),
        data.get("split_home_avg"), data.get("split_home_ops"), data.get("split_home_ab"),
        data.get("split_away_avg"), data.get("split_away_ops"), data.get("split_away_ab"),
        data.get("x_avg"), data.get("x_slg"), data.get("x_woba"),
    ]
    placeholders = ",".join("?" * len(fields))
    updates = ", ".join(f"{f}=excluded.{f}" for f in fields if f not in ("player", "cache_date"))
    conn.execute(
        f"INSERT INTO player_recent_stats ({','.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(player, cache_date) DO UPDATE SET {updates}",
        values,
    )


def resolve_player_id(player: str, name_to_id: dict[str, int]) -> int | None:
    if player in name_to_id:
        return name_to_id[player]
    last = player.split()[-1]
    for name, pid in name_to_id.items():
        if last in name.split():
            return pid
    return None


def cache_game(game_name: str, game_date: str):
    conn = get_conn()

    rows = conn.execute("""
        SELECT DISTINCT player, player_type
        FROM mlb_game_lines
        WHERE game=? AND scraped_date=? AND player_type IN ('pitcher', 'batter')
        ORDER BY player_type, player
    """, (game_name, game_date)).fetchall()

    if not rows:
        print(f"No lines found for '{game_name}' on {game_date}. Run /underdog-mlb first.")
        conn.close()
        return

    print(f"Caching stats for {len(rows)} players -- {game_name} ({game_date})")

    game_pk = find_game_pk(game_name, game_date)
    if not game_pk:
        print(f"ERROR: Could not find game_pk for '{game_name}' on {game_date}")
        conn.close()
        return

    print(f"Game PK: {game_pk}")

    name_to_id = get_player_ids_from_boxscore(game_pk)
    if not name_to_id:
        name_to_id = get_player_ids_from_rosters(game_pk, game_date)
    print(f"Resolved {len(name_to_id)} player IDs\n")

    cached = skipped = 0
    for row in rows:
        player = row["player"]
        player_type = row["player_type"]

        player_id = resolve_player_id(player, name_to_id)
        if not player_id:
            print(f"  SKIP {player}: player ID not found")
            skipped += 1
            continue

        print(f"  {player} ({player_type}) ... ", end="", flush=True)
        try:
            if player_type == "pitcher":
                data = compute_pitcher_cache(player_id)
                ks = json.loads(data.get("last5_ks") or "[]")
                summary = f"ERA={data.get('season_era')}, K/9={data.get('season_k9')}, WHIP={data.get('season_whip')}, last5Ks={ks}"
            else:
                data = compute_batter_cache(player_id)
                summary = (
                    f"H+R+RBI/g={data.get('last15_h_r_rbi')}, "
                    f"Hits/g={data.get('last15_hits')}, "
                    f"Ks/g={data.get('last15_ks_batter')}, "
                    f"AVG={data.get('season_avg')}"
                )

            upsert_cache(conn, player, game_date, data)
            print(summary)
            cached += 1
        except Exception as e:
            print(f"ERROR: {e}")
            skipped += 1

    conn.commit()
    conn.close()
    print(f"\n{cached} cached, {skipped} skipped")
    print(f'Query: python cache_stats.py --game "{game_name}" --query')


def query_cache(game_name: str, game_date: str):
    conn = get_conn()

    rows = conn.execute("""
        SELECT DISTINCT l.player, l.player_type, l.team,
               p.games_lookback, p.cache_date,
               p.season_era, p.season_k9, p.season_whip, p.recent_era, p.last5_ks,
               p.last15_h_r_rbi, p.last15_hits, p.last15_ks_batter,
               p.last15_hr, p.season_avg, p.season_ops
        FROM mlb_game_lines l
        LEFT JOIN player_recent_stats p ON p.player=l.player AND p.cache_date=?
        WHERE l.game=? AND l.scraped_date=? AND l.player_type IN ('pitcher', 'batter')
        ORDER BY l.player_type DESC, l.player
    """, (game_date, game_name, game_date)).fetchall()

    conn.close()

    if not rows:
        print(f"No data for '{game_name}' on {game_date}")
        return

    print(f"\nCACHED STATS -- {game_name} -- {game_date}\n")

    pitchers = [r for r in rows if r["player_type"] == "pitcher"]
    batters = [r for r in rows if r["player_type"] == "batter"]

    print("PITCHERS")
    print(f"{'Player':<22} {'ERA':>6} {'K/9':>5} {'WHIP':>6} {'Rec.ERA':>8} {'Last 5 Ks'}")
    print("-" * 70)
    for r in pitchers:
        ks = r["last5_ks"] or "[]"
        era = f"{r['season_era']:.2f}" if r["season_era"] else "--"
        k9 = f"{r['season_k9']:.1f}" if r["season_k9"] else "--"
        whip = f"{r['season_whip']:.2f}" if r["season_whip"] else "--"
        rec = f"{r['recent_era']:.2f}" if r["recent_era"] else "--"
        tag = "" if r["cache_date"] else " [NOT CACHED]"
        print(f"{r['player']:<22} {era:>6} {k9:>5} {whip:>6} {rec:>8} {ks}{tag}")

    print("\nBATTERS")
    print(f"{'Player':<22} {'H+R+RBI/g':>10} {'Hits/g':>7} {'Ks/g':>6} {'HR/g':>6} {'AVG':>6} {'OPS':>6}")
    print("-" * 78)
    for r in batters:
        h_r_rbi = f"{r['last15_h_r_rbi']:.2f}" if r["last15_h_r_rbi"] is not None else "--"
        hits = f"{r['last15_hits']:.2f}" if r["last15_hits"] is not None else "--"
        ks = f"{r['last15_ks_batter']:.2f}" if r["last15_ks_batter"] is not None else "--"
        hr = f"{r['last15_hr']:.2f}" if r["last15_hr"] is not None else "--"
        avg = f"{r['season_avg']:.3f}" if r["season_avg"] else "--"
        ops = f"{r['season_ops']:.3f}" if r["season_ops"] else "--"
        tag = "" if r["cache_date"] else " [NOT CACHED]"
        print(f"{r['player']:<22} {h_r_rbi:>10} {hits:>7} {ks:>6} {hr:>6} {avg:>6} {ops:>6}{tag}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True, help="Game name, e.g. 'SF Giants @ Athletics'")
    parser.add_argument("--date", default=str(date_cls.today()), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--query", action="store_true", help="Show cached stats instead of fetching")
    args = parser.parse_args()

    if args.query:
        query_cache(args.game, args.date)
    else:
        cache_game(args.game, args.date)


if __name__ == "__main__":
    main()
