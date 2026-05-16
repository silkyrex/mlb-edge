#!/bin/bash
# Wrapper for launchd -- sources .env so paths are configurable
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

PYTHON="${PYTHON_BIN:-$(which python3)}"
exec $PYTHON "$SCRIPT_DIR/morning_brief.py" --post
