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

-- Phase 3: Pre-cached player recent stats from MLB Stats API
-- Populated by cache_stats.py; read by /underdog-mlb-analyze skill
CREATE TABLE IF NOT EXISTS player_recent_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    player              TEXT NOT NULL,
    player_type         TEXT NOT NULL,      -- 'pitcher' or 'batter'
    cache_date          TEXT NOT NULL,      -- YYYY-MM-DD
    mlb_player_id       INTEGER,
    games_lookback      INTEGER,
    -- Pitcher: last 5 starts
    last5_ks            TEXT,               -- JSON array e.g. [5,4,6,3,7]
    season_k9           REAL,
    season_era          REAL,
    season_whip         REAL,
    recent_era          REAL,               -- ERA over last 5 starts
    -- Batter: last 15 games
    last15_h_r_rbi      REAL,               -- avg H+R+RBI per game
    last15_hits         REAL,
    last15_ks_batter    REAL,               -- avg batter Ks per game
    last15_hr           REAL,
    last15_tb           REAL,
    season_avg          REAL,
    season_ops          REAL,
    UNIQUE(player, cache_date)
);

CREATE INDEX IF NOT EXISTS idx_prs_player ON player_recent_stats(player, cache_date);

-- Phase 3: Player injury status and IL transaction cache
-- Populated by cache_news.py; read by /underdog-mlb-analyze skill
CREATE TABLE IF NOT EXISTS player_news (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    news_date   TEXT NOT NULL,
    player      TEXT NOT NULL,
    team        TEXT,
    status      TEXT,   -- 'active', 'IL-10', 'IL-15', 'IL-60', 'IL-return-today', 'IL-return-Nd', 'DTD', 'note'
    note        TEXT,
    source      TEXT DEFAULT 'mlb-api',   -- 'mlb-api' or 'web'
    UNIQUE(news_date, player)
);

CREATE INDEX IF NOT EXISTS idx_news_player ON player_news(player, news_date);
CREATE INDEX IF NOT EXISTS idx_news_date   ON player_news(news_date);
