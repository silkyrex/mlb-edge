"""
settle.py -- show live/final stats for open bets, prompt for settlement

Usage:
    python settle.py           # show all open bets with current box score stats
    python settle.py --id 1    # show specific bet
"""

import argparse
import re
import requests
import sqlite3
from datetime import date as date_cls
from pathlib import Path

BETLOG_DB = Path(__file__).parent / "betlog.db"
PICKS_DB = Path.home() / "sports/dfs/picks.db"
BASE = "https://statsapi.mlb.com/api/v1"


def get_open_bets(bet_id: int | None = None) -> list[dict]:
    conn = sqlite3.connect(BETLOG_DB)
    conn.row_factory = sqlite3.Row
    if bet_id:
        rows = conn.execute("SELECT * FROM bets WHERE id=? AND (result='open' OR result IS NULL)", (bet_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM bets WHERE result='open' OR result IS NULL ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_game_pk(matchup: str, game_date: str) -> int | None:
    """Match free-text matchup to a game on the given date."""
    words = [w.lower() for w in re.split(r"[\s@/]+", matchup) if len(w) > 2]
    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "date": game_date}, timeout=10)
    if r.status_code != 200:
        return None
    for d in r.json().get("dates", []):
        for game in d.get("games", []):
            teams = game["teams"]
            names = (
                teams["away"]["team"]["name"].lower() +
                teams["home"]["team"]["name"].lower()
            )
            if sum(1 for w in words if w in names) >= 2:
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
    r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "gamePk": game_pk, "hydrate": "linescore"}, timeout=10)
    if r.status_code != 200:
        return {}
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            if g["gamePk"] == game_pk:
                teams = g["teams"]
                ls = g.get("linescore", {})
                away = teams["away"]["team"]["name"]
                home = teams["home"]["team"]["name"]
                away_r = teams["away"].get("score", "?")
                home_r = teams["home"].get("score", "?")
                return {"score": f"{away} {away_r}  {home} {home_r}", "linescore": ls}
    return {}


def extract_player_stats(bet_on: str, box: dict) -> list[str]:
    """
    Parse the bet_on string for player names and pull their relevant stats.
    Returns list of formatted stat lines.
    """
    lines = []
    # Split multi-pick slips by '+' or newline
    picks = re.split(r"\s*\+\s*|\n", bet_on)

    for pick in picks:
        pick = pick.strip()
        if not pick:
            continue

        # Try to match player name in box stats
        matched_name = None
        matched_stats = None
        for name, stats in box.items():
            # Check if any word of the player name appears in the pick string
            last = name.split()[-1]
            first = name.split()[0]
            if last.lower() in pick.lower() or first.lower() in pick.lower():
                matched_name = name
                matched_stats = stats
                break

        if not matched_name:
            lines.append(f"  {pick}  →  player not found in box score")
            continue

        bat = matched_stats["batting"]
        pit = matched_stats["pitching"]

        # Determine what stat to show based on pick text
        pick_lower = pick.lower()
        stat_lines = []

        if "strikeout" in pick_lower or "ks" in pick_lower or " k " in pick_lower:
            if pit.get("strikeOuts") is not None:
                stat_lines.append(f"K={pit['strikeOuts']}")
            if bat.get("strikeOuts") is not None:
                stat_lines.append(f"Batter K={bat['strikeOuts']}")
        if "earned run" in pick_lower or "era" in pick_lower:
            stat_lines.append(f"ER={pit.get('earnedRuns','?')}  IP={pit.get('inningsPitched','?')}")
        if "hits allowed" in pick_lower:
            stat_lines.append(f"H allowed={pit.get('hits','?')}")
        if "pitching out" in pick_lower:
            outs = int(float(pit.get("inningsPitched", 0) or 0) * 3) if pit.get("inningsPitched") else "?"
            stat_lines.append(f"Pitching Outs={outs}")
        if "h+r+rbi" in pick_lower or "h + r + rbi" in pick_lower:
            h = bat.get("hits", 0)
            r = bat.get("runs", 0)
            rbi = bat.get("rbi", 0)
            stat_lines.append(f"H+R+RBI={h+r+rbi} ({h}H {r}R {rbi}RBI)")
        if "hits" in pick_lower and "allowed" not in pick_lower and "h+r" not in pick_lower:
            stat_lines.append(f"Hits={bat.get('hits','?')}")
        if "total bases" in pick_lower:
            stat_lines.append(f"TB={bat.get('totalBases','?')}")
        if "home run" in pick_lower:
            stat_lines.append(f"HR={bat.get('homeRuns','?')}")

        if not stat_lines:
            # Show summary
            if pit.get("inningsPitched"):
                stat_lines.append(f"IP={pit['inningsPitched']} ER={pit.get('earnedRuns','?')} K={pit.get('strikeOuts','?')}")
            else:
                stat_lines.append(f"AB={bat.get('atBats','?')} H={bat.get('hits','?')} K={bat.get('strikeOuts','?')}")

        lines.append(f"  {pick}")
        lines.append(f"    {matched_name}: {' | '.join(stat_lines)}")

    return lines


def settle_bet(bet_id: int, result: str):
    conn = sqlite3.connect(BETLOG_DB)
    conn.row_factory = sqlite3.Row
    bet = conn.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
    if not bet:
        print(f"Bet {bet_id} not found.")
        conn.close()
        return

    stake = bet["stake"]
    line = bet["line"]
    profit = None

    if result == "W":
        if line > 0:
            profit = round(stake * line / 100, 2)
        else:
            profit = round(stake * 100 / abs(line), 2)
    elif result == "L":
        profit = -stake

    conn.execute(
        "UPDATE bets SET result=?, profit=? WHERE id=?",
        (result, profit, bet_id)
    )
    conn.commit()
    conn.close()

    sign = "+" if profit and profit > 0 else ""
    print(f"Bet #{bet_id} settled: {result}  profit={sign}{profit}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, help="Specific bet ID")
    parser.add_argument("--settle", choices=["W", "L"], help="Settle directly without prompting")
    args = parser.parse_args()

    bets = get_open_bets(args.id)
    if not bets:
        print("No open bets.")
        return

    for bet in bets:
        print(f"\n{'='*60}")
        print(f"Bet #{bet['id']}  |  {bet['date']}  |  {bet['matchup']}")
        print(f"Signal: {bet['signal']}")
        print(f"Bet:    {bet['bet_on']}")
        print(f"Line:   {bet['line']:+d}  |  Stake: ${bet['stake']}")

        # Find game and pull stats
        game_pk = find_game_pk(bet["matchup"], bet["date"])
        if not game_pk:
            print("  Could not locate game in MLB API.")
            continue

        status = get_game_status(game_pk)
        score_data = get_linescore(game_pk)
        print(f"\nGame status: {status}")
        if score_data.get("score"):
            print(f"Score: {score_data['score']}")

        box = get_box_stats(game_pk)
        stat_lines = extract_player_stats(bet["bet_on"], box)
        print("\nPick stats:")
        for l in stat_lines:
            print(l)

        if status == "FINAL":
            if args.settle:
                settle_bet(bet["id"], args.settle)
            else:
                print(f"\nGame is FINAL. Settle with:")
                print(f"  python settle.py --id {bet['id']} --settle W")
                print(f"  python settle.py --id {bet['id']} --settle L")
        else:
            print(f"\nGame in progress ({status}). Re-run when final.")


if __name__ == "__main__":
    main()
