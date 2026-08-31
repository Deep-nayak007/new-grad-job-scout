#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if /usr/bin/curl -fsS --max-time 2 http://127.0.0.1:8765/api/stats >/dev/null 2>&1; then
  /usr/bin/open http://127.0.0.1:8765
  exit 0
fi

exec /usr/bin/env python3 -m job_scout.app

