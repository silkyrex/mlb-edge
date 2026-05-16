"""
cache_tomorrow.py -- pre-cache IL/injury status for all of tomorrow's games

Called by daily.sh at 11pm PT. Fetches the next day's schedule and runs
cache_news --roster for each game so injury data is ready before noon.

Usage:
    python cache_tomorrow.py                     # default: tomorrow
    python cache_tomorrow.py --date 2026-05-16
"""

import argparse
import requests
import subprocess
import sys
from datetime import date as date_cls, timedelta
from pathlib import Path

BASE = "https://statsapi.mlb.com/api/v1"
PYTHON = sys.executable
DIR = Path(__file__).parent


def get_games(game_date: str) -> list[str]:
    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "date": game_date}, timeout=10)
    if r.status_code != 200:
        print(f"Schedule fetch failed: {r.status_code}")
        return []
    names = []
    for d in r.json().get("dates", []):
        for game in d.get("games", []):
            teams = game["teams"]
            away = teams["away"]["team"]["name"]
            home = teams["home"]["team"]["name"]
            names.append(f"{away} @ {home}")
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date_cls.today() + timedelta(days=1)))
    args = parser.parse_args()

    games = get_games(args.date)
    if not games:
        print(f"No games found for {args.date}")
        return

    print(f"Pre-caching IL status for {len(games)} games on {args.date}")
    ok = failed = 0

    for game in games:
        print(f"  {game} ... ", end="", flush=True)
        result = subprocess.run(
            [PYTHON, str(DIR / "cache_news.py"), "--game", game, "--date", args.date, "--roster"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            # Count flagged players
            flagged = result.stdout.count("[IL-")
            print(f"ok ({flagged} IL flags)" if flagged else "ok")
            ok += 1
        else:
            print(f"FAILED: {result.stderr[:100]}")
            failed += 1

    print(f"\n{ok} cached, {failed} failed")


if __name__ == "__main__":
    main()
