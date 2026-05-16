# mlb-edge

MLB sports betting data pipeline. Collects game results and player stats, builds a local database, and (eventually) runs analysis against betting lines.

## Phases

- **Phase 1 (done):** Ingest game + player data from MLB Stats API into SQLite
- **Phase 2:** Historical backfill, data quality checks
- **Phase 3 (active):** Line comparison -- Underdog pick'em lines scraped to `mlb_game_lines` via `/underdog-mlb` Claude skill; analyzed via `/underdog-mlb-analyze` with lean context + external research + scoring

## Quickstart

```bash
pip install -r requirements.txt
python db.py            # initialize schema
python fetch.py date 2026-05-15
python fetch.py range 2026-05-01 2026-05-15
python fetch.py date 2026-05-15 --team Yankees
```

## Schema

Four tables:

- `games` -- one row per game (scores, teams, venue)
- `players` -- player registry (name, position, team)
- `player_game_logs` -- per-game hitting and pitching stats, keyed on `game_pk + player_id + stat_type`
- `mlb_game_lines` -- Underdog pick'em lines (pitcher + batter + team), scraped daily per game

See `schema.sql` for full definitions.

## Phase 3 Workflow

```
12:00 PT  Discord lean signal arrives (OVER/UNDER lean + pitcher/offense grades)
          ↓
          /playwright-underdog   (login to Underdog)
          ↓
          /underdog-mlb [game]   (scrape all 23 stat tabs → mlb_game_lines)
          ↓
          /underdog-mlb-analyze [game] [lean]  (score + rank + research → pick list)
          ↓
          /bet-score MLB [slip]  (gate top picks before placing)
```

Query lines directly:
```bash
python lines_query.py --list-games
python lines_query.py --game "SF Giants @ Athletics" --type pitcher
python lines_query.py --game "SF Giants @ Athletics" --stat "Strikeouts"
```

## Data Sources

See `DATASOURCES.md` for MLB Stats API docs, Baseball Reference notes, and paid service options.
