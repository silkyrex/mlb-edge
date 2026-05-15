#!/bin/bash
# Daily fetch -- runs after MLB games end (11pm PT)
cd /Users/rzhu/mlb-edge
/Users/rzhu/.venv/bin/python fetch.py date "$(date +%Y-%m-%d)" >> /tmp/mlb-edge-daily.log 2>&1
