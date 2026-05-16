"""
morning_brief.py -- daily 9am pre-game brief

Pulls tomorrow's MLB schedule, grades probable starters, flags IL returns,
and posts a summary to Discord (or prints to terminal).

Usage:
    python morning_brief.py               # print to terminal
    python morning_brief.py --post        # post to Discord
    python morning_brief.py --date 2026-05-16  # specific date
"""

import argparse
import json
import os
import re
import requests
from datetime import date as date_cls, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE = "https://statsapi.mlb.com/api/v1"
SEASON = str(date_cls.today().year)
WEBHOOK_URL = os.getenv("SPORTS_WEBHOOK_URL", "")

ERA_ELITE = 3.00
ERA_FADE  = 4.50


def grade_era(era: float | None) -> str:
    if era is None:
        return "?"
    if era <= ERA_ELITE:
        return "ELITE"
    if era >= ERA_FADE:
        return "FADE"
    return "mid"


def _float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def get_schedule(game_date: str) -> list[dict]:
    r = requests.get(f"{BASE}/schedule", params={
        "sportId": 1,
        "date": game_date,
        "hydrate": "probablePitcher",
    }, timeout=10)
    r.raise_for_status()
    games = []
    for d in r.json().get("dates", []):
        games.extend(d.get("games", []))
    return games


def get_pitcher_stats(player_id: int) -> dict:
    r = requests.get(f"{BASE}/people/{player_id}/stats", params={
        "stats": "season",
        "group": "pitching",
        "season": SEASON,
    }, timeout=10)
    if r.status_code != 200:
        return {}
    splits = r.json().get("stats", [{}])[0].get("splits", [])
    return splits[0].get("stat", {}) if splits else {}


def get_recent_il_returns(team_ids: list[int], game_date: str) -> list[str]:
    """Players who returned from IL in the last 7 days."""
    start = str(date_cls.fromisoformat(game_date) - timedelta(days=7))
    as_of = date_cls.fromisoformat(game_date)
    activated = []
    il_re = re.compile(r"activated .+ from the (\d+)-day injured list", re.I)

    for tid in team_ids:
        r = requests.get(f"{BASE}/transactions", params={
            "sportId": 1, "teamId": tid,
            "startDate": start, "endDate": game_date,
        }, timeout=10)
        if r.status_code != 200:
            continue
        for t in r.json().get("transactions", []):
            if t.get("typeCode") != "SC":
                continue
            desc = t.get("description", "")
            if il_re.search(desc):
                txn_date = date_cls.fromisoformat(t["date"])
                days_ago = (as_of - txn_date).days
                name = t["person"]["fullName"]
                tag = "today" if days_ago == 0 else f"{days_ago}d ago"
                activated.append(f"{name} (returned {tag})")
    return activated


def utc_to_pt(utc_str: str) -> str:
    h, m = int(utc_str[:2]), int(utc_str[3:5])
    h = (h - 7) % 24
    ampm = "am" if h < 12 else "pm"
    h12 = h if h <= 12 else h - 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{m:02d}{ampm} PT"


def build_brief(game_date: str) -> str:
    games = get_schedule(game_date)
    if not games:
        return f"No games found for {game_date}."

    lines = [f"**MLB Brief -- {game_date}**  ({len(games)} games)\n"]

    team_ids_all: list[int] = []
    game_blocks: list[str] = []

    for g in games:
        teams = g["teams"]
        away_team = teams["away"]["team"]["name"]
        home_team = teams["home"]["team"]["name"]
        away_id = teams["away"]["team"]["id"]
        home_id = teams["home"]["team"]["id"]
        team_ids_all.extend([away_id, home_id])

        away_p = teams["away"].get("probablePitcher", {})
        home_p = teams["home"].get("probablePitcher", {})

        game_time = utc_to_pt(g.get("gameDate", "00:00")[11:16])

        block = [f"**{away_team} @ {home_team}**  {game_time}"]

        for label, pitcher in [("Away", away_p), ("Home", home_p)]:
            if not pitcher:
                block.append(f"  {label}: TBD")
                continue
            pid = pitcher.get("id")
            name = pitcher.get("fullName", "TBD")
            stats = get_pitcher_stats(pid) if pid else {}
            era = _float(stats.get("era"))
            whip = _float(stats.get("whip"))
            ip = _float(stats.get("inningsPitched")) or 0
            ks = stats.get("strikeOuts", 0)
            k9 = round(ks * 9 / ip, 1) if ip > 0 else None
            grade = grade_era(era)

            era_str = f"{era:.2f}" if era is not None else "?"
            whip_str = f"{whip:.2f}" if whip is not None else "?"
            k9_str = f"{k9}" if k9 is not None else "?"
            block.append(f"  {label}: {name} [{grade}] ERA {era_str} WHIP {whip_str} K/9 {k9_str}")

        game_blocks.append("\n".join(block))

    lines.append("\n\n".join(game_blocks))

    # IL returns across all teams in today's slate
    il_returns = get_recent_il_returns(list(set(team_ids_all)), game_date)
    if il_returns:
        lines.append(f"\n**Recent IL Returns (last 7 days):**")
        for r in il_returns:
            lines.append(f"  {r}")

    lines.append(f"\n_Lean signal arrives 12pm PT via Discord._")
    return "\n".join(lines)


def post_to_discord(message: str):
    if not WEBHOOK_URL:
        print("No SPORTS_WEBHOOK_URL set -- printing only.")
        return
    r = requests.post(WEBHOOK_URL, json={"content": message},
                      headers={"User-Agent": "mlb-edge/1.0"}, timeout=10)
    if r.status_code in (200, 204):
        print("Posted to Discord.")
    else:
        print(f"Discord post failed: {r.status_code} {r.text}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date_cls.today() + timedelta(days=1)),
                        help="Date to brief (default: tomorrow)")
    parser.add_argument("--post", action="store_true", help="Post to Discord")
    args = parser.parse_args()

    brief = build_brief(args.date)
    print(brief)

    if args.post:
        post_to_discord(brief)


if __name__ == "__main__":
    main()
