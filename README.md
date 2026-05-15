# mlb-edge

MLB sports betting data pipeline. Collects game results and player stats, builds a local database, and (eventually) runs analysis against betting lines.

## Phases

- **Phase 1 (now):** Ingest game + player data from MLB Stats API into SQLite
- **Phase 2:** Historical backfill, data quality checks
- **Phase 3:** Line comparison -- run DB against props/totals

## Quickstart

```bash
pip install -r requirements.txt
python db.py            # initialize schema
python fetch.py date 2026-05-15
python fetch.py range 2026-05-01 2026-05-15
python fetch.py date 2026-05-15 --team Yankees
```

## Schema

Three tables:

- `games` -- one row per game (scores, teams, venue)
- `players` -- player registry (name, position, team)
- `player_game_logs` -- per-game hitting and pitching stats, keyed on `game_pk + player_id + stat_type`

See `schema.sql` for full definitions.

## Data Sources

See `DATASOURCES.md` for MLB Stats API docs, Baseball Reference notes, and paid service options.
