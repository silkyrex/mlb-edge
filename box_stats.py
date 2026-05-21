"""
box_stats.py -- box score stat extraction and pick_lessons observation

Converts raw MLB Stats API box score dicts into human-readable lines and
numeric actuals for pick_lessons tracking.
"""

import re
from pick_lessons import observe as _pl_observe


def extract_player_stats(bet_on: str, box: dict) -> list[str]:
    """Parse a pick string for player names and return formatted stat lines."""
    lines = []
    picks = re.split(r"\s*\+\s*|\n", bet_on)

    for pick in picks:
        pick = pick.strip()
        if not pick:
            continue

        matched_name = None
        matched_stats = None
        for name, stats in box.items():
            last = name.split()[-1]
            first = name.split()[0]
            if last.lower() in pick.lower() or first.lower() in pick.lower():
                matched_name = name
                matched_stats = stats
                break

        if not matched_name:
            lines.append(f"  {pick}  ->  player not found in box score")
            continue

        bat = matched_stats["batting"]
        pit = matched_stats["pitching"]
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
            if pit.get("inningsPitched"):
                stat_lines.append(f"IP={pit['inningsPitched']} ER={pit.get('earnedRuns','?')} K={pit.get('strikeOuts','?')}")
            else:
                stat_lines.append(f"AB={bat.get('atBats','?')} H={bat.get('hits','?')} K={bat.get('strikeOuts','?')}")

        lines.append(f"  {pick}")
        lines.append(f"    {matched_name}: {' | '.join(stat_lines)}")

    return lines


def get_actual_from_box(matched_name: str, stat_key: str, box: dict) -> float | None:
    """Return the numeric actual for a stat from the box score."""
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
    """Fire pick_lessons.observe for each structured pick using box score data."""
    if not bet["picks"]:
        print("  [pick_lessons] no per-pick structure -- skipped")
        return

    for pick in bet["picks"]:
        player = pick["player"]
        stat_key = pick["stat"]
        line = float(pick["line"])
        side = pick["side"]
        player_type = pick.get("player_type", "pitcher")
        game = pick.get("game", "")

        name_hint = player.split()[-1].lower()
        matched_name = next((n for n in box if name_hint in n.lower()), None)
        if not matched_name:
            print(f"  [pick_lessons] no box match for {player} -- skipped")
            continue

        actual = get_actual_from_box(matched_name, stat_key, box)
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
