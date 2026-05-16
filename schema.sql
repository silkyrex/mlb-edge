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

-- Phase 3: Underdog pick'em lines scraped via Playwright MCP
-- Populated by /underdog-mlb Claude skill; analyzed by /underdog-mlb-analyze
CREATE TABLE IF NOT EXISTS mlb_game_lines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_date  TEXT NOT NULL,
    scraped_at    TEXT NOT NULL,
    game          TEXT NOT NULL,       -- e.g. "SF Giants @ Athletics"
    game_time     TEXT,                -- e.g. "6:40 pm"
    player        TEXT NOT NULL,       -- player name or "TEAM" for game-level picks
    team          TEXT,                -- team abbreviation
    player_type   TEXT,                -- "pitcher" | "batter" | "team"
    stat          TEXT NOT NULL,       -- e.g. "Strikeouts", "Hits + Runs + RBIs"
    line          REAL,                -- numeric line value (NULL for moneylines)
    higher_mult   TEXT,                -- e.g. "1.03x" or odds string like "+108"
    lower_mult    TEXT,                -- e.g. "0.82x" or odds string like "-134"
    UNIQUE(scraped_date, game, player, stat)
);

CREATE INDEX IF NOT EXISTS idx_lines_date  ON mlb_game_lines(scraped_date);
CREATE INDEX IF NOT EXISTS idx_lines_game  ON mlb_game_lines(game, scraped_date);
CREATE INDEX IF NOT EXISTS idx_lines_player ON mlb_game_lines(player, scraped_date);
