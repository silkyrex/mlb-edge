"""
settle.py -- show live/final stats for open bets, prompt for settlement

Usage:
    python settle.py                      # show all open bets with box score stats
    python settle.py --id 1               # show specific bet
    python settle.py --id 1 --settle W    # settle bet as win
    python settle.py --post-discord       # post settlement status to Discord (used by daily.sh)
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



def build_ob1_content(bet: dict, result: str, profit: float | None) -> str:
    sign = "+" if profit and profit > 0 else ""
    profit_str = f"{sign}{profit:.2f}" if profit is not None else "n/a"
    result_label = "WIN" if result == "W" else "LOSS"

    # Pull FIP/WAR context for pitchers named in the bet
    fip_context = []
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
            if r["player"].split()[-1].lower() in bet["bet_on"].lower():
                era = r["season_era"]
                fip = r["espn_fip"]
                war = r["espn_war"]
                if era and fip and abs(fip - era) > 0.5:
                    flag = "ERA LUCKY" if fip > era else "ERA UNLUCKY"
                    fip_context.append(f"{r['player']}: ERA {era:.2f} FIP {fip:.2f} WAR {war} [{flag}]")
    except Exception:
        pass

    lines = [
        f"MLB bet settled [{result_label}]: {bet['matchup']} ({bet['date']})",
        f"Lean: {bet.get('signal', 'n/a')}",
        f"Pick: {bet['bet_on']}",
        f"Result: {result_label} | Profit: {profit_str} | Stake: ${bet['stake']:.0f} | Line: {bet['line']:+d}",
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
    """Parse bet_on string and call pick_lessons.observe for each pick with box data."""
    raw_picks = re.split(r"\s*\+\s*|\n", bet["bet_on"])
    game = bet["matchup"].split("/")[0].strip()

    for raw in raw_picks:
        raw = raw.strip()
        m = _PICK_RE.search(raw)
        if not m:
            continue
        name_hint = m.group("name").split()[-1].lower()
        stat_abbrev = m.group("stat").lower()
        line = float(m.group("line"))
        side = m.group("side").capitalize()

        stat_key, player_type = _STAT_ABBREVS.get(stat_abbrev, (None, "pitcher"))
        if not stat_key:
            continue

        matched_name = next(
            (n for n in box if name_hint in n.lower()), None
        )
        if not matched_name:
            print(f"  [pick_lessons] no box match for {name_hint} -- skipped")
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
    """Append a settled bet row to the mlb-rubric.md Data Log."""
    try:
        fip_flags = []
        conn = sqlite3.connect(PICKS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT player, season_era, espn_fip
            FROM player_recent_stats
            WHERE cache_date=? AND player_type='pitcher' AND espn_fip IS NOT NULL
        """, (bet["date"],)).fetchall()
        conn.close()
        for r in rows:
            if r["player"].split()[-1].lower() in bet["bet_on"].lower():
                era, fip = r["season_era"], r["espn_fip"]
                if era and fip and abs(fip - era) > 0.5:
                    fip_flags.append(f"{'ERA LUCKY' if fip > era else 'ERA UNLUCKY'} {r['player']}")

        sign = "+" if profit and profit > 0 else ""
        profit_str = f"{sign}{profit:.2f}" if profit is not None else "n/a"
        fip_str = " / ".join(fip_flags) if fip_flags else "none"
        picks = bet["bet_on"].replace("\n", " + ")
        row = (
            f"| {bet['date']} | {bet['matchup']} | {bet.get('signal','?')} "
            f"| {picks} | -- | -- | -- | {result} | {profit_str} | {fip_str} | auto-logged |"
        )

        text = RUBRIC_LOG.read_text()
        marker = "| Date | Game | Lean |"
        header_end = text.find(marker)
        if header_end == -1:
            return
        # Find end of header row + separator row, insert after
        insert_after = text.find("\n", text.find("\n", header_end) + 1) + 1
        new_text = text[:insert_after] + row + "\n" + text[insert_after:]
        RUBRIC_LOG.write_text(new_text)
        print(f"Rubric log updated.")
    except Exception as e:
        print(f"Rubric log append failed: {e}")


def capture_loss_lesson(bet: dict, profit: float | None) -> None:
    """Prompt for a lesson-learned on a losing bet and capture to OB1 + second brain."""
    print("\n--- LOSS REVIEW ---")
    print(f"Signal: {bet.get('signal', 'n/a')}  |  Pick: {bet['bet_on'].replace(chr(10), ' + ')}")

    # Pull FIP flags for context
    fip_lines = []
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
            if r["player"].split()[-1].lower() in bet["bet_on"].lower():
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
        f"[{bet['date']}] bet loss lesson: {bet['matchup']} | "
        f"signal={bet.get('signal', '?')} | pick={bet['bet_on'].replace(chr(10), ' + ')} | "
        f"profit={profit_str} | lesson={lesson or 'none'}"
    )
    ok = ob1_push(content, {
        "type": "bet_loss_lesson",
        "matchup": bet["matchup"],
        "signal": bet.get("signal", ""),
        "pick": bet["bet_on"],
        "profit": profit,
        "fip_flags": len(fip_lines),
        "lesson": lesson,
        "outcome": "captured" if lesson else "skipped",
        "moved": False,
    })
    print(f"Loss lesson OB1: {'captured' if ok else 'failed'}")

    if lesson:
        sb_path = Path.home() / "second-brain/sports/insights.md"
        if sb_path.exists():
            entry = (
                f"## {bet['date']} -- Loss: {bet['matchup']}\n"
                f"**Signal:** {bet.get('signal', '?')}  |  **Pick:** {bet['bet_on'].replace(chr(10), ' + ')}\n"
                f"**Lesson:** {lesson}\n\n---\n\n"
            )
            sb_path.write_text(entry + sb_path.read_text())
            print("Lesson written to sports/insights.md")


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

    bet_row = dict(conn.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone())
    conn.close()

    sign = "+" if profit and profit > 0 else ""
    print(f"Bet #{bet_id} settled: {result}  profit={sign}{profit}")

    content = build_ob1_content(bet_row, result, profit)
    metadata = {
        "type": "mlb_bet_settled",
        "outcome": "WIN" if result == "W" else "LOSS",
        "profit": profit,
        "stake": bet_row["stake"],
        "line": bet_row["line"],
        "matchup": bet_row["matchup"],
        "signal": bet_row.get("signal", ""),
        "moved": profit is not None and profit > 0,
    }
    ok = ob1_push(content, metadata)
    print(f"OB1 capture: {'ok' if ok else 'failed'}")
    append_rubric_log(bet_row, result, profit)

    if result == "L":
        capture_loss_lesson(bet_row, profit)


def post_discord(message: str):
    if not WEBHOOK_URL:
        return
    requests.post(WEBHOOK_URL, json={"content": message},
                  headers={"User-Agent": "mlb-edge/1.0"}, timeout=10)


def build_bet_summary(bet: dict) -> tuple[str, str]:
    """Returns (terminal_text, discord_text) for a single bet."""
    game_pk = find_game_pk(bet["matchup"], bet["date"])
    if not game_pk:
        return f"Bet #{bet['id']}: could not locate game", ""

    status = get_game_status(game_pk)
    score_data = get_linescore(game_pk)
    box = get_box_stats(game_pk)
    stat_lines = extract_player_stats(bet["bet_on"], box)

    score_str = score_data.get("score", "")
    stats_str = "\n".join(stat_lines)

    terminal = (
        f"\nBet #{bet['id']} | {bet['matchup']} | {status}\n"
        f"  {score_str}\n"
        f"  Bet: {bet['bet_on']}\n"
        f"{stats_str}"
    )

    if status == "FINAL":
        settle_hint = f"  → `python settle.py --id {bet['id']} --settle W/L`"
        discord = (
            f"**Bet #{bet['id']} -- FINAL** | {bet['matchup']}\n"
            f"Score: {score_str}\n"
            f"Bet: {bet['bet_on']} (line {bet['line']:+d}, stake ${bet['stake']:.0f})\n"
            f"{chr(10).join(stat_lines)}\n"
            f"{settle_hint}"
        )
    else:
        discord = ""

    return terminal, discord


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
        print(f"\n{'='*60}")
        print(f"Bet #{bet['id']}  |  {bet['date']}  |  {bet['matchup']}")
        print(f"Signal: {bet['signal']}")
        print(f"Bet:    {bet['bet_on']}")
        print(f"Line:   {bet['line']:+d}  |  Stake: ${bet['stake']}")

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
        for line in stat_lines:
            print(line)

        if status == "FINAL":
            if args.settle:
                settle_bet(bet["id"], args.settle)
                print("\nAuto-trigger: pick_lessons.observe per pick:")
                auto_observe_picks(bet, box, args.settle)
            else:
                print(f"\nGame is FINAL. Settle with:")
                print(f"  python settle.py --id {bet['id']} --settle W")
                print(f"  python settle.py --id {bet['id']} --settle L")

            if args.post_discord:
                msg = (
                    f"**Bet #{bet['id']} -- FINAL** | {bet['matchup']}\n"
                    f"Score: {score_data.get('score', '?')}\n"
                    f"Bet: {bet['bet_on']} (line {bet['line']:+d}, stake ${bet['stake']:.0f})\n"
                    + "\n".join(stat_lines) + "\n"
                    f"→ Settle: `python settle.py --id {bet['id']} --settle W/L`"
                )
                discord_posts.append(msg)
        else:
            print(f"\nGame in progress ({status}). Re-run when final.")

    for msg in discord_posts:
        post_discord(msg)


if __name__ == "__main__":
    main()
