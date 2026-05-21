"""
rank.py -- daily player tiering (ELITE / MID / FADE)

Reads mlb_game_lines + player_recent_stats + player_news from mlb.db.
Uses mlb_game_lines as the starter/active-batter filter (Underdog already
confirmed these players are in tonight's lineups).

Usage:
    python rank.py                         # today's scraped games
    python rank.py --date 2026-05-20       # specific date
    python rank.py --game "ATH @ LAA"      # filter to one game
    python rank.py --json                  # machine-readable output (used by mismatch.py)
"""

import argparse
import json
import sqlite3
from datetime import date as date_cls, datetime
from pathlib import Path

DB = Path(__file__).parent / "mlb.db"
NEWS_DB = Path(__file__).parent / "sliplog.db"  # player_news lives in mlb.db too

# ── Tier thresholds ────────────────────────────────────────────────────────────

# Pitcher -- FIP primary, ERA secondary
P_ELITE_FIP = 3.50
P_ELITE_ERA = 3.00
P_ELITE_K9  = 9.0
P_FADE_FIP  = 4.50
P_FADE_ERA  = 4.50
P_FADE_K9   = 7.0

# Batter -- L15 H+R+RBI primary, season_avg floor
B_ELITE_L15   = 2.0    # ~top 20% of cache
B_ELITE_AVG   = 0.275  # floor: avoids hot streak on weak hitter
B_ELITE_SPLIT = 0.310  # handedness split threshold
B_FADE_L15    = 1.0
B_FADE_AVG    = 0.220
B_FADE_SPLIT  = 0.220

# Team lineup
T_ELITE_AVG   = 0.265
T_ELITE_OPS   = 0.780
T_ELITE_K_PCT = 18.0
T_FADE_AVG    = 0.235
T_FADE_OPS    = 0.700
T_FADE_K_PCT  = 24.0


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_float(val, default=None):
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def tier_pitcher(stats: dict, il_days: int | None) -> dict:
    """Return tier dict for a pitcher row from player_recent_stats."""
    fip = _safe_float(stats.get("espn_fip"))
    era = _safe_float(stats.get("season_era"))
    k9  = _safe_float(stats.get("season_k9"))
    home_era = _safe_float(stats.get("split_home_era"))
    away_era = _safe_float(stats.get("split_away_era"))
    role = stats.get("pitcher_role", "starter")

    flags = []

    # IL return flag
    if il_days is not None and il_days <= 7:
        flags.append(f"il_return_{il_days}d")

    # Role mismatch (reliever listed as starter tonight)
    if role == "reliever":
        flags.append("role_mismatch")

    # Short hook flag: last 3 starts avg IP ≤ 5.0
    last5_ip = json.loads(stats.get("last5_ip") or "[]")
    if len(last5_ip) >= 3:
        avg_ip = sum(last5_ip[-3:]) / 3
        if avg_ip <= 5.0:
            flags.append(f"hook_risk_avg{avg_ip:.1f}ip")

    # Tier logic: FIP primary, ERA fallback
    primary = fip if fip is not None else era
    secondary = era  # used as cross-check

    # Structural FADE regardless of ERA (IL return or role mismatch)
    if "il_return" in " ".join(flags) or "role_mismatch" in flags:
        tier = "FADE"
    elif primary is not None:
        if primary <= P_ELITE_FIP and (k9 is None or k9 >= P_ELITE_K9):
            tier = "ELITE"
        elif primary >= P_FADE_FIP:
            tier = "FADE"
        else:
            tier = "MID"
    elif era is not None:
        if era <= P_ELITE_ERA:
            tier = "ELITE"
        elif era >= P_FADE_ERA:
            tier = "FADE"
        else:
            tier = "MID"
    else:
        tier = "MID"  # no data

    # Additional FADE flag: hook risk alone doesn't change quality tier but flags PO LOWER
    fade_po_lower = "hook_risk" in " ".join(flags) or tier == "FADE"

    return {
        "tier": tier,
        "flags": flags,
        "fade_po_lower": fade_po_lower,
        "fip": fip,
        "era": era,
        "k9": k9,
        "home_era": home_era,
        "away_era": away_era,
        "role": role,
        "last5_ip": last5_ip,
    }


def tier_batter(stats: dict, pitcher_throws: str | None = None) -> dict:
    """Return tier dict for a batter row from player_recent_stats.
    pitcher_throws: 'L' or 'R' — if known, use handedness-specific split.
    """
    l15 = _safe_float(stats.get("last15_h_r_rbi"))
    s_avg = _safe_float(stats.get("season_avg"))
    x_avg = _safe_float(stats.get("x_avg"))
    vs_lhp = _safe_float(stats.get("vs_lhp_avg"))
    vs_rhp = _safe_float(stats.get("vs_rhp_avg"))
    ops = _safe_float(stats.get("season_ops"))

    # Handedness-specific split (most important for H+R+RBI direction)
    if pitcher_throws == "L" and vs_lhp is not None:
        h_split = vs_lhp
    elif pitcher_throws == "R" and vs_rhp is not None:
        h_split = vs_rhp
    else:
        h_split = None  # unknown handedness, show both

    # Regression flag: season_avg much higher than xAVG = lucky, regression risk
    regression_risk = (
        s_avg is not None and x_avg is not None and (s_avg - x_avg) > 0.040
    )

    flags = []
    if regression_risk:
        flags.append("regression_risk")

    # Tier: L15 primary, season_avg floor
    if l15 is not None and l15 >= B_ELITE_L15:
        if s_avg is not None and s_avg >= B_ELITE_AVG:
            tier = "ELITE"
        elif s_avg is None:
            tier = "ELITE"  # no season data, trust L15
        else:
            tier = "MID"  # hot streak on weak hitter
            flags.append("hot_streak_caution")
    elif l15 is not None and l15 <= B_FADE_L15:
        if s_avg is None or s_avg <= B_FADE_AVG:
            tier = "FADE"
        else:
            tier = "MID"  # season ok, just cold recently
    else:
        tier = "MID"

    return {
        "tier": tier,
        "flags": flags,
        "regression_risk": regression_risk,
        "l15_h_r_rbi": l15,
        "season_avg": s_avg,
        "x_avg": x_avg,
        "vs_lhp": vs_lhp,
        "vs_rhp": vs_rhp,
        "h_split": h_split,
        "ops": ops,
    }


def tier_lineup(team_stats: dict) -> dict:
    """Return tier dict for a team lineup from team_game_stats."""
    avg = _safe_float(team_stats.get("team_avg"))
    ops = _safe_float(team_stats.get("team_ops"))
    k_pct = _safe_float(team_stats.get("team_k_pct"))

    if avg is not None and avg >= T_ELITE_AVG and ops is not None and ops >= T_ELITE_OPS:
        tier = "ELITE"
    elif avg is not None and avg <= T_FADE_AVG:
        tier = "FADE"
    elif ops is not None and ops <= T_FADE_OPS:
        tier = "FADE"
    else:
        tier = "MID"

    return {
        "tier": tier,
        "avg": avg,
        "ops": ops,
        "k_pct": k_pct,
    }


def load_tiers(query_date: str, game_filter: str | None = None) -> dict:
    """
    Load and compute tiers for all players in mlb_game_lines for query_date.
    Returns {
        "pitchers": {player_name: {tier_data, game}},
        "batters":  {player_name: {tier_data, game}},
        "lineups":  {team_name: tier_data},
        "games":    [game_str, ...],
    }
    """
    conn = connect()

    # Get all players in scraped lines for this date
    q = """
        SELECT DISTINCT player, player_type, game, team
        FROM mlb_game_lines
        WHERE scraped_date = ?
    """
    params = [query_date]
    if game_filter:
        q += " AND LOWER(game) LIKE ?"
        params.append(f"%{game_filter.lower()}%")
    lines_rows = conn.execute(q, params).fetchall()

    games = sorted({r["game"] for r in lines_rows})

    # Batch load player stats for all players in cache
    players_in_game = [r["player"] for r in lines_rows if r["player_type"] in ("pitcher", "batter")]
    if not players_in_game:
        conn.close()
        return {"pitchers": {}, "batters": {}, "lineups": {}, "games": games}

    placeholders = ",".join("?" * len(players_in_game))
    stats_rows = conn.execute(
        f"""
        SELECT * FROM player_recent_stats
        WHERE player IN ({placeholders})
          AND cache_date = (
              SELECT MAX(cache_date) FROM player_recent_stats p2
              WHERE p2.player = player_recent_stats.player
          )
        """,
        players_in_game,
    ).fetchall()
    stats_map = {r["player"]: dict(r) for r in stats_rows}

    # IL status from player_news
    il_rows = conn.execute(
        f"""
        SELECT player, status, note, news_date FROM player_news
        WHERE player IN ({placeholders})
          AND news_date >= date('now', '-14 days')
        ORDER BY news_date DESC
        """,
        players_in_game,
    ).fetchall()
    # Keep most recent IL row per player
    il_map: dict[str, dict] = {}
    for r in il_rows:
        if r["player"] not in il_map:
            il_map[r["player"]] = dict(r)

    # Team stats for lineup tier
    teams_in_game = {r["team"] for r in lines_rows if r["team"] and r["player_type"] == "batter"}
    team_stats_rows = conn.execute(
        """
        SELECT * FROM team_game_stats
        WHERE cache_date = ? AND team_name IN ({})
        """.format(",".join("?" * len(teams_in_game))),
        [query_date] + list(teams_in_game),
    ).fetchall() if teams_in_game else []
    team_map = {r["team_name"]: dict(r) for r in team_stats_rows}

    conn.close()

    # Build game->pitcher map for handedness lookup (not in DB yet, so None)
    game_pitchers: dict[str, list[str]] = {}
    for r in lines_rows:
        if r["player_type"] == "pitcher":
            game_pitchers.setdefault(r["game"], []).append(r["player"])

    pitchers: dict[str, dict] = {}
    batters: dict[str, dict] = {}
    lineups: dict[str, dict] = {}

    for r in lines_rows:
        name = r["player"]
        pt = r["player_type"]
        game = r["game"]

        if pt == "pitcher":
            if name in pitchers:
                continue
            s = stats_map.get(name, {})
            il = il_map.get(name)
            il_days = None
            if il and il["status"] and "IL-return" in il["status"]:
                # parse days: IL-return-today=0, IL-return-Nd=N
                status = il["status"]
                if "today" in status:
                    il_days = 0
                else:
                    try:
                        il_days = int(status.split("-")[-1].replace("d", ""))
                    except (ValueError, IndexError):
                        il_days = 3
            elif il and il["status"] and il["status"].startswith("IL-"):
                il_days = None  # still on IL, not returning
            t = tier_pitcher(s, il_days)
            t["game"] = game
            t["player"] = name
            pitchers[name] = t

        elif pt == "batter":
            if name in batters:
                continue
            s = stats_map.get(name, {})
            t = tier_batter(s, pitcher_throws=None)  # handedness TBD
            t["game"] = game
            t["player"] = name
            t["team"] = r["team"]
            batters[name] = t

    # Lineup tiers from team_game_stats
    for team_name, ts in team_map.items():
        lineups[team_name] = tier_lineup(ts)
        lineups[team_name]["team"] = team_name

    return {
        "pitchers": pitchers,
        "batters": batters,
        "lineups": lineups,
        "games": games,
        "date": query_date,
    }


def _tier_badge(tier: str) -> str:
    return {"ELITE": "⭐ ELITE", "MID": "   MID ", "FADE": "🔴 FADE"}.get(tier, tier)


def print_tiers(tiers: dict) -> None:
    games = tiers["games"]
    pitchers = tiers["pitchers"]
    batters = tiers["batters"]
    lineups = tiers["lineups"]
    query_date = tiers.get("date", "")

    print(f"\n{'─'*60}")
    print(f"  PLAYER TIERS — {query_date}  ({len(games)} game(s))")
    print(f"{'─'*60}")

    for game in games:
        print(f"\n  🎮 {game}")

        # Pitchers in this game
        game_pitchers = [p for p in pitchers.values() if p["game"] == game]
        for p in sorted(game_pitchers, key=lambda x: (x["tier"] != "FADE", x["tier"] != "ELITE")):
            badge = _tier_badge(p["tier"])
            era_str = f"ERA {p['era']:.2f}" if p["era"] else "ERA --"
            fip_str = f"FIP {p['fip']:.2f}" if p["fip"] else "FIP --"
            k9_str  = f"K9 {p['k9']:.1f}" if p["k9"] else "K9 --"
            flags_str = "  [" + ", ".join(p["flags"]) + "]" if p["flags"] else ""
            home_away = ""
            if p["home_era"] and p["away_era"]:
                home_away = f"  home {p['home_era']:.2f}/away {p['away_era']:.2f}"
            print(f"    {badge}  P  {p['player']:<28} {era_str}  {fip_str}  {k9_str}{home_away}{flags_str}")

        # Batters in this game
        game_batters = [b for b in batters.values() if b["game"] == game]
        for b in sorted(game_batters, key=lambda x: (x["tier"] != "ELITE", x["tier"] != "FADE",
                                                       -(x["l15_h_r_rbi"] or 0))):
            badge = _tier_badge(b["tier"])
            l15_str = f"L15 {b['l15_h_r_rbi']:.2f}" if b["l15_h_r_rbi"] else "L15 --"
            avg_str = f"avg {b['season_avg']:.3f}" if b["season_avg"] else "avg ---"
            lhp_str = f"vsL {b['vs_lhp']:.3f}" if b["vs_lhp"] else "vsL ---"
            rhp_str = f"vsR {b['vs_rhp']:.3f}" if b["vs_rhp"] else "vsR ---"
            flags_str = "  [" + ", ".join(b["flags"]) + "]" if b["flags"] else ""
            print(f"    {badge}  B  {b['player']:<28} {l15_str}  {avg_str}  {lhp_str}  {rhp_str}{flags_str}")

        # Lineup tier
        game_teams = [l for l in lineups.values() if any(
            b["team"] == l["team"] and b["game"] == game for b in batters.values()
        )]
        for lt in game_teams:
            badge = _tier_badge(lt["tier"])
            avg_s = f"avg {lt['avg']:.3f}" if lt["avg"] else "avg ---"
            ops_s = f"ops {lt['ops']:.3f}" if lt["ops"] else "ops ---"
            k_s   = f"k% {lt['k_pct']:.1f}" if lt["k_pct"] else "k% --"
            print(f"    {badge}  T  {lt['team']:<28} {avg_s}  {ops_s}  {k_s}")

    # Summary counts
    e_p = sum(1 for p in pitchers.values() if p["tier"] == "ELITE")
    f_p = sum(1 for p in pitchers.values() if p["tier"] == "FADE")
    e_b = sum(1 for b in batters.values() if b["tier"] == "ELITE")
    f_b = sum(1 for b in batters.values() if b["tier"] == "FADE")
    print(f"\n  Summary: {e_p} ELITE pitchers, {f_p} FADE pitchers | "
          f"{e_b} ELITE batters, {f_b} FADE batters")


def main():
    parser = argparse.ArgumentParser(description="Daily player tier report")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--game", default=None, help="Filter to one game (substring match)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args()

    query_date = args.date or date_cls.today().isoformat()
    tiers = load_tiers(query_date, args.game)

    if args.json:
        print(json.dumps(tiers, indent=2, default=str))
    else:
        print_tiers(tiers)


if __name__ == "__main__":
    main()
