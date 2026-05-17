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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import requests

MLB_DB = Path(__file__).parent / "mlb.db"
SLIPLOG_DB = Path(__file__).parent / "sliplog.db"

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
    bet = sqlite3.connect(SLIPLOG_DB)
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

    # Fetch game context (lineup + ump) and ump ratings -- live, not cached in DB
    schedule_ctx = fetch_schedule_context(target_date)
    ump_ratings = fetch_ump_ratings()

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

        # Game context: umpire + today's lineup card
        gctx = _match_game_ctx(game, schedule_ctx)
        sections.append("\nGAME CONTEXT:")
        if gctx:
            # Umpire
            ump = gctx.get("ump_name", "")
            if ump:
                ur = ump_ratings.get(ump.lower(), {})
                if ur:
                    acc = f"acc {_fmt(ur['accuracy_pct'], 1)}%" if ur.get("accuracy_pct") else ""
                    err = f"err {ur['error_rate_pct']}%" if ur.get("error_rate_pct") else ""
                    score = f"score {_fmt(ur['weighted_score'], 1)}" if ur.get("weighted_score") else ""
                    games_str = f"{ur['games']}g"
                    sections.append(f"  HP Ump: {ump} | {games_str} | {acc} | {err} | {score}")
                else:
                    sections.append(f"  HP Ump: {ump} | [no rating data this season]")
            else:
                sections.append("  HP Ump: [not yet assigned]")
            # Lineups
            for side, lineup_key, label in [
                ("away", "away_lineup", gctx.get("away_name", "Away")),
                ("home", "home_lineup", gctx.get("home_name", "Home")),
            ]:
                lineup = gctx.get(lineup_key, [])
                if lineup and any(p["name"] for p in lineup):
                    order = " ".join(
                        f"{i+1}.{(p['name'].split()[-1] if p['name'] else '?')}({p['bats']})"
                        for i, p in enumerate(lineup[:9])
                    )
                    sections.append(f"  {label}: {order}")
                else:
                    sections.append(f"  {label} lineup: [not posted yet -- check closer to game time]")
        else:
            sections.append("  [Game context unavailable -- schedule API returned no match]")

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
                    rest_str = ""
                    pid = stats["mlb_player_id"]
                    if pid:
                        rest = pitcher_days_rest(pid, target_date)
                        if rest is not None:
                            label = "short" if rest <= 3 else "extra" if rest >= 6 else "regular"
                            rest_str = f" | rest {rest}d ({label})"
                    sections.append(
                        f"    ERA {era or 'n/a'} / FIP {fip or 'n/a'}{fip_flag} / "
                        f"WAR {stats['espn_war'] or 'n/a'} / K9 {stats['season_k9'] or 'n/a'} / "
                        f"K/BB {stats['espn_k_bb'] or 'n/a'} / WHIP {stats['season_whip'] or 'n/a'}"
                        f"{l5_str}{rest_str}"
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
- Opposing split advantage for batters (check lineup handedness vs pitcher arm)
- Starter early exit risk on K props
- ERA LUCKY flag (regression incoming)
- ELITE offense opposing a pitcher Lower prop
- No confirmed pick lesson backing the pick
- Short rest (3d or less) -- fewer Ks, worse command
- Extra rest (6d+) -- potential rust, rough first inning
- HP umpire with high error rate or tight zone tendency -- suppresses K props
- Lineup missing a key bat (star sitting) -- reduces opposing K exposure or H+R+RBI ceiling

Write as numbered list matching Scout's numbering. Be brutal -- if a pick has no strong counter, say clearly why."""

CLOSER_PROMPT = """You are the final decision-maker for MLB prop bets.
You have seen the bull case from Scout, the ruthless challenges from Skeptic, and fresh Statcast data
pulled live from the MLB Stats API (same source as Baseball Savant).
Cut without mercy. Only keep picks where the bull case clearly survives the Skeptic's strongest challenge.

Today's cached data:
{data_brief}

Scout's picks:
{scout_output}

Skeptic's challenges:
{skeptic_output}

LIVE STATCAST DATA (fetched now -- use this to override stale cached stats):
{savant_supplement}

SELECTION RULES:
- Drop if Skeptic's strongest counter is Strong AND you cannot specifically rebut it
- Drop if any IL flag is present (active IL or returned within 7 days)
- Drop if multiplier on the pick side is below 0.95x (market has priced this out)
- Keep only where Scout's evidence is specific and Skeptic's counters are Moderate or Weak
- FLAG: if live Statcast data contradicts Scout's thesis (e.g. xFIP much higher than FIP, velocity down,
  wRC+ trending negative), either drop the pick or explicitly note the conflict in the reason string
- FLAG: if live Statcast data validates Skeptic's counter, weight that counter more heavily
- FLAG: ump with high error_rate or low accuracy -- downgrade K props for both pitchers in that game
- FLAG: short rest pitcher (3d) -- reduce confidence on K Higher props; Extra rest (6d+) -- note rust risk
- FLAG: lineup missing a key bat -- reduces H+R+RBI ceiling for that team's batters
- NOTE: lineup handedness is provided -- use batter hand vs pitcher arm to validate or challenge split-based picks

For each surviving pick, write a 'reason' string (1-2 tight sentences):
  "[Key stat evidence with numbers, including any live Statcast metric that clinches the case].
   [Skeptic's main counter + one-line rebuttal]. [If Statcast flagged anything, note it explicitly]."

Example: "L5 avg 8.4 Ks vs 6.5 line; live xFIP 2.91 < FIP 3.10 -- ERA is sustainable. Skeptic: low mult (1.03x) -- evidence volume clears it."
Example with flag: "STATCAST FLAG: velocity down 1.8 mph from season avg -- Skeptic's regression concern confirmed; DROPPED."

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

# ---------------------------------------------------------------------------
# Shared MLB Stats API helpers
# ---------------------------------------------------------------------------

_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def _fmt(val, decimals: int = 2) -> str:
    """Round a numeric API value to a readable string."""
    if val is None:
        return "n/a"
    try:
        return f"{float(val):.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


# ---------------------------------------------------------------------------
# Game context: lineup card + umpire + pitcher days rest
# Fetched inline in build_data_brief() -- no DB cache (lineup changes pre-game)
# ---------------------------------------------------------------------------

def fetch_schedule_context(target_date: str) -> dict[str, dict]:
    """
    Single MLB Stats API call: lineups + HP umpire for all today's games.
    Returns {"{Away} @ {Home}": {ump_name, away_lineup, home_lineup, away_name, home_name}}
    """
    try:
        r = requests.get(
            f"{_STATS_BASE}/schedule",
            params={"sportId": 1, "date": target_date, "hydrate": "lineups,officials"},
            timeout=10,
            headers={"User-Agent": "mlb-edge/1.0"},
        )
        r.raise_for_status()
        dates = r.json().get("dates", [])
        games = dates[0].get("games", []) if dates else []
    except Exception:
        return {}

    result = {}
    for g in games:
        away_name = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
        home_name = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "")

        ump_name = ""
        for off in g.get("officials", []):
            if off.get("officialType") == "Home Plate":
                ump_name = off.get("official", {}).get("fullName", "")
                break

        lineups = g.get("lineups", {})
        def _parse_lineup(players: list) -> list[dict]:
            return [
                {"name": p.get("person", {}).get("fullName", ""), "bats": p.get("batSide", {}).get("code", "?")}
                for p in players
            ]

        result[f"{away_name} @ {home_name}"] = {
            "ump_name": ump_name,
            "away_lineup": _parse_lineup(lineups.get("awayPlayers", [])),
            "home_lineup": _parse_lineup(lineups.get("homePlayers", [])),
            "away_name": away_name,
            "home_name": home_name,
        }
    return result


def fetch_ump_ratings() -> dict[str, dict]:
    """
    Fetch season ump ratings from umpscorecards.com.
    Returns {ump_name_lower: {name, games, accuracy_pct, error_rate_pct, weighted_score}}
    """
    try:
        r = requests.get(
            "https://umpscorecards.com/api/umpires",
            timeout=10,
            headers={"User-Agent": "mlb-edge/1.0"},
        )
        r.raise_for_status()
        rows = r.json().get("rows", [])
    except Exception:
        return {}

    result = {}
    for row in rows:
        name = row.get("umpire", "")
        if not name:
            continue
        called = row.get("called_pitches_sum") or 0
        wrong = row.get("called_wrong_sum") or 0
        result[name.lower()] = {
            "name": name,
            "games": row.get("n", 0),
            "accuracy_pct": row.get("overall_accuracy_wmean"),
            "error_rate_pct": round(wrong / called * 100, 1) if called else None,
            "weighted_score": row.get("weighted_score"),
        }
    return result


def _match_game_ctx(game_str: str, ctx_map: dict[str, dict]) -> dict | None:
    """
    Fuzzy-match mlb_game_lines.game string (e.g. 'Los Angeles Dodgers @ Los Angeles Angels'
    or 'LAD @ LAA') against schedule API full team names.
    Matches on last word of each team name (e.g. 'Dodgers', 'Angels').
    """
    if not ctx_map:
        return None
    if game_str in ctx_map:
        return ctx_map[game_str]
    game_lower = game_str.lower()
    for ctx in ctx_map.values():
        away_last = ctx.get("away_name", "").split()[-1].lower()
        home_last = ctx.get("home_name", "").split()[-1].lower()
        if away_last and home_last and away_last in game_lower and home_last in game_lower:
            return ctx
    return None


def pitcher_days_rest(player_id: int, target_date: str) -> int | None:
    """Days since last pitching appearance. None if no recent game log."""
    splits = _stats_get(player_id, "gameLog", "pitching")
    if not splits:
        return None
    today = date.fromisoformat(target_date)
    past_dates = []
    for s in splits:
        d_str = s.get("date")
        if d_str:
            try:
                d = date.fromisoformat(d_str)
                if d < today:
                    past_dates.append(d)
            except ValueError:
                pass
    if not past_dates:
        return None
    return (today - max(past_dates)).days


def extract_candidate_players(scout_output: str, target_date: str) -> list[tuple[str, str, int]]:
    """
    Return (player_name, player_type, mlb_player_id) for players mentioned
    in Scout's output that have a cached mlb_player_id for today.
    Matches on last name (robust to formatting differences).
    """
    mlb = sqlite3.connect(MLB_DB)
    mlb.row_factory = sqlite3.Row
    rows = mlb.execute(
        "SELECT player, player_type, mlb_player_id FROM player_recent_stats "
        "WHERE cache_date=? AND mlb_player_id IS NOT NULL",
        (target_date,),
    ).fetchall()
    mlb.close()

    scout_lower = scout_output.lower()
    seen: set[int] = set()
    candidates = []
    for row in rows:
        pid = row["mlb_player_id"]
        if pid in seen:
            continue
        last = re.escape(row["player"].split()[-1].lower())
        if re.search(rf"\b{last}\b", scout_lower):
            seen.add(pid)
            candidates.append((row["player"], row["player_type"], pid))
    return candidates


def _stats_get(player_id: int, stat_type: str, group: str) -> list[dict]:
    """Single-stat-type request to MLB Stats API (API does not accept multiple types per call)."""
    season = str(date.today().year)
    try:
        r = requests.get(
            f"{_STATS_BASE}/people/{player_id}/stats",
            params={"stats": stat_type, "group": group, "season": season},
            timeout=10,
            headers={"User-Agent": "mlb-edge/1.0"},
        )
        if r.status_code != 200:
            return []
        blocks = r.json().get("stats") or []
        return blocks[0].get("splits", []) if blocks else []
    except Exception:
        return []


def fetch_statcast(player_name: str, player_type: str, player_id: int) -> dict:
    """
    Fetch live Statcast metrics from MLB Stats API for one player.
    Returns a dict of the most useful fields; sets 'error' key on failure.
    """
    group = "pitching" if player_type == "pitcher" else "hitting"
    result: dict = {"player": player_name, "player_type": player_type}

    # Expected stats (xBA, xSLG, xwOBA)
    exp = _stats_get(player_id, "expectedStatistics", group)
    if exp:
        s = exp[0].get("stat", {})
        result["x_ba"] = s.get("avg")
        result["x_slg"] = s.get("slg")
        result["x_woba"] = s.get("woba")

    # Sabermetrics (FIP/xFIP/WAR/ERA- for pitchers; wRC+/WAR/wOBA for batters)
    sab = _stats_get(player_id, "sabermetrics", group)
    if sab:
        s = sab[0].get("stat", {})
        if player_type == "pitcher":
            result["fip"] = s.get("fip")
            result["x_fip"] = s.get("xfip")
            result["war"] = s.get("war")
            result["era_minus"] = s.get("eraMinus")
        else:
            result["wrc_plus"] = s.get("wRcPlus")
            result["war"] = s.get("war")
            result["woba"] = s.get("woba")

    # Pitch arsenal (pitchers only)
    if player_type == "pitcher":
        arsenal_splits = _stats_get(player_id, "pitchArsenal", group)
        if arsenal_splits:
            parts = []
            for split in arsenal_splits:
                ps = split.get("stat", {})
                pname = ps.get("type", {}).get("description", "")
                mph = ps.get("averageSpeed")
                pct = ps.get("percentage")
                if pname and mph:
                    pct_str = f" ({pct*100:.0f}%)" if pct else ""
                    parts.append(f"{pname} {mph:.1f}mph{pct_str}")
            if parts:
                result["arsenal"] = ", ".join(parts)

    return result


def build_savant_supplement(candidates: list[tuple[str, str, int]]) -> str:
    """
    Fetch live Statcast for all candidate players in parallel and format
    as a text block for the Closer prompt.
    """
    if not candidates:
        return "[No candidate players identified -- Statcast supplement unavailable]"

    print(f"[closer] Fetching live Statcast for {len(candidates)} candidates...", end=" ", flush=True)

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(fetch_statcast, name, ptype, pid): name
            for name, ptype, pid in candidates
        }
        for fut in as_completed(futures):
            data = fut.result()
            results[data["player"]] = data

    print("done.")

    lines = ["Live Statcast (MLB Stats API, fetched now):"]
    for name, _, _ in candidates:
        d = results.get(name, {})
        if "error" in d:
            lines.append(f"  {name}: [fetch failed -- {d['error']}]")
            continue

        ptype = d.get("player_type", "?")
        parts = []
        if ptype == "pitcher":
            if d.get("x_fip"):
                parts.append(f"live xFIP {_fmt(d['x_fip'])}")
            if d.get("fip"):
                parts.append(f"FIP {_fmt(d['fip'])}")
            if d.get("war"):
                parts.append(f"WAR {_fmt(d['war'], 1)}")
            if d.get("era_minus"):
                parts.append(f"ERA- {_fmt(d['era_minus'], 0)}")
            if d.get("x_woba"):
                parts.append(f"xwOBA-against {d['x_woba']}")
            if d.get("arsenal"):
                parts.append(f"arsenal: {d['arsenal']}")
        else:
            if d.get("wrc_plus"):
                parts.append(f"wRC+ {_fmt(d['wrc_plus'], 0)}")
            if d.get("war"):
                parts.append(f"WAR {_fmt(d['war'], 1)}")
            if d.get("x_woba"):
                parts.append(f"xwOBA {d['x_woba']}")
            if d.get("x_ba"):
                parts.append(f"xBA {d['x_ba']}")
            if d.get("x_slg"):
                parts.append(f"xSLG {d['x_slg']}")

        if parts:
            lines.append(f"  {name} ({ptype}): {' | '.join(parts)}")
        else:
            lines.append(f"  {name}: [no Statcast data returned for this season]")

    return "\n".join(lines)


def run_scout(data_brief: str, model_id: str) -> str:
    return run_agent(SCOUT_PROMPT.format(data_brief=data_brief), "Scout", model_id)


def run_skeptic(data_brief: str, scout_output: str, model_id: str) -> str:
    return run_agent(
        SKEPTIC_PROMPT.format(data_brief=data_brief, scout_output=scout_output),
        "Skeptic",
        model_id,
    )


def run_closer(
    data_brief: str,
    scout_output: str,
    skeptic_output: str,
    savant_supplement: str,
    model_id: str,
) -> list[dict]:
    raw = run_agent(
        CLOSER_PROMPT.format(
            data_brief=data_brief,
            scout_output=scout_output,
            skeptic_output=skeptic_output,
            savant_supplement=savant_supplement,
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
    if len(picks) < 3:
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
    candidates = extract_candidate_players(scout, target_date)
    savant = build_savant_supplement(candidates)
    picks = run_closer(data_brief, scout, skeptic, savant, model_id)
    print_results(picks, scout, skeptic)


if __name__ == "__main__":
    main()
