#!/bin/bash
# Daily automation -- runs at 11pm PT via launchd
# 1. Ingest today's completed games
# 2. Pre-cache tomorrow's injury/IL status for all games
# 3. Check open bets against final box scores, post to Discord

# Resolve script directory so this works from any working directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load config from .env
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

LOG=/tmp/mlb-edge-daily.log
DIR="${MLB_EDGE_DIR:-$SCRIPT_DIR}"
PYTHON="${PYTHON_BIN:-$(which python3)}"

echo "=== MLB Edge Daily $(date) ===" >> "$LOG"

# Step 1: Ingest today's completed games
echo "[fetch] $(date +%H:%M)" >> "$LOG"
cd "$DIR"
$PYTHON fetch.py date "$(date +%Y-%m-%d)" >> "$LOG" 2>&1 || echo "[fetch] WARNING: failed" >> "$LOG"

# Step 2: Pre-cache tomorrow's IL/injury status for all games
TOMORROW=$(date -v+1d +%Y-%m-%d 2>/dev/null || date -d tomorrow +%Y-%m-%d)
echo "[cache_news] Pre-caching tomorrow ($TOMORROW)" >> "$LOG"
$PYTHON "$DIR/cache_tomorrow.py" --date "$TOMORROW" >> "$LOG" 2>&1 || echo "[cache_news] WARNING: failed" >> "$LOG"

# Step 3: Check open bets, post final-game results to Discord
echo "[settle] Checking open bets" >> "$LOG"
$PYTHON "$DIR/settle.py" --post-discord >> "$LOG" 2>&1 || echo "[settle] WARNING: failed" >> "$LOG"

echo "=== Done $(date) ===" >> "$LOG"
