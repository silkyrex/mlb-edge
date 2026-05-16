"""
cache_espn.py -- augment player_recent_stats with ESPN pitcher season stats

Adds espn_war, espn_fip, espn_k_bb to any pitcher rows already cached by cache_stats.py.
Run after cache_stats.py for the same game. Idempotent.

Usage:
    python cache_espn.py --game "SF Giants @ Athletics"
    python cache_espn.py --game "SF Giants @ Athletics" --date 2026-05-16
"""

import argparse
import requests
import sqlite3
from datetime import date as date_cls
from pathlib import Path

PICKS_DB = Path(__file__).parent / "mlb.db"
ESPN_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb"
    "/statistics/byathlete?category=pitching&season={season}&seasontype=2&limit=300"
)
FIP_CONSTANT = 3.1
SEASON = str(date_cls.today().year)


def fetch_espn_pitchers() -> dict[str, dict]:
    r = requests.get(ESPN_URL.format(season=SEASON), timeout=15)
    r.raise_for_status()
    data = r.json()

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
        k = row.get("K") or 0
        bb = row.get("BB") or 0
        hr = row.get("HR") or 0
        war = row.get("WAR")

        fip = None
        if ip and ip > 0:
            fip = round((13 * hr + 3 * bb - 2 * k) / ip + FIP_CONSTANT, 2)

        k_bb = round(k / bb, 2) if bb and bb > 0 else None

        result[name.lower()] = {
            "espn_war": round(war, 2) if war is not None else None,
            "espn_fip": fip,
            "espn_k_bb": k_bb,
        }

    return result


def match_name(player: str, espn_map: dict[str, dict]) -> dict | None:
    key = player.lower()
    if key in espn_map:
        return espn_map[key]
    last = player.split()[-1].lower()
    for espn_name, stats in espn_map.items():
        if espn_name.split()[-1] == last:
            return stats
    return None


def augment_game(game_name: str, game_date: str):
    conn = sqlite3.connect(PICKS_DB)
    conn.row_factory = sqlite3.Row

    pitchers = conn.execute("""
        SELECT DISTINCT player FROM player_recent_stats
        WHERE cache_date=? AND player_type='pitcher'
        AND player IN (
            SELECT DISTINCT player FROM mlb_game_lines
            WHERE game=? AND scraped_date=?
        )
    """, (game_date, game_name, game_date)).fetchall()

    if not pitchers:
        print(f"No cached pitchers for '{game_name}' on {game_date}. Run cache_stats.py first.")
        conn.close()
        return

    print(f"Fetching ESPN pitcher stats for season {SEASON}...")
    try:
        espn_map = fetch_espn_pitchers()
    except Exception as e:
        print(f"ESPN fetch failed: {e}")
        conn.close()
        return
    print(f"ESPN returned {len(espn_map)} pitchers\n")

    updated = skipped = 0
    for row in pitchers:
        player = row["player"]
        stats = match_name(player, espn_map)
        if not stats:
            print(f"  SKIP {player}: not in ESPN qualified list")
            skipped += 1
            continue

        conn.execute("""
            UPDATE player_recent_stats
            SET espn_war=?, espn_fip=?, espn_k_bb=?
            WHERE player=? AND cache_date=? AND player_type='pitcher'
        """, (stats["espn_war"], stats["espn_fip"], stats["espn_k_bb"], player, game_date))

        print(
            f"  {player:<22} WAR={stats['espn_war']:>5}  "
            f"FIP={stats['espn_fip']:>5}  K/BB={stats['espn_k_bb']}"
        )
        updated += 1

    conn.commit()
    conn.close()
    print(f"\n{updated} updated, {skipped} skipped (not in ESPN qualified list)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True, help="e.g. 'SF Giants @ Athletics'")
    parser.add_argument("--date", default=str(date_cls.today()), help="YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    augment_game(args.game, args.date)


if __name__ == "__main__":
    main()
