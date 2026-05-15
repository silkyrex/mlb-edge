CREATE TABLE IF NOT EXISTS games (
    game_pk     INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    home_score  INTEGER,
    away_score  INTEGER,
    status      TEXT,
    venue       TEXT,
    inserted_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS players (
    player_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    position    TEXT,
    team        TEXT,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS player_game_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk     INTEGER NOT NULL REFERENCES games(game_pk),
    player_id   INTEGER NOT NULL REFERENCES players(player_id),
    team        TEXT,
    stat_type   TEXT NOT NULL,  -- 'hitting' or 'pitching'
    ab          INTEGER,
    hits        INTEGER,
    rbi         INTEGER,
    hr          INTEGER,
    bb          INTEGER,
    so          INTEGER,
    avg         REAL,
    ops         REAL,
    ip          REAL,
    er          INTEGER,
    k           INTEGER,
    era         REAL,
    inserted_at TEXT DEFAULT (datetime('now')),
    UNIQUE(game_pk, player_id, stat_type)
);

CREATE INDEX IF NOT EXISTS idx_logs_game   ON player_game_logs(game_pk);
CREATE INDEX IF NOT EXISTS idx_logs_player ON player_game_logs(player_id);
CREATE INDEX IF NOT EXISTS idx_games_date  ON games(date);
