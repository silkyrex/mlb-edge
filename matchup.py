"""
matchup.py -- tonight's games ranked by ELITE/FADE signal

Usage:
  python matchup.py              # today
  python matchup.py 2026-05-16   # specific date
"""

import sys
import requests
from datetime import date
from db import connect

BASE       = "https://statsapi.mlb.com/api/v1"
ELITE_PCT  = 0.20
FADE_PCT   = 0.20
STARTER_IP = 30.0


# ── tier builders ──────────────────────────────────────────────────────────────

def team_tiers() -> dict[str, str]:
    conn = connect()
    rows = conn.execute("""
        SELECT t.name, ROUND(AVG(t.rf - t.ra), 2) as diff
        FROM (
            SELECT home_team as name, home_score as rf, away_score as ra FROM games WHERE home_score IS NOT NULL
            UNION ALL
            SELECT away_team, away_score, home_score FROM games WHERE away_score IS NOT NULL
        ) t
        GROUP BY t.name
        ORDER BY diff DESC
    """).fetchall()
    conn.close()
    total = len(rows)
    elite_n = int(total * ELITE_PCT)
    fade_n  = int(total * FADE_PCT)
    result = {}
    for i, r in enumerate(rows):
        rank = i + 1
        if rank <= elite_n:
            result[r["name"]] = "ELITE"
        elif rank > total - fade_n:
            result[r["name"]] = "FADE"
        else:
            result[r["name"]] = "mid"
    return result


def pitcher_tiers() -> dict[str, tuple[str, float]]:
    """Returns {name: (tier, era)} for pitchers with enough IP."""
    conn = connect()
    rows = conn.execute(f"""
        SELECT p.name,
               ROUND(SUM(l.er) * 9.0 / NULLIF(SUM(l.ip), 0), 2) as era,
               SUM(l.ip) as ip
        FROM player_game_logs l
        JOIN players p ON p.player_id = l.player_id
        WHERE l.stat_type = 'pitching' AND l.ip IS NOT NULL
        GROUP BY l.player_id
        HAVING SUM(l.ip) >= {STARTER_IP}
        ORDER BY era ASC
    """).fetchall()
    conn.close()
    total   = len(rows)
    elite_n = int(total * ELITE_PCT)
    fade_n  = int(total * FADE_PCT)
    result  = {}
    for i, r in enumerate(rows):
        rank = i + 1
        if rank <= elite_n:
            tier = "ELITE"
        elif rank > total - fade_n:
            tier = "FADE"
        else:
            tier = "mid"
        result[r["name"]] = (tier, r["era"])
    return result


# ── signal logic ───────────────────────────────────────────────────────────────

def signal(away_team_t, home_team_t, away_p_t, home_p_t) -> list[str]:
    signals = []

    # strong team + strong pitcher vs weak team + weak pitcher
    if home_team_t == "ELITE" and home_p_t == "ELITE" and away_team_t == "FADE" and away_p_t == "FADE":
        signals.append("LOCK HOME -- elite team+pitcher vs fade team+pitcher")
    elif away_team_t == "ELITE" and away_p_t == "ELITE" and home_team_t == "FADE" and home_p_t == "FADE":
        signals.append("LOCK AWAY -- elite team+pitcher vs fade team+pitcher")

    # strong offense vs weak pitcher
    elif home_team_t == "ELITE" and away_p_t == "FADE":
        signals.append("OVER lean -- elite home offense vs fade away starter")
    elif away_team_t == "ELITE" and home_p_t == "FADE":
        signals.append("OVER lean -- elite away offense vs fade home starter")

    # elite pitcher shutting down fade offense
    elif home_p_t == "ELITE" and away_team_t == "FADE":
        signals.append("UNDER lean -- elite home starter vs fade away offense")
    elif away_p_t == "ELITE" and home_team_t == "FADE":
        signals.append("UNDER lean -- elite away starter vs fade home offense")

    # pure team matchup
    elif home_team_t == "ELITE" and away_team_t == "FADE":
        signals.append("HOME lean -- elite team vs fade team")
    elif away_team_t == "ELITE" and home_team_t == "FADE":
        signals.append("AWAY lean -- elite team vs fade team")

    return signals if signals else ["no signal -- middle matchup, skip"]


# ── main ───────────────────────────────────────────────────────────────────────

def run(game_date: str):
    t_tiers = team_tiers()
    p_tiers = pitcher_tiers()

    r = requests.get(f"{BASE}/schedule", params={
        "sportId": 1,
        "date": game_date,
        "hydrate": "probablePitcher",
    })
    r.raise_for_status()

    games = []
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {}).get("detailedState", "")
            away_t = g["teams"]["away"]["team"]["name"]
            home_t = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName")

            away_team_t = t_tiers.get(away_t, "mid")
            home_team_t = t_tiers.get(home_t, "mid")
            away_p_t, away_era = p_tiers.get(away_p, ("?", None)) if away_p else ("?", None)
            home_p_t, home_era = p_tiers.get(home_p, ("?", None)) if home_p else ("?", None)

            sigs = signal(away_team_t, home_team_t, away_p_t, home_p_t)
            games.append({
                "status":       status,
                "away_t":       away_t,
                "home_t":       home_t,
                "away_team_t":  away_team_t,
                "home_team_t":  home_team_t,
                "away_p":       away_p or "TBD",
                "home_p":       home_p or "TBD",
                "away_p_t":     away_p_t,
                "home_p_t":     home_p_t,
                "away_era":     away_era,
                "home_era":     home_era,
                "signals":      sigs,
            })

    # sort: LOCK first, then leaning, then no signal
    def sort_key(g):
        s = g["signals"][0]
        if "LOCK" in s:   return 0
        if "lean" in s:   return 1
        return 2

    games.sort(key=sort_key)

    print(f"\nMATCHUPS -- {game_date}\n")
    for g in games:
        away_label = f"{g['away_t']} [{g['away_team_t']}]"
        home_label = f"{g['home_t']} [{g['home_team_t']}]"
        print(f"  {away_label}  @  {home_label}  ({g['status']})")

        away_era_str = f"  ERA {g['away_era']}" if g["away_era"] is not None else ""
        home_era_str = f"  ERA {g['home_era']}" if g["home_era"] is not None else ""
        print(f"    away starter: {g['away_p']} [{g['away_p_t']}]{away_era_str}")
        print(f"    home starter: {g['home_p']} [{g['home_p_t']}]{home_era_str}")

        for s in g["signals"]:
            arrow = ">>>" if "LOCK" in s else ("-->" if "lean" in s else "   ")
            print(f"    {arrow} {s}")
        print()


if __name__ == "__main__":
    game_date = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
    run(game_date)
