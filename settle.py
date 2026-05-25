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
import sqlite3
from datetime import date as date_cls
from pathlib import Path
from dotenv import load_dotenv

from ob1 import ob1_push
from mlb_api import find_game_pk, get_box_stats, get_game_status, get_linescore
from box_stats import extract_player_stats, get_actual_from_box, auto_observe_picks

load_dotenv(Path(__file__).parent / ".env")

BETLOG_DB = Path(__file__).parent / "sliplog.db"
PICKS_DB = Path(__file__).parent / "mlb.db"
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


def settle_bet(bet_id: int, result: str, actual_payout: float | None = None):
    conn = sqlite3.connect(BETLOG_DB)
    conn.row_factory = sqlite3.Row
    slip = conn.execute("SELECT * FROM slips WHERE id=?", (bet_id,)).fetchone()
    if not slip:
        print(f"Slip {bet_id} not found.")
        conn.close()
        return

    entry = slip["entry"]
    payout = actual_payout if actual_payout is not None else slip["payout"]
    profit = round(payout - entry, 2) if result == "W" else round(-entry, 2)
    status = "win" if result == "W" else "loss"

    conn.execute(
        "UPDATE slips SET status=?, profit=?, payout=? WHERE id=?",
        (status, profit, payout, bet_id)
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
    try:
        from discord_manager import send as _dm_send
        _dm_send("mlb_edge_alert", message)
    except Exception as e:
        print(f"Discord post failed: {e}")


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
    parser.add_argument("--payout", type=float, default=None, help="Actual payout received (overrides stored payout; use for flex/partial wins)")
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
                settle_bet(bet["id"], args.settle, actual_payout=args.payout)
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
