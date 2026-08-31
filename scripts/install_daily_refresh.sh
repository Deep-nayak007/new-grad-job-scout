#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PLIST_PATH="$HOME/Library/LaunchAgents/com.local.jobscout.refresh.plist"
PYTHON_PATH="$(command -v python3)"

/bin/mkdir -p "$HOME/Library/LaunchAgents"
/bin/cp "$SCRIPT_DIR/com.local.jobscout.refresh.plist" "$PLIST_PATH"
/usr/bin/plutil -replace ProgramArguments -json "[\"$PYTHON_PATH\",\"-m\",\"job_scout.app\",\"--refresh-only\"]" "$PLIST_PATH"
/usr/bin/plutil -replace WorkingDirectory -string "$PROJECT_DIR" "$PLIST_PATH"
/usr/bin/plutil -replace StandardOutPath -string "$PROJECT_DIR/data/daily-refresh.log" "$PLIST_PATH"
/usr/bin/plutil -replace StandardErrorPath -string "$PROJECT_DIR/data/daily-refresh-error.log" "$PLIST_PATH"

/bin/launchctl bootout "gui/$(id -u)/com.local.jobscout.refresh" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
echo "Installed daily 8:00 AM refresh. The Excel file updates even when the web app is closed."

