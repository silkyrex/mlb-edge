#!/usr/bin/env python3
"""
prep.py -- One-command cache prep for today's scraped games.

Finds every game in mlb_game_lines for today, then runs all 4 cache scripts
in parallel across games. Within each game: cache_stats -> cache_espn (sequential,
espn depends on stats rows), cache_news + cache_team (parallel).

Usage:
  python prep.py                    # cache everything for today's scraped games
  python prep.py --check            # show cache status only, no writes
  python prep.py --game "LAD @ LAA" # single game
  python prep.py --date 2026-05-18  # specific date
"""

import argparse
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

MLB_DB = Path(__file__).parent / "mlb.db"
HERE = Path(__file__).parent

SCRIPTS = {
    "stats": HERE / "cache_stats.py",
    "espn":  HERE / "cache_espn.py",
    "news":  HERE / "cache_news.py",
    "team":  HERE / "cache_team.py",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cache all data for today's scraped games")
    p.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
    p.add_argument("--game", default=None, help="Single game substring filter")
    p.add_argument("--check", action="store_true", help="Show cache status only, no writes")
    return p.parse_args()


def get_scraped_games(target_date: str, game_filter: str | None) -> list[str]:
    db = sqlite3.connect(MLB_DB)
    where = "scraped_date = ?"
    params: list = [target_date]
    if game_filter:
        where += " AND LOWER(game) LIKE ?"
        params.append(f"%{game_filter.lower()}%")
    rows = db.execute(
        f"SELECT DISTINCT game FROM mlb_game_lines WHERE {where} ORDER BY game", params
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def cache_status(game: str, target_date: str) -> dict[str, bool]:
    """Check which cache tables have data for this game+date."""
    db = sqlite3.connect(MLB_DB)
    db.row_factory = sqlite3.Row

    # Get players in this game's lines
    players = [r[0] for r in db.execute(
        "SELECT DISTINCT player FROM mlb_game_lines WHERE scraped_date=? AND game=?",
        (target_date, game),
    ).fetchall()]

    has_stats = has_espn = has_news = has_team = False

    if players:
        placeholders = ",".join("?" * len(players))
        has_stats = bool(db.execute(
            f"SELECT 1 FROM player_recent_stats WHERE cache_date=? AND player IN ({placeholders}) LIMIT 1",
            [target_date] + players,
        ).fetchone())
        has_espn = bool(db.execute(
            f"SELECT 1 FROM player_recent_stats WHERE cache_date=? AND player IN ({placeholders}) AND espn_fip IS NOT NULL LIMIT 1",
            [target_date] + players,
        ).fetchone())
        has_news = bool(db.execute(
            f"SELECT 1 FROM player_news WHERE news_date=? AND player IN ({placeholders}) LIMIT 1",
            [target_date] + players,
        ).fetchone())

    has_team = bool(db.execute(
        "SELECT 1 FROM team_game_stats WHERE cache_date=? AND game=? LIMIT 1",
        (target_date, game),
    ).fetchone())

    db.close()
    return {"stats": has_stats, "espn": has_espn, "news": has_news, "team": has_team}


def run_script(script_key: str, game: str, target_date: str) -> tuple[str, bool, str]:
    """Run one cache script. Returns (script_key, success, message)."""
    cmd = [sys.executable, str(SCRIPTS[script_key]), "--game", game, "--date", target_date]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=HERE)
        ok = result.returncode == 0
        msg = (result.stderr or result.stdout or "").strip().splitlines()[-1] if not ok else ""
        return script_key, ok, msg
    except subprocess.TimeoutExpired:
        return script_key, False, "timeout after 120s"
    except Exception as exc:
        return script_key, False, str(exc)


def prep_game(game: str, target_date: str) -> dict[str, tuple[bool, str]]:
    """
    Cache one game. Order: stats -> espn (sequential), news + team (parallel with each other).
    Returns {script_key: (success, error_msg)}.
    """
    results: dict[str, tuple[bool, str]] = {}

    # stats must complete before espn
    for key in ("stats", "espn"):
        k, ok, msg = run_script(key, game, target_date)
        results[k] = (ok, msg)
        if not ok and k == "stats":
            # espn is pointless without stats rows
            results["espn"] = (False, "skipped -- stats failed")
            break

    # news + team are independent -- run in parallel
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_script, key, game, target_date): key for key in ("news", "team")}
        for fut in as_completed(futures):
            k, ok, msg = fut.result()
            results[k] = (ok, msg)

    return results


def status_icon(ok: bool | None) -> str:
    if ok is None:
        return "~"
    return "+" if ok else "!"


def print_status_table(games: list[str], target_date: str, run_results: dict | None = None) -> None:
    """Print a status table. run_results = {game: {key: (ok, msg)}} if available."""
    header = f"{'GAME':<45} {'stats':>5} {'espn':>5} {'news':>5} {'team':>5}"
    print()
    print(header)
    print("-" * len(header))

    failures = []
    for game in games:
        if run_results and game in run_results:
            row = run_results[game]
            icons = {k: status_icon(row[k][0]) for k in ("stats", "espn", "news", "team")}
        else:
            status = cache_status(game, target_date)
            icons = {k: ("+" if v else "-") for k, v in status.items()}

        label = game[:43] + ".." if len(game) > 43 else game
        print(f"{label:<45} {icons['stats']:>5} {icons['espn']:>5} {icons['news']:>5} {icons['team']:>5}")

        if run_results and game in run_results:
            for key, (ok, msg) in run_results[game].items():
                if not ok and msg and msg != "skipped -- stats failed":
                    print(f"  {'':45} {key}: {msg[:60]}")
                    failures.append(f"{game}/{key}")

    print()
    if run_results is not None:
        print("  + = cached   ! = failed   ~ = skipped")
    else:
        print("  + = cached   - = missing")
    print()
    return failures


def main() -> None:
    args = parse_args()
    target_date = args.date or date.today().isoformat()

    games = get_scraped_games(target_date, args.game)
    if not games:
        print(f"[prep] No scraped games found for {target_date}.")
        print("[prep] Run /underdog-mlb first to scrape today's lines.")
        sys.exit(1)

    print(f"[prep] {target_date} -- {len(games)} game(s) found")

    if args.check:
        print_status_table(games, target_date)
        return

    # Show current state before running
    print("[prep] Current cache state:")
    print_status_table(games, target_date)

    print(f"[prep] Caching {len(games)} game(s) -- running in parallel...\n")

    run_results: dict[str, dict] = {}

    # Run all games in parallel
    with ThreadPoolExecutor(max_workers=min(len(games), 4)) as pool:
        futures = {pool.submit(prep_game, game, target_date): game for game in games}
        for fut in as_completed(futures):
            game = futures[fut]
            run_results[game] = fut.result()
            icons = " ".join(
                f"{k}:{'ok' if v[0] else 'FAIL'}"
                for k, v in run_results[game].items()
            )
            print(f"  {game[:50]:50}  {icons}")

    print("\n[prep] Final state:")
    failures = print_status_table(games, target_date, run_results)
    if failures:
        print(f"[prep] {len(failures)} failure(s): {', '.join(failures)}")
        print("[prep] Re-run prep.py to retry failed scripts.")
    else:
        print("[prep] All cached. Run: python closer.py")


if __name__ == "__main__":
    main()
