#!/usr/bin/env python3
"""
closer.py -- MLB Edge Final Round Critique

Runs after caches are warm. Reads today's lines + stats, fires a 3-agent
sequential debate (Scout -> Skeptic -> Closer), and outputs final picks
with reason strings ready for sliplog.py add --picks.

Usage:
  python closer.py                           # today's full slate
  python closer.py --date 2026-05-18         # specific date
  python closer.py --game "LAD @ LAA"        # single game (substring match)
  python closer.py --dry-run                 # print data brief only
  python closer.py --model haiku             # budget run (faster/cheaper)
"""

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

MLB_DB = Path(__file__).parent / "mlb.db"
BETLOG_DB = Path(__file__).parent / "betlog.db"

MODEL_IDS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-4-7",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MLB Edge Final Round Critique")
    p.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
    p.add_argument("--game", default=None, help="Filter to single game (substring match)")
    p.add_argument("--dry-run", action="store_true", help="Print data brief only, skip agents")
    p.add_argument("--model", default="sonnet", choices=list(MODEL_IDS), help="Claude model tier")
    return p.parse_args()


def build_data_brief(target_date: str, game_filter: str | None) -> str:
    mlb = sqlite3.connect(MLB_DB)
    mlb.row_factory = sqlite3.Row
    bet = sqlite3.connect(BETLOG_DB)
    bet.row_factory = sqlite3.Row

    where = "scraped_date = ?"
    params: list = [target_date]
    if game_filter:
        where += " AND LOWER(game) LIKE ?"
        params.append(f"%{game_filter.lower()}%")

    games = mlb.execute(
        f"SELECT DISTINCT game FROM mlb_game_lines WHERE {where} ORDER BY game", params
    ).fetchall()

    if not games:
        print(f"[closer] No scraped lines found for {target_date}", file=sys.stderr)
        print("[closer] Run /underdog-mlb first to scrape today's lines.", file=sys.stderr)
        sys.exit(1)

    # Pre-fetch all stats/news for this date to avoid N+1 queries
    all_stats = {
        (r["player"], r["player_type"]): r
        for r in mlb.execute(
            "SELECT * FROM player_recent_stats WHERE cache_date=?", (target_date,)
        ).fetchall()
    }
    all_news = {
        r["player"]: r
        for r in mlb.execute(
            "SELECT player, status, note FROM player_news WHERE news_date=?", (target_date,)
        ).fetchall()
    }

    confirmed_rules = bet.execute(
        "SELECT rule_key, hypothesis, direction, occurrences FROM pick_lessons WHERE status='confirmed'"
    ).fetchall()

    mlb.close()
    bet.close()

    # Reopen for per-game line queries (simpler with a fresh connection scoped here)
    mlb = sqlite3.connect(MLB_DB)
    mlb.row_factory = sqlite3.Row

    sections = [f"MLB PROP DATA BRIEF -- {target_date}\n{'='*60}"]

    for g_row in games:
        game = g_row["game"]
        sections.append(f"\n\nGAME: {game}")
        sections.append("-" * 40)

        # Team stats
        teams = mlb.execute(
            "SELECT team_name, bullpen_era, bullpen_whip, bullpen_k9, "
            "team_avg, team_ops, team_k_pct, venue_name, venue_roof "
            "FROM team_game_stats WHERE cache_date=? AND game=?",
            (target_date, game),
        ).fetchall()

        if teams:
            sections.append("\nTEAM CONTEXT:")
            for t in teams:
                roof = f" ({t['venue_roof']})" if t["venue_roof"] else ""
                venue = f"{t['venue_name'] or 'unknown'}{roof}"
                sections.append(
                    f"  {t['team_name']}: bullpen ERA {t['bullpen_era'] or 'n/a'} / "
                    f"WHIP {t['bullpen_whip'] or 'n/a'} / K9 {t['bullpen_k9'] or 'n/a'} | "
                    f"offense avg {t['team_avg'] or 'n/a'} / OPS {t['team_ops'] or 'n/a'} / "
                    f"K% {t['team_k_pct'] or 'n/a'} | venue: {venue}"
                )
        else:
            sections.append("\nTEAM CONTEXT: [not cached -- run cache_team.py]")

        players_in_game = mlb.execute(
            "SELECT DISTINCT player, player_type FROM mlb_game_lines "
            "WHERE scraped_date=? AND game=? ORDER BY player_type, player",
            (target_date, game),
        ).fetchall()

        pitchers = [p for p in players_in_game if p["player_type"] == "pitcher"]
        batters = [p for p in players_in_game if p["player_type"] == "batter"]

        # Pitchers
        if pitchers:
            sections.append("\nPITCHERS:")
            for p in pitchers:
                player = p["player"]
                stats = all_stats.get((player, "pitcher"))
                news = all_news.get(player)
                lines = mlb.execute(
                    "SELECT stat, line, higher_mult, lower_mult FROM mlb_game_lines "
                    "WHERE scraped_date=? AND game=? AND player=?",
                    (target_date, game, player),
                ).fetchall()

                il_flag = ""
                if news and news["status"] not in ("active",):
                    il_flag = f" [IL: {news['status']}]"

                sections.append(f"\n  {player}{il_flag}")
                if stats:
                    l5_str = ""
                    if stats["last5_ks"]:
                        try:
                            ks = json.loads(stats["last5_ks"])
                            l5_str = f" | L5 Ks: {ks}"
                        except Exception:
                            pass
                    fip_flag = ""
                    era = stats["season_era"]
                    fip = stats["espn_fip"]
                    if era and fip:
                        diff = fip - era
                        if diff > 0.75:
                            fip_flag = " [ERA LUCKY]"
                        elif diff < -0.75:
                            fip_flag = " [ERA UNLUCKY]"
                    sections.append(
                        f"    ERA {era or 'n/a'} / FIP {fip or 'n/a'}{fip_flag} / "
                        f"WAR {stats['espn_war'] or 'n/a'} / K9 {stats['season_k9'] or 'n/a'} / "
                        f"K/BB {stats['espn_k_bb'] or 'n/a'} / WHIP {stats['season_whip'] or 'n/a'}"
                        f"{l5_str}"
                    )
                    home_era = stats["split_home_era"]
                    away_era = stats["split_away_era"]
                    if home_era or away_era:
                        sections.append(
                            f"    Splits: home ERA {home_era or 'n/a'} / away ERA {away_era or 'n/a'}"
                        )
                else:
                    sections.append("    Stats: [not cached -- run cache_stats.py + cache_espn.py]")

                for ln in lines:
                    sections.append(
                        f"    {ln['stat']}: {ln['line']} | "
                        f"Higher {ln['higher_mult'] or '--'} / Lower {ln['lower_mult'] or '--'}"
                    )

        # Batters -- show cached ones first, up to 12 per game (brief stays manageable)
        if batters:
            sections.append("\nBATTERS:")
            cached, uncached = [], []
            for p in batters:
                s = all_stats.get((p["player"], "batter"))
                if s:
                    cached.append((p["player"], s))
                else:
                    uncached.append(p["player"])

            for player, stats in cached[:12]:
                news = all_news.get(player)
                il_flag = ""
                if news and news["status"] not in ("active",):
                    il_flag = f" [IL: {news['status']}]"
                lines = mlb.execute(
                    "SELECT stat, line, higher_mult, lower_mult FROM mlb_game_lines "
                    "WHERE scraped_date=? AND game=? AND player=?",
                    (target_date, game, player),
                ).fetchall()

                sections.append(f"\n  {player}{il_flag}")
                sections.append(
                    f"    avg {stats['season_avg'] or 'n/a'} / OPS {stats['season_ops'] or 'n/a'} | "
                    f"L15 H+R+RBI: {stats['last15_h_r_rbi'] or 'n/a'} / Hits: {stats['last15_hits'] or 'n/a'} | "
                    f"xAVG {stats['x_avg'] or 'n/a'} / xwOBA {stats['x_woba'] or 'n/a'}"
                )
                lhp = stats["vs_lhp_avg"]
                rhp = stats["vs_rhp_avg"]
                if lhp or rhp:
                    sections.append(
                        f"    vs LHP avg {lhp or 'n/a'} / vs RHP avg {rhp or 'n/a'} | "
                        f"home avg {stats['split_home_avg'] or 'n/a'} / away avg {stats['split_away_avg'] or 'n/a'}"
                    )
                for ln in lines:
                    sections.append(
                        f"    {ln['stat']}: {ln['line']} | "
                        f"Higher {ln['higher_mult'] or '--'} / Lower {ln['lower_mult'] or '--'}"
                    )

            if uncached:
                sections.append(
                    f"\n  [No stats cached for: {', '.join(uncached[:6])}"
                    f"{'...' if len(uncached) > 6 else ''}]"
                )
            if len(cached) > 12:
                sections.append(f"  [+{len(cached) - 12} more batters with cached stats not shown]")

    mlb.close()

    # Confirmed pick lessons
    if confirmed_rules:
        sections.append(f"\n\n{'='*60}")
        sections.append("CONFIRMED PICK LESSONS (battle-tested rules):")
        for r in confirmed_rules:
            sections.append(
                f"  [{r['direction'].upper()} -- {r['occurrences']}x] "
                f"{r['rule_key']}: {r['hypothesis']}"
            )

    return "\n".join(sections)


def run_agent(prompt: str, label: str, model_id: str) -> str:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print(f"[closer] 'claude' not found in PATH -- cannot run {label}", file=sys.stderr)
        sys.exit(1)

    print(f"[closer] Running {label}...", end=" ", flush=True)
    result = subprocess.run(
        [claude_bin, "-p", prompt, "--model", model_id],
        text=True,
        capture_output=True,
        timeout=300,
    )
    print("done." if result.returncode == 0 else f"exit {result.returncode}.")
    if result.returncode != 0 and result.stderr:
        print(result.stderr[:400], file=sys.stderr)
    return result.stdout.strip()


SCOUT_PROMPT = """You are a sharp MLB prop analyst with years of experience finding edges in player prop lines.
You look for situations where the line is clearly beatable based on recent performance, advanced stats, and matchup context.

Here is today's complete slate with all available stats:

{data_brief}

TASK: Find the 6-8 highest-confidence prop picks across today's slate.

For each pick, provide a numbered entry with:
- Player | Stat | Line | Side (Higher/Lower) | Game
- Bull thesis (2-3 sentences): cite specific numbers -- L5 trend vs the line, FIP vs ERA, K9, splits, confirmed rules.

Prioritize:
- Recent trend clearly beats/misses the line (L5 above/below by meaningful margin)
- FIP supports ERA (not a regression candidate)
- Multiplier >= 1.00x on your side (market not strongly opposed)
- No IL flags

Do NOT output JSON yet. Write as a numbered plain-text list."""

SKEPTIC_PROMPT = """You are a ruthless bet-checker. Your only job is to find reasons picks fail.
You are NOT being constructive -- you are preventing bad bets.

Today's data:
{data_brief}

Scout's proposed picks:
{scout_output}

TASK: For each Scout pick (match the numbering), write the 2 strongest counter-arguments.
Rate each: Strong / Moderate / Weak.

Flag aggressively:
- Small sample (< 5 recent starts/games)
- IL return within 7 days (rust factor)
- Low multiplier on Scout's side (market disagrees)
- Opposing split advantage for batters
- Starter early exit risk on K props
- ERA LUCKY flag (regression incoming)
- ELITE offense opposing a pitcher Lower prop
- No confirmed pick lesson backing the pick

Write as numbered list matching Scout's numbering. Be brutal -- if a pick has no strong counter, say clearly why."""

CLOSER_PROMPT = """You are the final decision-maker for MLB prop bets.
You have seen the bull case from Scout and the ruthless challenges from Skeptic.
Cut without mercy. Only keep picks where the bull case clearly survives the Skeptic's strongest challenge.

Today's data:
{data_brief}

Scout's picks:
{scout_output}

Skeptic's challenges:
{skeptic_output}

SELECTION RULES:
- Drop if Skeptic's strongest counter is Strong AND you cannot specifically rebut it
- Drop if any IL flag is present (active IL or returned within 7 days)
- Drop if multiplier on the pick side is below 0.95x (market has priced this out)
- Keep only where Scout's evidence is specific and Skeptic's counters are Moderate or Weak

For each surviving pick, write a 'reason' string (1-2 tight sentences):
  "[Key stat evidence with numbers]. [Skeptic's main counter + one-line rebuttal or why it doesn't kill the pick]."

Example: "L5 avg 8.4 Ks vs 6.5 line; FIP 2.81 confirms ERA not luck. Skeptic: low mult (1.03x) -- clears on evidence volume."

OUTPUT: Return ONLY valid JSON array. No markdown fences, no preamble, no explanation. Start with [ and end with ].

Schema for each pick object:
{{
  "player": "string",
  "player_type": "pitcher" or "batter" or "team",
  "stat": "string (exact stat name from the data)",
  "line": number,
  "side": "Higher" or "Lower",
  "game": "TEAM @ TEAM",
  "reason": "string"
}}

If fewer than 3 picks survive, output what remains -- do not force weak picks to reach a minimum."""


def run_scout(data_brief: str, model_id: str) -> str:
    return run_agent(SCOUT_PROMPT.format(data_brief=data_brief), "Scout", model_id)


def run_skeptic(data_brief: str, scout_output: str, model_id: str) -> str:
    return run_agent(
        SKEPTIC_PROMPT.format(data_brief=data_brief, scout_output=scout_output),
        "Skeptic",
        model_id,
    )


def run_closer(data_brief: str, scout_output: str, skeptic_output: str, model_id: str) -> list[dict]:
    raw = run_agent(
        CLOSER_PROMPT.format(
            data_brief=data_brief, scout_output=scout_output, skeptic_output=skeptic_output
        ),
        "Closer",
        model_id,
    )

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    # Find first [ ... ] block
    bracket = re.search(r"(\[.*\])", raw, re.DOTALL)
    if bracket:
        try:
            return json.loads(bracket.group(1))
        except json.JSONDecodeError:
            pass

    print("[closer] ERROR: Closer returned unparseable JSON. Raw output below:", file=sys.stderr)
    print(raw, file=sys.stderr)
    sys.exit(1)


def _wrap(text: str, indent: str = "    ", width: int = 76) -> str:
    words = text.split()
    lines, current = [], []
    for w in words:
        current.append(w)
        if len(indent + " ".join(current)) > width:
            lines.append(indent + " ".join(current[:-1]))
            current = [w]
    if current:
        lines.append(indent + " ".join(current))
    return "\n".join(lines)


def print_results(picks: list[dict], scout_output: str, skeptic_output: str) -> None:
    print()
    print("=" * 60)
    print(f"  FINAL PICKS ({len(picks)} survived debate)")
    print("=" * 60)

    if not picks:
        print("\n  No picks cleared the debate. Slate is thin or market is efficient today.")
        return

    for i, p in enumerate(picks, 1):
        player = p.get("player", "?")
        stat = p.get("stat", "?")
        line = p.get("line", "?")
        side = p.get("side", "?")
        game = p.get("game", "unknown game")
        reason = p.get("reason", "")
        print(f"\n  {i}. {player}  --  {stat} {line} {side}")
        print(f"     {game}")
        if reason:
            print(_wrap(reason))

    # Debate summary: count Scout's proposed picks from numbered list
    numbered = re.findall(r"^\s*\d+\.", scout_output, re.MULTILINE)
    proposed = len(numbered) if numbered else "?"
    dropped = (proposed - len(picks)) if isinstance(proposed, int) else "?"
    print()
    print("-" * 60)
    print(
        f"  Debate: Scout proposed ~{proposed} | Skeptic challenged all | "
        f"Closer kept {len(picks)} (dropped ~{dropped})"
    )
    if isinstance(len(picks), int) and len(picks) < 3:
        print(f"  WARNING: thin slate -- only {len(picks)} pick(s) cleared")

    # Sliplog command
    picks_json = json.dumps(picks, separators=(",", ":"))
    print()
    print("=" * 60)
    print("  READY TO LOG:")
    print()
    print(f"  python sliplog.py add --entry 20 --payout TBD --picks '{picks_json}'")
    print("=" * 60)


def main() -> None:
    args = parse_args()
    target_date = args.date or date.today().isoformat()
    model_id = MODEL_IDS[args.model]

    print(f"[closer] Building data brief for {target_date}...", flush=True)
    data_brief = build_data_brief(target_date, args.game)

    if args.dry_run:
        print(data_brief)
        return

    scout = run_scout(data_brief, model_id)
    skeptic = run_skeptic(data_brief, scout, model_id)
    picks = run_closer(data_brief, scout, skeptic, model_id)
    print_results(picks, scout, skeptic)


if __name__ == "__main__":
    main()
