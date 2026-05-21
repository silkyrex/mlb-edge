"""
mlb_api.py -- MLB Stats API helpers

Game lookup, box scores, linescores, game status.
All functions are stateless except for the lazily-loaded team abbreviation cache.
"""

import re
import requests

BASE = "https://statsapi.mlb.com/api/v1"

# Lazily populated from /api/v1/teams on first call. Maps abbreviation -> full name (lowercase).
_TEAM_ABBREV: dict[str, str] = {}


def _load_team_abbrevs() -> None:
    if _TEAM_ABBREV:
        return
    try:
        r = requests.get(f"{BASE}/teams", params={"sportId": 1}, timeout=10)
        if r.status_code == 200:
            for t in r.json().get("teams", []):
                abbrev = t.get("abbreviation", "").upper()
                name = t.get("name", "").lower()
                if abbrev and name:
                    _TEAM_ABBREV[abbrev] = name
    except Exception:
        pass


def find_game_pk(matchup: str, game_date: str) -> int | None:
    """Match free-text matchup to a game on the given date.

    Accepts 'AWAY @ HOME' with abbreviations (LAD, NYY, TB) or full names.
    Abbreviations are resolved via the /api/v1/teams map fetched once per run,
    so they stay correct even when teams relocate (OAK -> ATH, ARI -> AZ, etc.).
    Each side is matched independently to prevent cross-team false matches.
    """
    _load_team_abbrevs()

    halves = re.split(r"\s*[@/]+\s*", matchup, maxsplit=1)
    if len(halves) != 2:
        return None
    away_raw, home_raw = halves[0].strip(), halves[1].strip()

    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "date": game_date}, timeout=10)
    if r.status_code != 200:
        return None

    def matches(raw: str, api_name: str) -> bool:
        expanded = _TEAM_ABBREV.get(raw.upper())
        if expanded:
            return expanded == api_name.lower()
        words = [w for w in raw.lower().split() if len(w) > 2]
        return len(words) >= 1 and sum(1 for w in words if w in api_name.lower()) >= min(2, len(words))

    for d in r.json().get("dates", []):
        for game in d.get("games", []):
            api_away = game["teams"]["away"]["team"]["name"]
            api_home = game["teams"]["home"]["team"]["name"]
            if matches(away_raw, api_away) and matches(home_raw, api_home):
                return game["gamePk"]
            if matches(away_raw, api_home) and matches(home_raw, api_away):
                return game["gamePk"]
    return None


def get_box_stats(game_pk: int) -> dict:
    """Return {player_name: {batting: {...}, pitching: {...}}} for all players."""
    r = requests.get(f"{BASE}/game/{game_pk}/boxscore", timeout=10)
    if r.status_code != 200:
        return {}
    result = {}
    for side in ("home", "away"):
        for p in r.json().get("teams", {}).get(side, {}).get("players", {}).values():
            name = p["person"]["fullName"]
            result[name] = {
                "batting": p.get("stats", {}).get("batting", {}),
                "pitching": p.get("stats", {}).get("pitching", {}),
            }
    return result


def get_game_status(game_pk: int) -> str:
    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "gamePk": game_pk}, timeout=10)
    if r.status_code != 200:
        return "unknown"
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            if g["gamePk"] == game_pk:
                ls = g.get("linescore", {})
                state = g["status"]["detailedState"]
                inning = ls.get("currentInning", "")
                inn_state = ls.get("inningState", "")
                if state in ("Final", "Game Over"):
                    return "FINAL"
                if inning:
                    return f"{inn_state} {inning}"
                return state
    return "unknown"


def get_linescore(game_pk: int) -> dict:
    r = requests.get(
        f"{BASE}/schedule",
        params={"sportId": 1, "gamePk": game_pk, "hydrate": "linescore"},
        timeout=10,
    )
    if r.status_code != 200:
        return {}
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            if g["gamePk"] == game_pk:
                teams = g["teams"]
                away = teams["away"]["team"]["name"]
                home = teams["home"]["team"]["name"]
                away_r = teams["away"].get("score", "?")
                home_r = teams["home"].get("score", "?")
                return {"score": f"{away} {away_r}  {home} {home_r}", "linescore": g.get("linescore", {})}
    return {}
