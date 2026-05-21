#!/usr/bin/env python3
"""
watch.py -- Live game scout: polls every 5 min, Claude Haiku on key events.

Watches one MLB game from first pitch to final out. Meaningful state changes
(runs scored, pitching changes, late innings, extras, final) trigger a Claude
Haiku analysis note. Every tick writes to mlb.db:game_scout_log and appends
to ~/mlb-edge/scout_logs/YYYY-MM-DD_{away}_{home}.md.

Usage:
    python watch.py "NYM @ ATL"
    python watch.py "NYM @ ATL" --date 2026-05-20
    python watch.py --game-pk 745582 --date 2026-05-20
    python watch.py "LAD @ SF" --no-discord
    python watch.py "LAD @ SF" --interval 60   # faster polling for testing
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date as date_cls, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE = "https://statsapi.mlb.com/api/v1"
MLB_DB = Path(__file__).parent / "mlb.db"
SCOUT_LOGS = Path(__file__).parent / "scout_logs"
POLL_INTERVAL = 300
MODEL_HAIKU = "claude-haiku-4-5-20251001"

HAIKU_PROMPT = """You are a live MLB game analyst writing a brief scout note for a sports bettor.
Keep it tight: 2-4 sentences. No filler. Reference numbers from the pre-game context when relevant.

PRE-GAME CONTEXT:
{pregame_ctx}

CURRENT GAME STATE:
  Game: {game_str}
  Inning: {half} {inning} | Score: {away_score} - {home_score} (Away - Home)
  Current pitchers -> Away: {pitcher_away} | Home: {pitcher_home}
  Outs: {outs}

TRIGGER: {events_str}

INSTRUCTIONS BY TRIGGER TYPE:
- run_scored: Name the pitcher who gave it up. Note if this changes momentum on a K prop.
- pitching_change: Who came in, what role (starter exit or bullpen). Flag if K prop is now less live.
- late_inning (7-9): Flag the game situation (close, blowout, tying run on deck). Pitch count concern?
- extras: Game situation. Starter stats are locked; only closer/bullpen K props live.
- final: One sentence final summary. Include final K totals if pitchers had props going.

Write only the scout note. No headers, no labels."""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live MLB game scout -- polls every N seconds")
    p.add_argument("game", nargs="?", help='Game string e.g. "NYM @ ATL"')
    p.add_argument("--game-pk", type=int, default=None, help="Direct MLB game_pk (skips schedule lookup)")
    p.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
    p.add_argument("--no-discord", action="store_true", help="Suppress Discord webhook pushes")
    p.add_argument("--interval", type=int, default=POLL_INTERVAL, help="Poll interval in seconds (default 300)")
    return p.parse_args()


def resolve_game_pk(game_str: str, game_date: str) -> int | None:
    """Match free-text matchup to a game_pk on the given date. Adapted from settle.py."""
    words = [w.lower() for w in re.split(r"[\s@/]+", game_str) if len(w) > 2]
    try:
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
    except Exception as e:
        print(f"[watch] schedule lookup error: {e}", file=sys.stderr)
    return None


def resolve_game_str_from_pk(game_pk: int, game_date: str) -> str:
    """Reverse lookup: game_pk -> 'Away @ Home' display string."""
    try:
        r = requests.get(f"{BASE}/schedule", params={"sportId": 1, "gamePk": game_pk}, timeout=10)
        if r.status_code == 200:
            for d in r.json().get("dates", []):
                for g in d.get("games", []):
                    if g["gamePk"] == game_pk:
                        t = g["teams"]
                        return f"{t['away']['team']['name']} @ {t['home']['team']['name']}"
    except Exception:
        pass
    return f"game_pk={game_pk}"


def _get_game_abstract_state(game_pk: int) -> tuple[str, str]:
    """Return (abstractGameState, detailedState) from schedule endpoint."""
    try:
        r = requests.get(
            f"{BASE}/schedule",
            params={"sportId": 1, "gamePk": game_pk},
            timeout=10,
        )
        if r.status_code == 200:
            for d in r.json().get("dates", []):
                for g in d.get("games", []):
                    if g["gamePk"] == game_pk:
                        status = g.get("status", {})
                        return (
                            status.get("abstractGameState", "Preview"),
                            status.get("detailedState", ""),
                        )
    except Exception:
        pass
    return ("Preview", "")


def _get_current_pitchers(game_pk: int) -> tuple[str, str]:
    """Return (pitcher_away, pitcher_home) from boxscore. Empty string if unavailable."""
    pitcher_away = ""
    pitcher_home = ""
    try:
        r = requests.get(f"{BASE}/game/{game_pk}/boxscore", timeout=10)
        if r.status_code != 200:
            return pitcher_away, pitcher_home
        box = r.json()
        for side in ("away", "home"):
            team_data = box.get("teams", {}).get(side, {})
            pitchers = team_data.get("pitchers", [])
            players = team_data.get("players", {})
            if pitchers:
                pid_key = f"ID{pitchers[-1]}"
                if pid_key in players:
                    name = players[pid_key]["person"]["fullName"]
                    if side == "away":
                        pitcher_away = name
                    else:
                        pitcher_home = name
    except Exception:
        pass
    return pitcher_away, pitcher_home


def fetch_live_state(game_pk: int) -> dict | None:
    """
    Poll MLB Stats API linescore + schedule status.
    Returns normalized state dict or None on failure.
    """
    try:
        r = requests.get(f"{BASE}/game/{game_pk}/linescore", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()

        abstract_state, detailed_state = _get_game_abstract_state(game_pk)

        inning = data.get("currentInning", 0)
        half = "Top" if data.get("isTopInning", True) else "Bot"
        outs = data.get("outs", 0)

        teams = data.get("teams", {})
        away_score = teams.get("away", {}).get("runs", 0) or 0
        home_score = teams.get("home", {}).get("runs", 0) or 0

        pitcher_away, pitcher_home = _get_current_pitchers(game_pk)

        return {
            "abstract_state": abstract_state,
            "detailed_state": detailed_state,
            "inning": inning,
            "half": half,
            "away_score": away_score,
            "home_score": home_score,
            "outs": outs,
            "pitcher_away": pitcher_away,
            "pitcher_home": pitcher_home,
            "raw": data,
        }
    except Exception as e:
        print(f"[watch] fetch error: {e}", file=sys.stderr)
        return None


def detect_events(current: dict, prev: dict | None, seen_innings: set) -> list[str]:
    """Pure function. Returns list of meaningful event type strings."""
    if prev is None:
        return []

    # Final check first -- short-circuit all other checks
    if (current["abstract_state"] == "Final" or
            current["detailed_state"] in ("Final", "Game Over")):
        return ["final"]

    events = []

    # Run(s) scored
    if (current["away_score"] != prev["away_score"] or
            current["home_score"] != prev["home_score"]):
        events.append("run_scored")

    # Pitching change (either team, only when pitcher names are non-empty)
    for side in ("away", "home"):
        key = f"pitcher_{side}"
        if current[key] and prev[key] and current[key] != prev[key]:
            if "pitching_change" not in events:
                events.append("pitching_change")

    # Late inning (7, 8, 9) -- first entry per inning
    if 7 <= current["inning"] <= 9 and current["inning"] not in seen_innings:
        events.append("late_inning")

    # Extras (10+) -- first entry per inning
    if current["inning"] > 9 and current["inning"] not in seen_innings:
        events.append("extras")

    return events


def prioritize_event(events: list[str]) -> str:
    """Return highest-priority event type, or 'tick' if none."""
    for p in ("final", "run_scored", "pitching_change", "extras", "late_inning"):
        if p in events:
            return p
    return "tick"


def load_pregame_context(game_pk: int, game_date: str, game_str: str) -> str:
    """Query mlb.db for prescan + pitcher stats. Graceful fallback on any error."""
    try:
        conn = sqlite3.connect(MLB_DB)
        conn.row_factory = sqlite3.Row

        # Fuzzy game string match for pre_scan_scores
        words = [w.lower() for w in re.split(r"[\s@/]+", game_str) if len(w) > 2]
        game_pattern = "%" + "%".join(words[:2]) + "%"
        prescan = conn.execute(
            "SELECT score, pitcher_edge, k_gap, venue_score, components_json "
            "FROM pre_scan_scores WHERE date=? AND LOWER(game) LIKE ? LIMIT 1",
            (game_date, game_pattern),
        ).fetchone()

        pitcher_lines = []
        if prescan and prescan["components_json"]:
            try:
                comp = json.loads(prescan["components_json"])
                for side, pk_key, grade_key in [
                    ("Away", "away_pitcher", "away_grade"),
                    ("Home", "home_pitcher", "home_grade"),
                ]:
                    pname = comp.get(pk_key, "")
                    grade = comp.get(grade_key, "?")
                    if not pname:
                        continue
                    last = pname.split()[-1].lower()
                    stats = conn.execute(
                        "SELECT season_era, espn_fip, season_k9, last5_ks "
                        "FROM player_recent_stats "
                        "WHERE cache_date=? AND player_type='pitcher' "
                        "AND LOWER(player) LIKE ? LIMIT 1",
                        (game_date, f"%{last}%"),
                    ).fetchone()
                    if stats:
                        l5 = stats["last5_ks"] or "[]"
                        pitcher_lines.append(
                            f"  {side}: {pname} | grade: {grade} | "
                            f"ERA {stats['season_era'] or 'n/a'} "
                            f"FIP {stats['espn_fip'] or 'n/a'} "
                            f"K/9 {stats['season_k9'] or 'n/a'} | "
                            f"L5 Ks: {l5}"
                        )
                    else:
                        pitcher_lines.append(f"  {side}: {pname} | grade: {grade} | [stats not cached]")
            except Exception:
                pass

        conn.close()

        if prescan:
            lines = [
                f"  Prescan score: {prescan['score']} | "
                f"pitcher_edge: {prescan['pitcher_edge']} | "
                f"k_gap: {prescan['k_gap']} | "
                f"venue_score: {prescan['venue_score']}"
            ]
            lines.extend(pitcher_lines)
            return "\n".join(lines)

    except Exception:
        pass

    return "[no pre-game context cached -- run prescan.py and cache_stats.py first]"


def build_haiku_prompt(
    game_str: str,
    current: dict,
    events: list[str],
    pregame_ctx: str,
) -> str:
    return HAIKU_PROMPT.format(
        pregame_ctx=pregame_ctx,
        game_str=game_str,
        half=current["half"],
        inning=current["inning"],
        away_score=current["away_score"],
        home_score=current["home_score"],
        pitcher_away=current["pitcher_away"] or "unknown",
        pitcher_home=current["pitcher_home"] or "unknown",
        outs=current["outs"],
        events_str=", ".join(events),
    )


def call_haiku(prompt: str) -> str | None:
    """Call Claude Haiku via subprocess. Returns None on failure -- never aborts the loop."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("[watch] 'claude' not found in PATH -- skipping analysis", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--model", MODEL_HAIKU],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        if result.stderr:
            print(f"[watch] haiku error: {result.stderr[:400]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("[watch] haiku timed out (60s) -- skipping analysis", file=sys.stderr)
    except Exception as e:
        print(f"[watch] haiku subprocess error: {e}", file=sys.stderr)
    return None


def ensure_scout_log_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS game_scout_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            game_pk      INTEGER NOT NULL,
            game         TEXT NOT NULL,
            ts           TEXT NOT NULL,
            inning       INTEGER,
            half         TEXT,
            away_score   INTEGER,
            home_score   INTEGER,
            pitcher_home TEXT,
            pitcher_away TEXT,
            event_type   TEXT,
            raw_state    TEXT,
            scout_note   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_game_scout_log_game_pk
            ON game_scout_log(game_pk);
    """)
    conn.commit()


def write_db_row(
    conn: sqlite3.Connection,
    game_pk: int,
    game_str: str,
    current: dict,
    event_type: str,
    scout_note: str | None,
) -> None:
    conn.execute(
        """INSERT INTO game_scout_log
           (game_pk, game, ts, inning, half, away_score, home_score,
            pitcher_home, pitcher_away, event_type, raw_state, scout_note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            game_pk,
            game_str,
            datetime.now().isoformat(timespec="seconds"),
            current["inning"],
            current["half"],
            current["away_score"],
            current["home_score"],
            current["pitcher_home"],
            current["pitcher_away"],
            event_type,
            json.dumps(current["raw"]),
            scout_note,
        ),
    )


def write_md_header(log_path: Path, game_str: str, game_date: str, pregame_ctx: str) -> None:
    with log_path.open("w") as f:
        f.write(f"# Scout Log: {game_str} -- {game_date}\n\n")
        f.write("## Pre-game Context\n")
        f.write(pregame_ctx + "\n\n")
        f.write("---\n\n## Game Log\n")


def write_md_entry(
    log_path: Path,
    current: dict,
    event_type: str,
    scout_note: str | None,
) -> None:
    ts = datetime.now().strftime("%H:%M")
    score_line = (
        f"**[{ts}] {current['half']} {current['inning']} | "
        f"{current['away_score']} - {current['home_score']}** *({event_type})*"
    )
    pitcher_line = (
        f"Away: {current['pitcher_away'] or '--'} | "
        f"Home: {current['pitcher_home'] or '--'}"
    )
    with log_path.open("a") as f:
        f.write("\n---\n")
        f.write(score_line + "\n")
        f.write(pitcher_line + "\n")
        if scout_note:
            f.write("\n" + scout_note + "\n")


def discord_push(webhook_url: str, message: str) -> None:
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={"content": message},
            headers={"User-Agent": "mlb-edge/1.0"},
            timeout=10,
        )
    except Exception:
        pass


def parse_team_names(game_str: str) -> tuple[str, str]:
    """Extract last word of away/home team for use in log filename."""
    parts = re.split(r"\s*@\s*", game_str, maxsplit=1)
    if len(parts) == 2:
        away = re.sub(r"[^\w]", "_", parts[0].strip().split()[-1].lower())
        home = re.sub(r"[^\w]", "_", parts[1].strip().split()[-1].lower())
        return away, home
    return "away", "home"


def main() -> None:
    args = parse_args()
    webhook = os.getenv("SPORTS_WEBHOOK_URL", "") if not args.no_discord else ""
    game_date = args.date or date_cls.today().isoformat()

    if args.game_pk:
        game_pk = args.game_pk
        game_str = resolve_game_str_from_pk(game_pk, game_date) if not args.game else args.game
    elif args.game:
        game_pk = resolve_game_pk(args.game, game_date)
        game_str = args.game
        if not game_pk:
            sys.exit(f"[watch] Could not find game '{args.game}' on {game_date}")
    else:
        sys.exit("[watch] Provide a game string (e.g. 'NYM @ ATL') or --game-pk")

    SCOUT_LOGS.mkdir(exist_ok=True)
    away, home = parse_team_names(game_str)
    log_path = SCOUT_LOGS / f"{game_date}_{away}_{home}.md"

    pregame_ctx = load_pregame_context(game_pk, game_date, game_str)
    write_md_header(log_path, game_str, game_date, pregame_ctx)

    conn = sqlite3.connect(MLB_DB)
    ensure_scout_log_table(conn)

    prev_state: dict | None = None
    seen_innings: set[int] = set()
    tick = 0
    interval = args.interval

    print(f"[watch] Watching: {game_str} (game_pk={game_pk}) on {game_date}")
    print(f"[watch] Log: {log_path}")
    print(f"[watch] Polling every {interval}s. Ctrl-C to abort.")

    while True:
        tick += 1
        current = fetch_live_state(game_pk)

        if current is None:
            print(f"[watch] tick {tick}: fetch failed, retrying in {interval}s")
            time.sleep(interval)
            continue

        # Wait for first pitch
        if current["abstract_state"] == "Preview" and current["inning"] == 0:
            print(f"[watch] tick {tick}: waiting for first pitch ({current['detailed_state']})")
            time.sleep(interval)
            continue

        # Detect events BEFORE updating seen_innings so late_inning/extras trigger on entry
        events = detect_events(current, prev_state, seen_innings)
        event_type = prioritize_event(events)

        # Update seen innings after detection
        if current["inning"] > 0:
            seen_innings.add(current["inning"])

        scout_note = None
        if events:
            prompt = build_haiku_prompt(game_str, current, events, pregame_ctx)
            print(
                f"[watch] tick {tick}: {event_type} -> calling Haiku...",
                end=" ",
                flush=True,
            )
            scout_note = call_haiku(prompt)
            print("done." if scout_note else "failed (skipped).")
        else:
            print(
                f"[watch] tick {tick}: {current['half']} {current['inning']} | "
                f"{current['away_score']}-{current['home_score']} (tick)"
            )

        write_db_row(conn, game_pk, game_str, current, event_type, scout_note)
        conn.commit()
        write_md_entry(log_path, current, event_type, scout_note)

        if scout_note and events and webhook:
            msg = (
                f"**{current['half']} {current['inning']} | "
                f"{game_str} {current['away_score']}-{current['home_score']}** "
                f"[{event_type}]\n{scout_note}"
            )
            discord_push(webhook, msg)

        prev_state = current

        if event_type == "final" or current["inning"] > 18:
            if current["inning"] > 18:
                print("[watch] Safety stop: inning > 18.")
            else:
                print(f"[watch] Game final. Log: {log_path}")
            conn.close()
            break

        time.sleep(interval)


if __name__ == "__main__":
    main()
