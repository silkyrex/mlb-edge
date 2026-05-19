"""
settle.py -- show live/final stats for open slips, prompt for settlement

Usage:
    python settle.py                      # show all open slips with box score stats
    python settle.py --id 1               # show specific slip
    python settle.py --id 1 --settle W    # settle slip as win
    python settle.py --post-discord       # post final-game summaries to Discord (used by daily.sh)
"""

import argparse
import json
import os
import re
import requests
import sqlite3
from datetime import date as date_cls
from pathlib import Path
from dotenv import load_dotenv

from pick_lessons import observe as _pl_observe
from ob1 import ob1_push

load_dotenv(Path(__file__).parent / ".env")

BETLOG_DB = Path(__file__).parent / "sliplog.db"
PICKS_DB = Path(__file__).parent / "mlb.db"
BASE = "https://statsapi.mlb.com/api/v1"
WEBHOOK_URL = os.getenv("SPORTS_WEBHOOK_URL", "")


def get_open_bets(bet_id: int | None = None) -> list[dict]:
    conn = sqlite3.connect(BETLOG_DB)
    conn.row_factory = sqlite3.Row
    if bet_id:
        slips = conn.execute(
            "SELECT * FROM slips WHERE id=? AND status='open'", (bet_id,)
        ).fetchall()
    else:
        slips = conn.execute(
            "SELECT * FROM slips WHERE status='open' ORDER BY id"
        ).fetchall()

    result = []
    for s in slips:
        slip = dict(s)
        picks = conn.execute(
            "SELECT * FROM slip_picks WHERE slip_id=? ORDER BY id", (slip["id"],)
        ).fetchall()
        slip["picks"] = [dict(p) for p in picks]
        result.append(slip)
    conn.close()
    return result


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



def _slip_players_str(bet: dict) -> str:
    """Return a display string of players from picks or players JSON."""
    if bet["picks"]:
        return " + ".join(p["player"] for p in bet["picks"])
    try:
        return " + ".join(json.loads(bet["players"]))
    except Exception:
        return bet.get("players", "?")


def _slip_games_str(bet: dict) -> str:
    """Return unique games from picks, or 'unknown'."""
    games = list(dict.fromkeys(p["game"] for p in bet["picks"] if p.get("game")))
    return " / ".join(games) if games else "unknown"


def build_ob1_content(bet: dict, result: str, profit: float | None) -> str:
    sign = "+" if profit and profit > 0 else ""
    profit_str = f"{sign}{profit:.2f}" if profit is not None else "n/a"
    result_label = "WIN" if result == "W" else "LOSS"
    players_str = _slip_players_str(bet)
    games_str = _slip_games_str(bet)

    # Pull FIP/WAR context for pitchers named in this slip
    fip_context = []
    player_names_lower = [p["player"].lower() for p in bet["picks"]] if bet["picks"] else []
    try:
        conn = sqlite3.connect(PICKS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT player, season_era, espn_fip, espn_war
            FROM player_recent_stats
            WHERE cache_date=? AND player_type='pitcher'
            AND espn_fip IS NOT NULL
        """, (bet["date"],)).fetchall()
        conn.close()
        for r in rows:
            last = r["player"].split()[-1].lower()
            if any(last in n for n in player_names_lower) or last in players_str.lower():
                era = r["season_era"]
                fip = r["espn_fip"]
                war = r["espn_war"]
                if era and fip and abs(fip - era) > 0.5:
                    flag = "ERA LUCKY" if fip > era else "ERA UNLUCKY"
                    fip_context.append(f"{r['player']}: ERA {era:.2f} FIP {fip:.2f} WAR {war} [{flag}]")
    except Exception:
        pass

    lines = [
        f"MLB slip settled [{result_label}]: {games_str} ({bet['date']})",
        f"Picks: {players_str}",
        f"Result: {result_label} | Profit: {profit_str} | Entry: ${bet['entry']:.0f} | Payout: ${bet['payout']:.2f}",
        f"Agent: mlb-edge",
    ]
    if fip_context:
        lines.append("FIP context: " + " | ".join(fip_context))

    return "\n".join(lines)


_STAT_ABBREVS = {
    "ks": ("Strikeouts", "pitcher"),
    "po": ("Pitching Outs", "pitcher"),
    "er": ("Earned Runs", "pitcher"),
    "h+r+rbi": ("Hits + Runs + RBIs", "batter"),
    "tb": ("Total Bases", "batter"),
    "hr": ("Home Runs", "batter"),
}

_PICK_RE = re.compile(
    r"(?P<name>[A-Z][a-zA-Z\-']+(?:\s+[A-Z][a-zA-Z\-']+)*)\s+"
    r"(?P<stat>[A-Za-z+]+(?:\+[A-Za-z]+)*)\s+"
    r"(?P<line>\d+(?:\.\d+)?)\s+"
    r"(?P<side>Higher|Lower)",
    re.IGNORECASE,
)


def _get_actual_from_box(matched_name: str, stat_key: str, box: dict) -> float | None:
    """Pull the numeric actual for a stat from the box score entry."""
    stats = box.get(matched_name, {})
    pit = stats.get("pitching", {})
    bat = stats.get("batting", {})
    if stat_key == "Strikeouts":
        v = pit.get("strikeOuts")
        return float(v) if v is not None else None
    if stat_key == "Pitching Outs":
        ip = pit.get("inningsPitched")
        return round(float(ip) * 3, 0) if ip else None
    if stat_key == "Earned Runs":
        v = pit.get("earnedRuns")
        return float(v) if v is not None else None
    if stat_key == "Hits + Runs + RBIs":
        h = bat.get("hits", 0) or 0
        r = bat.get("runs", 0) or 0
        rbi = bat.get("rbi", 0) or 0
        return float(h + r + rbi)
    if stat_key == "Total Bases":
        v = bat.get("totalBases")
        return float(v) if v is not None else None
    if stat_key == "Home Runs":
        v = bat.get("homeRuns")
        return float(v) if v is not None else None
    return None


def auto_observe_picks(bet: dict, box: dict, result: str) -> None:
    """Call pick_lessons.observe for each structured pick using box score data."""
    if not bet["picks"]:
        print("  [pick_lessons] no per-pick structure -- skipped")
        return

    for pick in bet["picks"]:
        player = pick["player"]
        stat_key = pick["stat"]
        line = float(pick["line"])
        side = pick["side"]
        player_type = pick.get("player_type", "pitcher")
        game = pick.get("game", "")

        name_hint = player.split()[-1].lower()
        matched_name = next((n for n in box if name_hint in n.lower()), None)
        if not matched_name:
            print(f"  [pick_lessons] no box match for {player} -- skipped")
            continue

        actual = _get_actual_from_box(matched_name, stat_key, box)
        if actual is None:
            print(f"  [pick_lessons] no actual for {matched_name} {stat_key} -- skipped")
            continue

        hit = (actual > line) if side == "Higher" else (actual < line)
        _pl_observe(
            player=matched_name,
            player_type=player_type,
            stat=stat_key,
            line=line,
            side=side,
            actual=actual,
            hit=hit,
            game=game,
            date_str=bet["date"],
        )
        print(f"  [pick_lessons] {matched_name} {stat_key} {line} {side} -> actual={actual}  {'HIT' if hit else 'MISS'}")


RUBRIC_LOG = Path.home() / ".claude/skills/bet-score/mlb-rubric.md"


def append_rubric_log(bet: dict, result: str, profit: float | None):
    """Append a settled slip row to the mlb-rubric.md Data Log."""
    try:
        players_str = _slip_players_str(bet)
        games_str = _slip_games_str(bet)

        fip_flags = []
        player_names_lower = [p["player"].lower() for p in bet["picks"]] if bet["picks"] else []
        conn = sqlite3.connect(PICKS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT player, season_era, espn_fip
            FROM player_recent_stats
            WHERE cache_date=? AND player_type='pitcher' AND espn_fip IS NOT NULL
        """, (bet["date"],)).fetchall()
        conn.close()
        for r in rows:
            last = r["player"].split()[-1].lower()
            if any(last in n for n in player_names_lower) or last in players_str.lower():
                era, fip = r["season_era"], r["espn_fip"]
                if era and fip and abs(fip - era) > 0.5:
                    fip_flags.append(f"{'ERA LUCKY' if fip > era else 'ERA UNLUCKY'} {r['player']}")

        sign = "+" if profit and profit > 0 else ""
        profit_str = f"{sign}{profit:.2f}" if profit is not None else "n/a"
        fip_str = " / ".join(fip_flags) if fip_flags else "none"
        row = (
            f"| {bet['date']} | {games_str} | -- "
            f"| {players_str} | -- | -- | -- | {result} | {profit_str} | {fip_str} | auto-logged |"
        )

        text = RUBRIC_LOG.read_text()
        marker = "| Date | Game | Lean |"
        header_end = text.find(marker)
        if header_end == -1:
            return
        insert_after = text.find("\n", text.find("\n", header_end) + 1) + 1
        new_text = text[:insert_after] + row + "\n" + text[insert_after:]
        RUBRIC_LOG.write_text(new_text)
        print("Rubric log updated.")
    except Exception as e:
        print(f"Rubric log append failed: {e}")


def capture_bet_lesson(bet: dict, result: str, profit: float | None) -> None:
    """Prompt for a lesson on any settled slip and capture to OB1 + second brain."""
    label = "WIN REVIEW" if result == "W" else "LOSS REVIEW"
    players_str = _slip_players_str(bet)
    games_str = _slip_games_str(bet)
    print(f"\n--- {label} ---")
    print(f"Picks: {players_str}  |  Games: {games_str}")

    fip_lines = []
    player_names_lower = [p["player"].lower() for p in bet["picks"]] if bet["picks"] else []
    try:
        conn = sqlite3.connect(PICKS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT player, season_era, espn_fip
            FROM player_recent_stats
            WHERE cache_date=? AND player_type='pitcher' AND espn_fip IS NOT NULL
        """, (bet["date"],)).fetchall()
        conn.close()
        for r in rows:
            last = r["player"].split()[-1].lower()
            if any(last in n for n in player_names_lower) or last in players_str.lower():
                era, fip = r["season_era"], r["espn_fip"]
                if era and fip and abs(fip - era) > 0.5:
                    flag = "ERA LUCKY (regression risk)" if fip > era else "ERA UNLUCKY"
                    fip_lines.append(f"  {r['player']}: ERA {era:.2f} FIP {fip:.2f} [{flag}]")
    except Exception:
        pass

    if fip_lines:
        print("FIP at time of pick:")
        for line in fip_lines:
            print(line)

    try:
        lesson = input("Lesson (enter to skip): ").strip()
    except EOFError:
        lesson = ""

    profit_str = f"{profit:.2f}" if profit is not None else "n/a"
    content = (
        f"[{bet['date']}] slip {result} lesson: {games_str} | "
        f"picks={players_str} | "
        f"profit={profit_str} | lesson={lesson or 'none'}"
    )
    ok = ob1_push(content, {
        "type": "bet_lesson",
        "result": result,
        "matchup": games_str,
        "signal": "",
        "pick": players_str,
        "profit": profit,
        "fip_flags": len(fip_lines),
        "lesson": lesson,
        "outcome": "captured" if lesson else "skipped",
        "moved": False,
    })
    print(f"Lesson OB1: {'captured' if ok else 'failed'}")

    if lesson:
        sb_path = Path.home() / "second-brain/sports/insights.md"
        if sb_path.exists():
            entry = (
                f"## {bet['date']} -- {'Win' if result == 'W' else 'Loss'}: {games_str}\n"
                f"**Picks:** {players_str}\n"
                f"**Lesson:** {lesson}\n\n---\n\n"
            )
            sb_path.write_text(entry + sb_path.read_text())
            print("Lesson written to sports/insights.md")


def settle_bet(bet_id: int, result: str):
    conn = sqlite3.connect(BETLOG_DB)
    conn.row_factory = sqlite3.Row
    slip = conn.execute("SELECT * FROM slips WHERE id=?", (bet_id,)).fetchone()
    if not slip:
        print(f"Slip {bet_id} not found.")
        conn.close()
        return

    entry = slip["entry"]
    payout = slip["payout"]
    profit = round(payout - entry, 2) if result == "W" else round(-entry, 2)
    status = "win" if result == "W" else "loss"

    conn.execute(
        "UPDATE slips SET status=?, profit=? WHERE id=?",
        (status, profit, bet_id)
    )
    conn.commit()

    picks = conn.execute(
        "SELECT * FROM slip_picks WHERE slip_id=? ORDER BY id", (bet_id,)
    ).fetchall()
    bet_row = dict(slip)
    bet_row["picks"] = [dict(p) for p in picks]
    conn.close()

    sign = "+" if profit > 0 else ""
    print(f"Slip #{bet_id} settled: {result}  profit={sign}{profit:.2f}")

    content = build_ob1_content(bet_row, result, profit)
    metadata = {
        "type": "mlb_bet_settled",
        "outcome": "WIN" if result == "W" else "LOSS",
        "profit": profit,
        "entry": bet_row["entry"],
        "payout": bet_row["payout"],
        "matchup": _slip_games_str(bet_row),
        "signal": "",
        "moved": profit > 0,
    }
    ok = ob1_push(content, metadata)
    print(f"OB1 capture: {'ok' if ok else 'failed'}")
    append_rubric_log(bet_row, result, profit)

    capture_bet_lesson(bet_row, result, profit)


def post_discord(message: str):
    if not WEBHOOK_URL:
        return
    requests.post(WEBHOOK_URL, json={"content": message},
                  headers={"User-Agent": "mlb-edge/1.0"}, timeout=10)


def build_bet_summary(bet: dict) -> tuple[str, str]:
    """Returns (terminal_text, discord_text) for a single slip."""
    players_str = _slip_players_str(bet)
    games_str = _slip_games_str(bet)
    unique_games = list(dict.fromkeys(p["game"] for p in bet["picks"] if p.get("game")))

    terminal_lines = [f"\nSlip #{bet['id']} | {bet['date']} | {players_str}"]
    discord_lines = []
    all_final = bool(unique_games)

    for game_str in unique_games:
        game_pk = find_game_pk(game_str, bet["date"])
        if not game_pk:
            terminal_lines.append(f"  {game_str}: game not found")
            all_final = False
            continue
        status = get_game_status(game_pk)
        score_data = get_linescore(game_pk)
        score_str = score_data.get("score", "")
        terminal_lines.append(f"  {game_str} [{status}] {score_str}")
        discord_lines.append(f"{game_str}: {score_str} [{status}]")
        if status != "FINAL":
            all_final = False

    discord = ""
    if all_final:
        settle_hint = f"→ Settle: `python settle.py --id {bet['id']} --settle W/L`"
        discord = (
            f"**Slip #{bet['id']} -- FINAL** | {players_str}\n"
            + "\n".join(discord_lines) + "\n"
            f"Entry ${bet['entry']:.0f} | Payout ${bet['payout']:.2f}\n"
            f"{settle_hint}"
        )

    return "\n".join(terminal_lines), discord


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, help="Specific bet ID")
    parser.add_argument("--settle", choices=["W", "L"], help="Settle directly without prompting")
    parser.add_argument("--post-discord", action="store_true", help="Post final-game summaries to Discord")
    args = parser.parse_args()

    bets = get_open_bets(args.id)
    if not bets:
        print("No open bets.")
        return

    discord_posts = []

    for bet in bets:
        players_str = _slip_players_str(bet)
        games_str = _slip_games_str(bet)
        print(f"\n{'='*60}")
        print(f"Slip #{bet['id']}  |  {bet['date']}  |  {bet['platform']}")
        print(f"Picks:  {players_str}")
        print(f"Entry:  ${bet['entry']:.0f}  |  Payout: ${bet['payout']:.2f}")
        if bet["boost"]:
            print(f"Boost:  {bet['boost']}")

        # Per-pick box score lookup -- use unique games from slip_picks
        unique_games = list(dict.fromkeys(p["game"] for p in bet["picks"] if p.get("game")))
        if not unique_games and not bet["picks"]:
            # Legacy slip: no per-pick data, best-effort game lookup skipped
            print("  (legacy slip -- no per-pick structure, box score unavailable)")
            unique_games = []

        all_final = True
        all_boxes: dict[str, dict] = {}
        discord_score_lines = []

        for game_str in unique_games:
            game_pk = find_game_pk(game_str, bet["date"])
            if not game_pk:
                print(f"  Could not locate game '{game_str}' in MLB API.")
                all_final = False
                continue

            status = get_game_status(game_pk)
            score_data = get_linescore(game_pk)
            box = get_box_stats(game_pk)
            all_boxes.update(box)

            print(f"\n{game_str}  [{status}]")
            if score_data.get("score"):
                print(f"  Score: {score_data['score']}")
                discord_score_lines.append(f"{game_str}: {score_data['score']} [{status}]")

            # Show stats for picks in this game
            game_picks = [p for p in bet["picks"] if p.get("game") == game_str]
            for pick in game_picks:
                name_hint = pick["player"].split()[-1].lower()
                matched = next((n for n in box if name_hint in n.lower()), None)
                if matched:
                    stat_lines = extract_player_stats(
                        f"{pick['player']} {pick['stat']} {pick['line']} {pick['side']}", box
                    )
                    for s in stat_lines:
                        print(s)
                else:
                    print(f"  {pick['player']}: not found in box score")

            if status != "FINAL":
                all_final = False

        if not unique_games:
            all_final = False

        if all_final or (not unique_games and args.settle):
            if args.settle:
                settle_bet(bet["id"], args.settle)
                if all_boxes:
                    print("\nAuto-trigger: pick_lessons.observe per pick:")
                    auto_observe_picks(bet, all_boxes, args.settle)
            else:
                print(f"\nAll games FINAL. Settle with:")
                print(f"  python settle.py --id {bet['id']} --settle W")
                print(f"  python settle.py --id {bet['id']} --settle L")

            if args.post_discord:
                msg = (
                    f"**Slip #{bet['id']} -- FINAL** | {players_str}\n"
                    + "\n".join(discord_score_lines) + "\n"
                    f"Entry ${bet['entry']:.0f} | Payout ${bet['payout']:.2f}\n"
                    f"→ Settle: `python settle.py --id {bet['id']} --settle W/L`"
                )
                discord_posts.append(msg)
        elif unique_games:
            print(f"\nGames still in progress. Re-run when final.")

    for msg in discord_posts:
        post_discord(msg)


if __name__ == "__main__":
    main()
