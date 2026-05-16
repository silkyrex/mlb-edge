"""
dive.py -- full game deep dive before picking

Pulls everything from the cache tables and formats a single readable report:
pitcher matchup, opposing lineup splits vs that pitcher's hand, regression
flags, team context, venue, IL flags, and Underdog line angles.

Usage:
    python dive.py --game "SF Giants @ Athletics"
    python dive.py --game "SF Giants @ Athletics" --date 2026-05-16
"""

import argparse
import json
import requests
import sqlite3
from datetime import date as date_cls
from pathlib import Path

PICKS_DB = Path(__file__).parent / "mlb.db"
BASE = "https://statsapi.mlb.com/api/v1"
SEASON = str(date_cls.today().year)

SEP  = "─" * 68
SEP2 = "·" * 68


def get_conn():
    conn = sqlite3.connect(PICKS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _float(val):
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(val, fmt=".3f", fallback="  --"):
    if val is None:
        return fallback
    try:
        return format(float(val), fmt)
    except (TypeError, ValueError):
        return fallback


# ── Data fetchers ────────────────────────────────────────────────────────────

def get_game_info(game_name: str, game_date: str) -> dict:
    parts = game_name.lower().split(" @ ")
    if len(parts) != 2:
        return {}
    away_kw = [w for w in parts[0].split() if len(w) > 2]
    home_kw = [w for w in parts[1].split() if len(w) > 2]
    r = requests.get(f"{BASE}/schedule", params={
        "sportId": 1, "date": game_date, "hydrate": "probablePitcher"
    }, timeout=10)
    r.raise_for_status()
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            t = g["teams"]
            if (any(w in t["away"]["team"]["name"].lower() for w in away_kw) and
                    any(w in t["home"]["team"]["name"].lower() for w in home_kw)):
                return {
                    "game_pk": g["gamePk"],
                    "away_team": t["away"]["team"]["name"],
                    "home_team": t["home"]["team"]["name"],
                    "away_id": t["away"]["team"]["id"],
                    "home_id": t["home"]["team"]["id"],
                    "away_pitcher": t["away"].get("probablePitcher", {}),
                    "home_pitcher": t["home"].get("probablePitcher", {}),
                    "venue_id": g.get("venue", {}).get("id"),
                    "game_time": g.get("gameDate", "")[11:16],
                }
    return {}


def get_pitcher_hand(player_id: int) -> str:
    r = requests.get(f"{BASE}/people/{player_id}", timeout=10)
    if r.status_code != 200:
        return "R"
    people = r.json().get("people", [{}])
    return people[0].get("pitchHand", {}).get("code", "R") if people else "R"


def utc_to_pt(utc_str: str) -> str:
    try:
        h, m = int(utc_str[:2]), int(utc_str[3:5])
        h = (h - 7) % 24
        ampm = "am" if h < 12 else "pm"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d}{ampm} PT"
    except Exception:
        return utc_str


# ── Cache queries ────────────────────────────────────────────────────────────

def get_player_stats(conn, player: str, cache_date: str) -> dict:
    row = conn.execute(
        "SELECT * FROM player_recent_stats WHERE player=? AND cache_date=?",
        (player, cache_date)
    ).fetchone()
    return dict(row) if row else {}


def get_team_stats(conn, game: str, cache_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM team_game_stats WHERE game=? AND cache_date=? ORDER BY side DESC",
        (game, cache_date)
    ).fetchall()
    return [dict(r) for r in rows]


def get_lines(conn, game: str, cache_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM mlb_game_lines WHERE game=? AND scraped_date=? ORDER BY player_type, player, stat",
        (game, cache_date)
    ).fetchall()
    return [dict(r) for r in rows]


def get_news(conn, players: list[str], cache_date: str) -> dict[str, dict]:
    result = {}
    for p in players:
        row = conn.execute(
            "SELECT * FROM player_news WHERE player=? AND news_date=?",
            (p, cache_date)
        ).fetchone()
        if row:
            result[p] = dict(row)
    return result


def get_roster_players(team_id: int, side: str) -> list[dict]:
    """Get active roster with batter/pitcher split for one team."""
    r = requests.get(f"{BASE}/teams/{team_id}/roster", params={"season": SEASON}, timeout=10)
    if r.status_code != 200:
        return []
    players = []
    for entry in r.json().get("roster", []):
        pos = entry.get("position", {}).get("type", "")
        if pos == "Pitcher":
            continue
        players.append({
            "name": entry["person"]["fullName"],
            "id": entry["person"]["id"],
            "side": side,
        })
    return players


# ── Formatters ───────────────────────────────────────────────────────────────

def pitcher_block(name: str, team: str, side: str, stats: dict, hand: str) -> list[str]:
    lines = []
    era  = _fmt(stats.get("season_era"), ".2f")
    whip = _fmt(stats.get("season_whip"), ".2f")
    k9   = _fmt(stats.get("season_k9"), ".1f")
    r_era = _fmt(stats.get("recent_era"), ".2f")
    h_era = _fmt(stats.get("split_home_era"), ".2f")
    a_era = _fmt(stats.get("split_away_era"), ".2f")
    x_woba = _fmt(stats.get("x_woba_against"), ".3f")
    ks   = stats.get("last5_ks", "[]")
    try:
        ks_list = json.loads(ks)
        ks_str = str(ks_list)
        if len(ks_list) >= 3:
            trend_val = sum(ks_list[:3]) / 3 - sum(ks_list) / len(ks_list)
            trend = "K-HOT" if trend_val > 0.5 else ("K-COLD" if trend_val < -0.5 else "FLAT")
        else:
            trend = "--"
    except Exception:
        ks_str, trend = ks, "--"

    lines.append(f"  {name} ({team}, {side.upper()})  [{hand}HP]")
    lines.append(f"  ERA {era}  WHIP {whip}  K/9 {k9}  Recent ERA {r_era}  xwOBA-against {x_woba}")
    lines.append(f"  Home ERA {h_era}  Away ERA {a_era}  Last 5 Ks {ks_str}  [{trend}]")
    return lines


def batter_row(name: str, stats: dict, lines_map: dict, hand: str, side: str, news: dict) -> str:
    """One-line batter summary for the lineup table."""
    # Pick the right split based on pitcher hand
    if hand == "L":
        split_avg = stats.get("vs_lhp_avg")
        split_ops = stats.get("vs_lhp_ops")
        split_ab  = stats.get("vs_lhp_ab") or 0
    else:
        split_avg = stats.get("vs_rhp_avg")
        split_ops = stats.get("vs_rhp_ops")
        split_ab  = stats.get("vs_rhp_ab") or 0

    # Home/away split for this game
    loc_avg = stats.get("split_home_avg") if side == "home" else stats.get("split_away_avg")

    x_avg   = stats.get("x_avg")
    act_avg = stats.get("season_avg")
    hrbi    = stats.get("last15_h_r_rbi")

    # Underdog H+R+RBI line
    hrbi_line = None
    for row in lines_map.get(name, []):
        if "H+R+RBI" in row.get("stat", "") or "H + R" in row.get("stat", ""):
            hrbi_line = row.get("line")
            break

    # Signal flags
    flags = []
    if hrbi is not None and hrbi_line is not None:
        delta = hrbi - hrbi_line
        if delta > 0.4:
            flags.append(f"HOT +{delta:.1f}/g vs line")
        elif delta < -0.4:
            flags.append(f"cold {delta:.1f}/g vs line")

    if act_avg and x_avg:
        delta_x = act_avg - x_avg
        if delta_x > 0.03:
            flags.append(f"regression risk (xAVG {x_avg:.3f})")
        elif delta_x < -0.03:
            flags.append(f"underperforming (xAVG {x_avg:.3f})")

    il_status = news.get(name, {}).get("status", "active")
    if il_status != "active":
        flags.append(f"⚠ {il_status}")

    flag_str = "  " + " | ".join(flags) if flags else ""

    hrbi_str   = _fmt(hrbi, ".2f") if hrbi is not None else "  --"
    line_str   = f"{hrbi_line:.1f}" if hrbi_line is not None else " --"
    split_str  = _fmt(split_avg, ".3f") if split_ab >= 5 else f" ({_fmt(split_avg,'.3f')} {split_ab}AB)"
    loc_str    = _fmt(loc_avg, ".3f")

    name_col = f"{name:<24}"
    return f"  {name_col} line {line_str:>4}  {side[:4]:>4} avg {loc_str}  vs{'L' if hand=='L' else 'R'} {split_str}  H+R+RBI {hrbi_str}/g{flag_str}"


def pitcher_line_angles(pitcher_name: str, stats: dict, lines: list[dict]) -> list[str]:
    """Surface interesting pitcher prop lines given their stats."""
    pitcher_lines = [l for l in lines if l.get("player") == pitcher_name and l.get("player_type") == "pitcher"]
    if not pitcher_lines:
        return []

    angles = []
    k9 = _float(stats.get("season_k9"))
    era = _float(stats.get("season_era"))
    ks_raw = stats.get("last5_ks", "[]")
    try:
        ks_list = json.loads(ks_raw)
        avg_k = sum(ks_list) / len(ks_list) if ks_list else None
    except Exception:
        avg_k = None

    for row in pitcher_lines:
        stat  = row["stat"]
        line  = row.get("line")
        hi    = row.get("higher_mult", "--")
        lo    = row.get("lower_mult", "--")

        note = ""
        if "Strikeout" in stat and line is not None and avg_k is not None:
            delta = avg_k - line
            if abs(delta) > 0.5:
                direction = "Higher" if delta > 0 else "Lower"
                note = f"  ← avg {avg_k:.1f}/start vs line {line:.1f}, lean {direction}"
        if "Earned Runs" in stat and line is not None and era is not None:
            note = f"  ← season ERA {era:.2f}"

        if note or stat in ("Strikeouts", "Earned Runs Allowed", "Pitching Outs"):
            angles.append(f"    {stat:<28} line {str(line):>5}  ↑{hi}  ↓{lo}{note}")

    return angles


# ── Main report ──────────────────────────────────────────────────────────────

def dive(game_name: str, game_date: str):
    conn = get_conn()

    # ── Game info
    info = get_game_info(game_name, game_date)
    if not info:
        print(f"Game not found: {game_name} on {game_date}")
        conn.close()
        return

    away_team    = info["away_team"]
    home_team    = info["home_team"]
    away_pitcher = info["away_pitcher"]
    home_pitcher = info["home_pitcher"]
    game_time_pt = utc_to_pt(info["game_time"])

    # ── Pitcher hands
    away_hand = get_pitcher_hand(away_pitcher["id"]) if away_pitcher.get("id") else "R"
    home_hand = get_pitcher_hand(home_pitcher["id"]) if home_pitcher.get("id") else "R"

    # ── Cache lookups
    away_p_stats = get_player_stats(conn, away_pitcher.get("fullName", ""), game_date)
    home_p_stats = get_player_stats(conn, home_pitcher.get("fullName", ""), game_date)
    team_stats   = get_team_stats(conn, game_name, game_date)
    lines        = get_lines(conn, game_name, game_date)
    lines_map: dict[str, list] = {}
    for l in lines:
        lines_map.setdefault(l["player"], []).append(l)

    # ── Venue
    venue_row = next((t for t in team_stats if t.get("venue_name")), {})
    venue_str = f"{venue_row.get('venue_name','?')}  {venue_row.get('venue_roof','?')}  " \
                f"L{venue_row.get('venue_left','?')}-C{venue_row.get('venue_center','?')}-R{venue_row.get('venue_right','?')}"

    # ── Roster batters
    away_batters = get_roster_players(info["away_id"], "away")
    home_batters = get_roster_players(info["home_id"], "home")
    all_player_names = [b["name"] for b in away_batters + home_batters]
    news = get_news(conn, all_player_names, game_date)

    conn.close()

    # ── Print ────────────────────────────────────────────────────────────────

    print(f"\n{'='*68}")
    print(f"  DEEP DIVE: {away_team} @ {home_team}  --  {game_time_pt}  --  {game_date}")
    print(f"  Venue: {venue_str}")
    print(f"{'='*68}")

    # Pitching
    print(f"\nPITCHING MATCHUP")
    print(SEP)
    if away_pitcher.get("fullName"):
        for line in pitcher_block(away_pitcher["fullName"], away_team, "away", away_p_stats, away_hand):
            print(line)
    else:
        print(f"  {away_team} (AWAY): TBD")
    print()
    if home_pitcher.get("fullName"):
        for line in pitcher_block(home_pitcher["fullName"], home_team, "home", home_p_stats, home_hand):
            print(line)
    else:
        print(f"  {home_team} (HOME): TBD")

    # Team context
    print(f"\nTEAM CONTEXT")
    print(SEP)
    for t in team_stats:
        bull = f"Bull ERA {t.get('bullpen_era','?'):.2f}  K/9 {t.get('bullpen_k9','?')}" if t.get("bullpen_era") else "Bull ERA --"
        off  = f"OPS {t.get('team_ops','?')}  K% {t.get('team_k_pct','?')}%"
        side = t.get("side","?").upper()
        print(f"  {t['team_name']:<30} [{side}]  {off}  |  {bull}")

    # Home batters vs away pitcher
    print(f"\n{home_team.upper()} BATTERS  vs  {away_pitcher.get('fullName','?')} ({away_hand}HP / AWAY)")
    print(SEP)
    print(f"  {'Name':<24} {'Line':>8}  {'Loc avg':>10}  {'vs H/L':>8}  {'H+R+RBI':>10}  Notes")
    print(SEP2)

    cache_hits = 0
    for b in home_batters:
        stats = get_conn().execute(
            "SELECT * FROM player_recent_stats WHERE player=? AND cache_date=?",
            (b["name"], game_date)
        ).fetchone()
        if not stats:
            continue
        cache_hits += 1
        conn2 = get_conn()
        stats_d = dict(stats)
        conn2.close()
        print(batter_row(b["name"], stats_d, lines_map, away_hand, "home", news))

    if cache_hits == 0:
        print(f"  (no cache for {game_date} -- run cache_stats.py first)")

    # Away batters vs home pitcher
    print(f"\n{away_team.upper()} BATTERS  vs  {home_pitcher.get('fullName','?')} ({home_hand}HP / HOME)")
    print(SEP)
    print(f"  {'Name':<24} {'Line':>8}  {'Loc avg':>10}  {'vs H/L':>8}  {'H+R+RBI':>10}  Notes")
    print(SEP2)

    cache_hits = 0
    for b in away_batters:
        stats = get_conn().execute(
            "SELECT * FROM player_recent_stats WHERE player=? AND cache_date=?",
            (b["name"], game_date)
        ).fetchone()
        if not stats:
            continue
        cache_hits += 1
        stats_d = dict(stats)
        print(batter_row(b["name"], stats_d, lines_map, home_hand, "away", news))

    if cache_hits == 0:
        print(f"  (no cache for {game_date} -- run cache_stats.py first)")

    # Pitcher prop angles
    print(f"\nPITCHER PROP ANGLES  (from Underdog lines)")
    print(SEP)
    for pitcher_name, p_stats in [
        (away_pitcher.get("fullName",""), away_p_stats),
        (home_pitcher.get("fullName",""), home_p_stats),
    ]:
        if not pitcher_name:
            continue
        team = away_team if pitcher_name == away_pitcher.get("fullName") else home_team
        print(f"  {pitcher_name} ({team})")
        angles = pitcher_line_angles(pitcher_name, p_stats, lines)
        if angles:
            for a in angles:
                print(a)
        else:
            print("    (no lines scraped yet -- run /underdog-mlb first)")

    # IL flags
    flagged_news = {p: n for p, n in news.items() if n.get("status") not in ("active", None)}
    if flagged_news:
        print(f"\nIL FLAGS")
        print(SEP)
        for player, n in sorted(flagged_news.items()):
            print(f"  [{n['status']}] {player}  --  {n.get('note','')[:80]}")

    print(f"\n{'='*68}")
    print(f"  Tip: run `python player.py matchup <batter> <pitcher>` for H2H history")
    print(f"       run `python player.py pitcher <name>` for full start-by-start log")
    print(f"{'='*68}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True, help="Game name e.g. 'SF Giants @ Athletics'")
    parser.add_argument("--date", default=str(date_cls.today()))
    args = parser.parse_args()
    dive(args.game, args.date)


if __name__ == "__main__":
    main()
