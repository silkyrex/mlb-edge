#!/usr/bin/env python3
"""
watch.py -- Three-layer live MLB game scout.

Layer 1  Haiku  : raw field observation on every meaningful event
Layer 2  Sonnet : final analysis + opinionated critical read at game end

All output goes to mlb.db (game_scout_log + game_scout_summary) and
~/mlb-edge/scout_logs/YYYY-MM-DD_{away}_{home}.md.
Checkpoints + final sections push to Discord.

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
MODEL_SONNET = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

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

FINAL_ANALYSIS_PROMPT = """You are a professional MLB game analyst. The game is final.
Read the complete scout log and write the definitive game summary.

PRE-GAME CONTEXT:
{pregame_ctx}

COMPLETE SCOUT LOG:
{all_notes}

FINAL SCORE: {away_score}-{home_score} ({game_str})

Write 6-8 sentences:
- What was the dominant narrative of this game?
- How did the starters perform vs their pre-game grades?
- Which props would have hit or missed, and why?
- What was the key turning point?
- What does this game tell us for future bets on these teams or pitchers?

Be specific. Reference pre-game grades and how reality compared.
Write only the analysis. No headers, no labels."""

CRITICAL_READ_PROMPT = """You are a harsh, opinionated betting analyst. Tear apart what happened.

PRE-GAME CONTEXT (what was expected):
{pregame_ctx}

COMPLETE SCOUT LOG (what actually happened):
{all_notes}

FINAL SCORE: {away_score}-{home_score} ({game_str})

Write 4-6 sentences. Be direct and opinionated:
- What did the pre-game model get wrong?
- Which props should have been avoided in hindsight?
- Was the pre-game edge read correct or not?
- What pattern should be remembered next time these teams or pitchers show up?

No softening. No "could have gone either way." Pick a lane and commit.
Write only the critical read. No headers, no labels."""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Three-layer live MLB game scout")
    p.add_argument("game", nargs="?", help='Game string e.g. "NYM @ ATL"')
    p.add_argument("--game-pk", type=int, default=None)
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--no-discord", action="store_true")
    p.add_argument("--interval", type=int, default=POLL_INTERVAL,
                   help="Poll interval in seconds (default 300)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# MLB Stats API helpers
# ---------------------------------------------------------------------------

def resolve_game_pk(game_str: str, game_date: str) -> int | None:
    words = [w.lower() for w in re.split(r"[\s@/]+", game_str) if len(w) > 2]
    try:
        r = requests.get(f"{BASE}/schedule",
                         params={"sportId": 1, "date": game_date}, timeout=10)
        if r.status_code != 200:
            return None
        for d in r.json().get("dates", []):
            for game in d.get("games", []):
                teams = game["teams"]
                names = (teams["away"]["team"]["name"].lower() +
                         teams["home"]["team"]["name"].lower())
                if sum(1 for w in words if w in names) >= 2:
                    return game["gamePk"]
    except Exception as e:
        print(f"[watch] schedule lookup error: {e}", file=sys.stderr)
    return None


def resolve_game_str_from_pk(game_pk: int, game_date: str) -> str:
    try:
        r = requests.get(f"{BASE}/schedule",
                         params={"sportId": 1, "gamePk": game_pk}, timeout=10)
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
    try:
        r = requests.get(f"{BASE}/schedule",
                         params={"sportId": 1, "gamePk": game_pk}, timeout=10)
        if r.status_code == 200:
            for d in r.json().get("dates", []):
                for g in d.get("games", []):
                    if g["gamePk"] == game_pk:
                        status = g.get("status", {})
                        return (status.get("abstractGameState", "Preview"),
                                status.get("detailedState", ""))
    except Exception:
        pass
    return ("Preview", "")


def _get_current_pitchers(game_pk: int) -> tuple[str, str]:
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


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------

def detect_events(current: dict, prev: dict | None, seen_innings: set) -> list[str]:
    if prev is None:
        return []
    if (current["abstract_state"] == "Final" or
            current["detailed_state"] in ("Final", "Game Over")):
        return ["final"]
    events = []
    if (current["away_score"] != prev["away_score"] or
            current["home_score"] != prev["home_score"]):
        events.append("run_scored")
    for side in ("away", "home"):
        key = f"pitcher_{side}"
        if current[key] and prev[key] and current[key] != prev[key]:
            if "pitching_change" not in events:
                events.append("pitching_change")
    if 7 <= current["inning"] <= 9 and current["inning"] not in seen_innings:
        events.append("late_inning")
    if current["inning"] > 9 and current["inning"] not in seen_innings:
        events.append("extras")
    return events


def prioritize_event(events: list[str]) -> str:
    for p in ("final", "run_scored", "pitching_change", "extras", "late_inning"):
        if p in events:
            return p
    return "tick"


# ---------------------------------------------------------------------------
# Pre-game context
# ---------------------------------------------------------------------------

def load_pregame_context(game_pk: int, game_date: str, game_str: str) -> str:
    try:
        conn = sqlite3.connect(MLB_DB)
        conn.row_factory = sqlite3.Row
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
                        pitcher_lines.append(
                            f"  {side}: {pname} | grade: {grade} | [stats not cached]"
                        )
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


# ---------------------------------------------------------------------------
# Claude subprocess caller
# ---------------------------------------------------------------------------

def call_model(prompt: str, model: str, label: str, timeout: int = 90) -> str | None:
    """Call a Claude model via subprocess. Returns None on failure -- never aborts the loop."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print(f"[watch] 'claude' not found in PATH -- skipping {label}", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--model", model],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        if result.stderr:
            print(f"[watch] {label} error: {result.stderr[:400]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[watch] {label} timed out ({timeout}s) -- skipping", file=sys.stderr)
    except Exception as e:
        print(f"[watch] {label} subprocess error: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_haiku_prompt(
    game_str: str, current: dict, events: list[str], pregame_ctx: str
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


def build_final_analysis_prompt(
    game_str: str, current: dict, pregame_ctx: str, all_notes: str
) -> str:
    return FINAL_ANALYSIS_PROMPT.format(
        pregame_ctx=pregame_ctx,
        all_notes=all_notes or "[no notes recorded]",
        game_str=game_str,
        away_score=current["away_score"],
        home_score=current["home_score"],
    )


def build_critical_read_prompt(
    game_str: str, current: dict, pregame_ctx: str, all_notes: str
) -> str:
    return CRITICAL_READ_PROMPT.format(
        pregame_ctx=pregame_ctx,
        all_notes=all_notes or "[no notes recorded]",
        game_str=game_str,
        away_score=current["away_score"],
        home_score=current["home_score"],
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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
            scout_note   TEXT,
            sonnet_note  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_game_scout_log_game_pk
            ON game_scout_log(game_pk);
    """)
    # Migration: add sonnet_note to existing tables created before this version
    try:
        conn.execute("ALTER TABLE game_scout_log ADD COLUMN sonnet_note TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()


def ensure_summary_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS game_scout_summary (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            game_pk        INTEGER NOT NULL UNIQUE,
            game           TEXT NOT NULL,
            game_date      TEXT NOT NULL,
            ts             TEXT NOT NULL,
            final_score    TEXT,
            final_analysis TEXT,
            critical_read  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_gss_game_pk
            ON game_scout_summary(game_pk);
    """)
    conn.commit()


def get_all_scout_notes(conn: sqlite3.Connection, game_pk: int) -> str:
    """Return all Haiku scout notes for this game as a formatted text block."""
    rows = conn.execute(
        "SELECT inning, half, away_score, home_score, event_type, scout_note "
        "FROM game_scout_log "
        "WHERE game_pk=? AND scout_note IS NOT NULL "
        "ORDER BY id",
        (game_pk,),
    ).fetchall()
    if not rows:
        return ""
    parts = []
    for r in rows:
        header = (f"[Inn {r['inning']} {r['half']} | "
                  f"{r['away_score']}-{r['home_score']} | {r['event_type']}]")
        parts.append(f"{header}\n{r['scout_note']}")
    return "\n\n".join(parts)


def write_db_row(
    conn: sqlite3.Connection,
    game_pk: int,
    game_str: str,
    current: dict,
    event_type: str,
    scout_note: str | None,
    sonnet_note: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO game_scout_log
           (game_pk, game, ts, inning, half, away_score, home_score,
            pitcher_home, pitcher_away, event_type, raw_state, scout_note, sonnet_note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            sonnet_note,
        ),
    )


def write_summary_db(
    conn: sqlite3.Connection,
    game_pk: int,
    game_str: str,
    game_date: str,
    current: dict,
    final_analysis: str | None,
    critical_read: str | None,
) -> None:
    final_score = f"{current['away_score']}-{current['home_score']}"
    conn.execute(
        """INSERT OR REPLACE INTO game_scout_summary
           (game_pk, game, game_date, ts, final_score, final_analysis, critical_read)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            game_pk,
            game_str,
            game_date,
            datetime.now().isoformat(timespec="seconds"),
            final_score,
            final_analysis,
            critical_read,
        ),
    )


# ---------------------------------------------------------------------------
# Markdown writers
# ---------------------------------------------------------------------------

def write_md_header(
    log_path: Path, game_str: str, game_date: str, pregame_ctx: str
) -> None:
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
            f.write("\n**Scout (Haiku):** " + scout_note + "\n")


def write_md_final_sections(
    log_path: Path, final_analysis: str, critical_read: str
) -> None:
    with log_path.open("a") as f:
        f.write("\n---\n\n## Final Analysis\n\n")
        f.write(final_analysis + "\n")
        f.write("\n---\n\n## Critical Read\n\n")
        f.write(critical_read + "\n")


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def discord_push(message: str) -> None:
    try:
        from discord_manager import send as _dm_send
        _dm_send("mlb_edge_alert", message)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def parse_team_names(game_str: str) -> tuple[str, str]:
    parts = re.split(r"\s*@\s*", game_str, maxsplit=1)
    if len(parts) == 2:
        away = re.sub(r"[^\w]", "_", parts[0].strip().split()[-1].lower())
        home = re.sub(r"[^\w]", "_", parts[1].strip().split()[-1].lower())
        return away, home
    return "away", "home"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    discord_enabled = not args.no_discord
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
    conn.row_factory = sqlite3.Row
    ensure_scout_log_table(conn)
    ensure_summary_table(conn)

    prev_state: dict | None = None
    seen_innings: set[int] = set()
    tick = 0
    interval = args.interval

    print(f"[watch] Watching: {game_str} (game_pk={game_pk}) on {game_date}")
    print(f"[watch] Log: {log_path}")
    print(f"[watch] Haiku: per-event | Sonnet: final analysis + critical read only")
    print(f"[watch] Polling every {interval}s. Ctrl-C to abort.")

    while True:
        tick += 1
        current = fetch_live_state(game_pk)

        if current is None:
            print(f"[watch] tick {tick}: fetch failed, retrying in {interval}s")
            time.sleep(interval)
            continue

        if current["abstract_state"] == "Preview" and current["inning"] == 0:
            print(f"[watch] tick {tick}: waiting for first pitch ({current['detailed_state']})")
            time.sleep(interval)
            continue

        # Detect events BEFORE updating seen_innings (late_inning/extras trigger on entry)
        events = detect_events(current, prev_state, seen_innings)
        event_type = prioritize_event(events)

        if current["inning"] > 0:
            seen_innings.add(current["inning"])

        # --- Layer 1: Haiku scout note (every meaningful event) ---
        scout_note = None
        if events:
            prompt = build_haiku_prompt(game_str, current, events, pregame_ctx)
            print(
                f"[watch] tick {tick}: {event_type} -> Haiku...",
                end=" ", flush=True,
            )
            scout_note = call_model(prompt, MODEL_HAIKU, "Haiku", 60)
            print("done." if scout_note else "failed (skipped).")
        else:
            print(
                f"[watch] tick {tick}: {current['half']} {current['inning']} | "
                f"{current['away_score']}-{current['home_score']} (tick)"
            )

        write_db_row(conn, game_pk, game_str, current, event_type, scout_note)
        conn.commit()
        write_md_entry(log_path, current, event_type, scout_note)

        if scout_note and events and discord_enabled:
            discord_push(
                f"**{current['half']} {current['inning']} | "
                f"{game_str} {current['away_score']}-{current['home_score']}** "
                f"[{event_type}]\n{scout_note}")

        prev_state = current

        # --- Game over ---
        if event_type == "final" or current["inning"] > 18:
            if current["inning"] > 18:
                print("[watch] Safety stop: inning > 18.")
            else:
                print(f"[watch] Game final. Running end-game analysis...")

            all_notes = get_all_scout_notes(conn, game_pk)
            final_score = f"{current['away_score']}-{current['home_score']}"

            # Layer 3a: Final analysis
            fa_prompt = build_final_analysis_prompt(game_str, current, pregame_ctx, all_notes)
            print("[watch] Final analysis (Sonnet)...", end=" ", flush=True)
            final_analysis = call_model(fa_prompt, MODEL_SONNET, "Sonnet-final", 120)
            print("done." if final_analysis else "failed.")

            # Layer 3b: Critical read
            cr_prompt = build_critical_read_prompt(game_str, current, pregame_ctx, all_notes)
            print("[watch] Critical read (Sonnet)...", end=" ", flush=True)
            critical_read = call_model(cr_prompt, MODEL_SONNET, "Sonnet-critical", 120)
            print("done." if critical_read else "failed.")

            write_md_final_sections(
                log_path,
                final_analysis or "[analysis failed]",
                critical_read or "[critical read failed]",
            )
            write_summary_db(conn, game_pk, game_str, game_date, current,
                             final_analysis, critical_read)
            conn.commit()

            if discord_enabled:
                if final_analysis:
                    discord_push(
                        f"**Final Analysis: {game_str} {final_score}**\n{final_analysis}")
                if critical_read:
                    discord_push(
                        f"**Critical Read: {game_str} {final_score}**\n{critical_read}")

            print(f"[watch] Done. Log: {log_path}")
            conn.close()
            break

        time.sleep(interval)


if __name__ == "__main__":
    main()
