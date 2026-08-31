#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
APP_PATH="$PROJECT_DIR/Job Scout.app"
LAUNCHER="$PROJECT_DIR/run_job_scout.command"

/usr/bin/osacompile -o "$APP_PATH" \
  -e "tell application \"Terminal\"" \
  -e "activate" \
  -e "do script quoted form of \"$LAUNCHER\"" \
  -e "end tell"

echo "Created $APP_PATH"

