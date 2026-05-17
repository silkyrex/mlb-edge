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
    last5_ip            TEXT,               -- JSON array of IP per outing e.g. [1.0,0.2,5.0] -- detects role changes
    pitcher_role        TEXT,               -- 'starter' (avg IP>=4), 'reliever' (<3), 'mixed'
    season_k9           REAL,
    season_era          REAL,
    season_whip         REAL,
    recent_era          REAL,               -- ERA over last 5 starts
    split_home_era      REAL,
    split_away_era      REAL,
    -- Batter: last 15 games
    last15_h_r_rbi      REAL,               -- avg H+R+RBI per game
    last15_hits         REAL,
    last15_ks_batter    REAL,               -- avg batter Ks per game
    last15_hr           REAL,
    last15_tb           REAL,
    season_avg          REAL,
    season_ops          REAL,
    -- Pitcher: expected stats (contact quality against)
    x_woba_against      REAL,
    x_avg_against       REAL,
    -- Batter: L/R splits (season)
    vs_lhp_avg          REAL,
    vs_lhp_ops          REAL,
    vs_lhp_ab           INTEGER,
    vs_rhp_avg          REAL,
    vs_rhp_ops          REAL,
    vs_rhp_ab           INTEGER,
    -- Batter: home/away splits (season)
    split_home_avg      REAL,
    split_home_ops      REAL,
    split_home_ab       INTEGER,
    split_away_avg      REAL,
    split_away_ops      REAL,
    split_away_ab       INTEGER,
    -- Batter: expected stats (regression signal)
    x_avg               REAL,
    x_slg               REAL,
    x_woba              REAL,
    -- Pitcher: ESPN season stats (cache_espn.py) -- added 2026-05-16
    espn_war            REAL,
    espn_fip            REAL,               -- computed: (13*HR + 3*BB - 2*K) / IP + 3.1
    espn_k_bb           REAL,               -- K/BB ratio
    UNIQUE(player, cache_date)
);

CREATE INDEX IF NOT EXISTS idx_prs_player ON player_recent_stats(player, cache_date);

-- Phase 3: Team-level game stats (bullpen, offense, venue)
-- Populated by cache_team.py; read by /underdog-mlb-analyze skill
CREATE TABLE IF NOT EXISTS team_game_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_date      TEXT NOT NULL,
    game            TEXT NOT NULL,
    team_name       TEXT NOT NULL,
    team_id         INTEGER,
    side            TEXT,           -- 'home' or 'away'
    bullpen_era     REAL,
    bullpen_whip    REAL,
    bullpen_k9      REAL,
    starter_era     REAL,
    team_avg        REAL,
    team_ops        REAL,
    team_k_pct      REAL,
    venue_name      TEXT,
    venue_roof      TEXT,           -- 'Open', 'Dome', 'Retractable'
    venue_left      INTEGER,
    venue_center    INTEGER,
    venue_right     INTEGER,
    UNIQUE(cache_date, game, team_name)
);

CREATE INDEX IF NOT EXISTS idx_tgs_game ON team_game_stats(game, cache_date);

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

-- Underdog pick-em slip tracker (betlog.db, managed by sliplog.py)
-- NOTE: this table lives in betlog.db, not mlb.db. Shown here for reference.
CREATE TABLE IF NOT EXISTS slips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    platform    TEXT NOT NULL DEFAULT 'underdog',
    picks_count INTEGER NOT NULL,
    players     TEXT NOT NULL,         -- JSON list e.g. '["Teng","Soriano"]'
    boost       TEXT,
    entry       REAL NOT NULL,
    payout      REAL NOT NULL,
    multiplier  TEXT,
    status      TEXT NOT NULL DEFAULT 'open',  -- open / win / loss
    profit      REAL,
    logged_at   TEXT DEFAULT (datetime('now')),
    notes       TEXT
);

-- Per-pick structure for Underdog slips (betlog.db, managed by sliplog.py)
-- Captured on `sliplog.py add --picks`. Settled on `sliplog.py result --outcomes`
-- which auto-triggers pick_lessons.observe() per pick.
-- NOTE: this table lives in betlog.db, not mlb.db. Shown here for reference.
CREATE TABLE IF NOT EXISTS slip_picks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slip_id     INTEGER NOT NULL REFERENCES slips(id) ON DELETE CASCADE,
    player      TEXT NOT NULL,
    player_type TEXT NOT NULL,           -- 'pitcher' | 'batter' | 'team'
    stat        TEXT NOT NULL,           -- 'Strikeouts', 'Pitching Outs', 'Hits + Runs + RBIs', etc.
    line        REAL NOT NULL,
    side        TEXT NOT NULL,           -- 'Higher' | 'Lower'
    game        TEXT,                    -- 'TEX @ HOU' (per-pick: multi-game slips are common)
    actual      REAL,                    -- filled on result --outcomes
    hit         INTEGER,                 -- 0/1, filled on result --outcomes
    logged_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(slip_id, player, stat)
);
CREATE INDEX IF NOT EXISTS idx_slip_picks_slip ON slip_picks(slip_id);

-- Pick lessons (betlog.db, managed by pick_lessons.py)
-- Auto-generated rule tracker. Each settled pick observation increments occurrences
-- on a matching rule_key, or creates a new 'watching' row. Graduates to 'confirmed'
-- at 3 same-direction occurrences (0 counters). Falsified at 2 counters.
-- NOTE: this table lives in betlog.db, not mlb.db. Shown here for reference.
CREATE TABLE IF NOT EXISTS pick_lessons (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key     TEXT NOT NULL UNIQUE,        -- e.g. 'pitcher_strikeouts_higher_line_ge_l5_median'
    hypothesis   TEXT NOT NULL,               -- one-sentence plain-talk rule, frozen on first-seen
    direction    TEXT NOT NULL,               -- 'fail' | 'hit' (rule predicts misses or hits)
    occurrences  INTEGER NOT NULL DEFAULT 1,  -- count of same-direction confirmations (game-deduped)
    counters     INTEGER NOT NULL DEFAULT 0,  -- count of opposite-direction outcomes
    status       TEXT NOT NULL DEFAULT 'watching',  -- watching | confirmed | falsified | under_review
    first_seen   DATE NOT NULL,
    last_seen    DATE NOT NULL,
    evidence     TEXT NOT NULL,               -- JSON: [{date, player, stat, line, side, actual, hit, game, redundant?}, ...]
    promoted_at  DATETIME,                    -- when status flipped to confirmed
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS idx_pl_status ON pick_lessons(status);
