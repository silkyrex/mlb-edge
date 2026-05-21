"""
mismatch.py -- ELITE vs FADE mismatch finder

Reads tiers from rank.py, finds mismatches in today's games, writes to
mismatch_candidates in mlb.db, and outputs ranked bet candidates for closer.py.

Usage:
    python mismatch.py                     # today
    python mismatch.py --date 2026-05-20
    python mismatch.py --game "ATH @ LAA"  # filter to one game
    python mismatch.py --json              # machine-readable (for piping to closer)
"""

import argparse
import json
import sqlite3
from datetime import date as date_cls
from pathlib import Path

from rank import load_tiers

DB = Path(__file__).parent / "mlb.db"

# ── Edge key taxonomy ──────────────────────────────────────────────────────────
# Maps to mismatch matrix priority ranking.
EDGE_KEYS = {
    1: "elite_batter_fade_pitcher__hrbi_higher",
    2: "fade_pitcher_po_lower",
    3: "elite_batter_hits_higher",
    4: "elite_pitcher_fade_lineup__bk_higher",
    5: "elite_power_fade_pitcher__tb_higher",
}

EDGE_LABELS = {
    "elite_batter_fade_pitcher__hrbi_higher": ("Hits + Runs + RBIs", "Higher", "⭐⭐⭐ CONFIRMED"),
    "fade_pitcher_po_lower":                  ("Pitching Outs",       "Lower",  "⭐⭐  LOGICAL"),
    "elite_batter_hits_higher":               ("Hits",                "Higher", "⭐⭐  LOGICAL"),
    "elite_pitcher_fade_lineup__bk_higher":   ("Batter Strikeouts",   "Higher", "⭐   UNTESTED"),
    "elite_power_fade_pitcher__tb_higher":    ("Total Bases",         "Higher", "⭐   UNTESTED"),
}


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _game_to_teams(game: str) -> tuple[str, str]:
    """'SF Giants @ Athletics' -> ('SF Giants', 'Athletics')"""
    if " @ " in game:
        away, home = game.split(" @ ", 1)
        return away.strip(), home.strip()
    return game, game


def find_mismatches(tiers: dict) -> list[dict]:
    """
    Apply mismatch matrix to tier data. Returns list of candidate dicts,
    sorted by rank then confidence.
    """
    candidates = []
    pitchers = tiers["pitchers"]
    batters = tiers["batters"]
    lineups = tiers["lineups"]
    query_date = tiers.get("date", date_cls.today().isoformat())

    # Build game -> pitchers map
    game_pitchers: dict[str, list[dict]] = {}
    for p in pitchers.values():
        game_pitchers.setdefault(p["game"], []).append(p)

    # Build game -> batters map
    game_batters: dict[str, list[dict]] = {}
    for b in batters.values():
        game_batters.setdefault(b["game"], []).append(b)

    for game in tiers["games"]:
        g_pitchers = game_pitchers.get(game, [])
        g_batters = game_batters.get(game, [])
        away_team, home_team = _game_to_teams(game)

        for pitcher in g_pitchers:
            # Determine which batters face this pitcher
            # Away pitcher faces home batters; home pitcher faces away batters.
            # Use team name matching: if pitcher's team ~ home_team, opposing batters are away
            p_team = (pitcher.get("team") or "").lower()
            home_lower = home_team.lower()
            away_lower = away_team.lower()
            if any(tok in p_team for tok in home_lower.split()) or p_team in home_lower:
                facing_batters = [b for b in g_batters if b.get("team", "").lower() in away_lower
                                  or any(tok in away_lower for tok in (b.get("team") or "").lower().split())]
            else:
                facing_batters = [b for b in g_batters if b.get("team", "").lower() in home_lower
                                  or any(tok in home_lower for tok in (b.get("team") or "").lower().split())]

            if not facing_batters:
                facing_batters = g_batters  # fallback: all batters in game

            # ── Rank 1: ELITE batter vs FADE pitcher -> H+R+RBI HIGHER ──────
            if pitcher["tier"] == "FADE":
                for batter in facing_batters:
                    if batter["tier"] == "ELITE":
                        tier_grades = {
                            "batter_tier": "ELITE",
                            "pitcher_tier": "FADE",
                            "batter_l15": batter["l15_h_r_rbi"],
                            "batter_avg": batter["season_avg"],
                            "pitcher_era": pitcher["era"],
                            "pitcher_fip": pitcher["fip"],
                            "pitcher_flags": pitcher["flags"],
                        }
                        candidates.append({
                            "date": query_date,
                            "game": game,
                            "player": batter["player"],
                            "player_type": "batter",
                            "edge_key": "elite_batter_fade_pitcher__hrbi_higher",
                            "bet_category": "Hits + Runs + RBIs",
                            "bet_direction": "Higher",
                            "rank": 1,
                            "tier_grades": json.dumps(tier_grades),
                            "_batter": batter,
                            "_pitcher": pitcher,
                        })
                        # Rank 3 co-candidate: Hits Higher (same mismatch)
                        candidates.append({
                            "date": query_date,
                            "game": game,
                            "player": batter["player"],
                            "player_type": "batter",
                            "edge_key": "elite_batter_hits_higher",
                            "bet_category": "Hits",
                            "bet_direction": "Higher",
                            "rank": 3,
                            "tier_grades": json.dumps(tier_grades),
                            "_batter": batter,
                            "_pitcher": pitcher,
                        })

            # ── Rank 2: FADE pitcher -> PO LOWER (hook risk) ─────────────────
            if pitcher["fade_po_lower"]:
                tier_grades = {
                    "pitcher_tier": pitcher["tier"],
                    "pitcher_era": pitcher["era"],
                    "pitcher_fip": pitcher["fip"],
                    "pitcher_home_era": pitcher["home_era"],
                    "pitcher_away_era": pitcher["away_era"],
                    "pitcher_flags": pitcher["flags"],
                    "last5_ip": pitcher["last5_ip"],
                }
                candidates.append({
                    "date": query_date,
                    "game": game,
                    "player": pitcher["player"],
                    "player_type": "pitcher",
                    "edge_key": "fade_pitcher_po_lower",
                    "bet_category": "Pitching Outs",
                    "bet_direction": "Lower",
                    "rank": 2,
                    "tier_grades": json.dumps(tier_grades),
                    "_pitcher": pitcher,
                })

            # ── Rank 4: ELITE pitcher vs FADE lineup -> Batter Ks HIGHER ─────
            if pitcher["tier"] == "ELITE" and pitcher["k9"] and pitcher["k9"] >= 9.5:
                # Find opposing lineup tier
                p_game_team = pitcher.get("team", "")
                opp_team_name = home_team if away_lower in (p_game_team or "").lower() else away_team
                opp_lineup = lineups.get(opp_team_name)
                if opp_lineup and opp_lineup["tier"] == "FADE":
                    # High-K pitcher vs fade lineup: bet Batter Ks HIGHER on FADE batters
                    for batter in facing_batters:
                        if batter["tier"] == "FADE":
                            tier_grades = {
                                "pitcher_tier": "ELITE",
                                "batter_tier": "FADE",
                                "pitcher_k9": pitcher["k9"],
                                "lineup_tier": "FADE",
                                "lineup_k_pct": opp_lineup.get("k_pct"),
                            }
                            candidates.append({
                                "date": query_date,
                                "game": game,
                                "player": batter["player"],
                                "player_type": "batter",
                                "edge_key": "elite_pitcher_fade_lineup__bk_higher",
                                "bet_category": "Batter Strikeouts",
                                "bet_direction": "Higher",
                                "rank": 4,
                                "tier_grades": json.dumps(tier_grades),
                                "_batter": batter,
                                "_pitcher": pitcher,
                            })

    # Deduplicate: keep highest-rank (lowest rank number) per player+edge_key
    seen: dict[str, dict] = {}
    for c in candidates:
        key = f"{c['player']}|{c['edge_key']}"
        if key not in seen or c["rank"] < seen[key]["rank"]:
            seen[key] = c

    return sorted(seen.values(), key=lambda x: (x["rank"], x["player"]))


def write_candidates(candidates: list[dict]) -> int:
    conn = connect()
    written = 0
    for c in candidates:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO mismatch_candidates
                    (date, game, player, player_type, edge_key, bet_category, bet_direction, rank, tier_grades)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (c["date"], c["game"], c["player"], c["player_type"],
                 c["edge_key"], c["bet_category"], c["bet_direction"],
                 c["rank"], c["tier_grades"]),
            )
            written += 1
        except sqlite3.Error as e:
            print(f"  [warn] DB write failed for {c['player']}: {e}")
    conn.commit()
    conn.close()
    return written


def print_candidates(candidates: list[dict]) -> None:
    if not candidates:
        print("\n  No mismatches found. All games are MID vs MID.")
        return

    current_rank = None
    for c in candidates:
        if c["rank"] != current_rank:
            current_rank = c["rank"]
            label = EDGE_LABELS.get(c["edge_key"], ("?", "?", ""))
            print(f"\n  {'─'*56}")
            print(f"  RANK {c['rank']} — {c['edge_key'].upper()}")
            print(f"           {label[2]}")
            print(f"  {'─'*56}")

        grades = json.loads(c["tier_grades"])
        print(f"\n  🎮 {c['game']}")
        print(f"     Player:  {c['player']} ({c['player_type']})")
        print(f"     Bet:     {c['bet_category']} {c['bet_direction'].upper()}")

        if c["player_type"] == "batter":
            b = c.get("_batter", {})
            p = c.get("_pitcher", {})
            l15 = grades.get("batter_l15")
            avg = grades.get("batter_avg")
            era = grades.get("pitcher_era")
            fip = grades.get("pitcher_fip")
            flags = grades.get("pitcher_flags", [])
            l15_s = f"L15 {l15:.2f}" if l15 else "L15 --"
            avg_s = f"avg {avg:.3f}" if avg else "avg ---"
            era_s = f"ERA {era:.2f}" if era else "ERA --"
            fip_s = f"FIP {fip:.2f}" if fip else "FIP --"
            flag_s = "  [" + ", ".join(flags) + "]" if flags else ""
            print(f"     Batter:  ⭐ ELITE  {l15_s}  {avg_s}")
            if b.get("vs_lhp") and b.get("vs_rhp"):
                print(f"              vsL {b['vs_lhp']:.3f}  vsR {b['vs_rhp']:.3f}")
            print(f"     Pitcher: 🔴 FADE   {era_s}  {fip_s}{flag_s}")
        else:
            p = c.get("_pitcher", {})
            era = grades.get("pitcher_era")
            fip = grades.get("pitcher_fip")
            home_era = grades.get("pitcher_home_era")
            away_era = grades.get("pitcher_away_era")
            flags = grades.get("pitcher_flags", [])
            l5_ip = grades.get("last5_ip", [])
            era_s = f"ERA {era:.2f}" if era else "ERA --"
            fip_s = f"FIP {fip:.2f}" if fip else "FIP --"
            flag_s = "  [" + ", ".join(flags) + "]" if flags else ""
            ha_s = f"  home {home_era:.2f}/away {away_era:.2f}" if home_era and away_era else ""
            ip_s = f"  L5 IP: {l5_ip}" if l5_ip else ""
            print(f"     Pitcher: 🔴 FADE   {era_s}  {fip_s}{ha_s}{flag_s}{ip_s}")

        edge_key = c["edge_key"]
        print(f"\n     → edge_key: \"{edge_key}\"")
        print(f"     → sliplog.py add ... --edge-key \"{edge_key}\"")

    print(f"\n  {'─'*56}")
    print(f"  {len(candidates)} candidate(s) written to mismatch_candidates.")
    print(f"  Run: python closer.py   (Scout reads mismatch_candidates as input)")


def main():
    parser = argparse.ArgumentParser(description="Find ELITE vs FADE mismatches")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--game", default=None, help="Filter to one game (substring)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing to DB")
    args = parser.parse_args()

    query_date = args.date or date_cls.today().isoformat()
    tiers = load_tiers(query_date, args.game)

    if not tiers["games"]:
        print(f"No games found in mlb_game_lines for {query_date}.")
        return

    candidates = find_mismatches(tiers)

    if args.json:
        # Strip internal _batter/_pitcher refs (not serializable cleanly)
        clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates]
        print(json.dumps(clean, indent=2))
        return

    print(f"\nMISMATCH REPORT — {query_date}  ({len(tiers['games'])} game(s))")
    print_candidates(candidates)

    if not args.dry_run:
        clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates]
        n = write_candidates(clean)
        print(f"  Saved {n} row(s) to mismatch_candidates in mlb.db.")


if __name__ == "__main__":
    main()
