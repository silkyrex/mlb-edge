"""
discord_push.py -- post today's MLB matchup signals to Discord

Usage:
  python discord_push.py              # today
  python discord_push.py 2026-05-16   # specific date

Reads SPORTS_WEBHOOK_URL from environment or .env file.
"""

import os
import sys
import urllib.request
import urllib.error
import json
from datetime import date
from matchup import run as get_matchups, team_tiers, pitcher_tiers
import requests as _req

BASE = "https://statsapi.mlb.com/api/v1"


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def build_message(game_date: str) -> str:
    t_tiers = team_tiers()
    p_tiers = pitcher_tiers()

    r = _req.get(f"{BASE}/schedule", params={
        "sportId": 1,
        "date": game_date,
        "hydrate": "probablePitcher",
    })
    r.raise_for_status()

    from matchup import signal

    games = []
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            away_t = g["teams"]["away"]["team"]["name"]
            home_t = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName")

            away_team_t = t_tiers.get(away_t, "mid")
            home_team_t = t_tiers.get(home_t, "mid")
            away_p_t, away_era = p_tiers.get(away_p, ("?", None)) if away_p else ("?", None)
            home_p_t, home_era = p_tiers.get(home_p, ("?", None)) if home_p else ("?", None)

            sigs = signal(away_team_t, home_team_t, away_p_t, home_p_t)
            skip = sigs[0].startswith("no signal")

            games.append({
                "away_t": away_t, "home_t": home_t,
                "away_team_t": away_team_t, "home_team_t": home_team_t,
                "away_p": away_p or "TBD", "home_p": home_p or "TBD",
                "away_p_t": away_p_t, "home_p_t": home_p_t,
                "away_era": away_era, "home_era": home_era,
                "signals": sigs, "skip": skip,
            })

    def sort_key(g):
        s = g["signals"][0]
        if "LOCK" in s: return 0
        if "lean" in s: return 1
        return 2

    games.sort(key=sort_key)
    actionable = [g for g in games if not g["skip"]]
    skipped    = len(games) - len(actionable)

    lines = [f"**MLB MATCHUPS -- {game_date}**"]

    if not actionable:
        lines.append("No signals today -- all middle matchups.")
    else:
        for g in actionable:
            sig = g["signals"][0]
            prefix = "🔒" if "LOCK" in sig else "➡️"
            lines.append(f"\n{prefix} **{sig.upper()}**")
            lines.append(f"  {g['away_t']} [{g['away_team_t']}] @ {g['home_t']} [{g['home_team_t']}]")

            ap = g["away_p"]
            ae = f" ERA {g['away_era']}" if g["away_era"] is not None else ""
            lines.append(f"  away: {ap} [{g['away_p_t']}]{ae}")

            hp = g["home_p"]
            he = f" ERA {g['home_era']}" if g["home_era"] is not None else ""
            lines.append(f"  home: {hp} [{g['home_p_t']}]{he}")

    lines.append(f"\n_{skipped} games skipped (no signal)_")
    return "\n".join(lines)


def post(webhook_url: str, message: str):
    payload = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "mlb-edge/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"Posted -- HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"Failed -- HTTP {e.code}: {e.read().decode()}")


if __name__ == "__main__":
    load_env()
    webhook = os.environ.get("SPORTS_WEBHOOK_URL")
    if not webhook:
        print("SPORTS_WEBHOOK_URL not set -- check .env")
        sys.exit(1)

    game_date = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
    msg = build_message(game_date)
    print(msg)
    print()
    post(webhook, msg)
