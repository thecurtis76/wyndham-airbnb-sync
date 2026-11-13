#!/bin/bash
# One-time Mac setup: Python env, Keychain secrets, git push auth, launchd schedule.
#   ./setup_mac.sh            set up (asks for Wyndham username/password the first time)
#   ./setup_mac.sh --reset    re-enter Wyndham username/password
#   ./setup_mac.sh --urls     just print the Airbnb feed URLs
#   ./setup_mac.sh --remove   stop the schedule
set -euo pipefail
cd "$(dirname "$0")"
DIR="$PWD"
SERVICE=wyndham-airbnb-sync
LABEL=com.thecurtis76.wyndham-airbnb-sync
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/wyndham-airbnb-sync.log"
PAGES="https://thecurtis76.github.io/wyndham-airbnb-sync"

have() { security find-generic-password -s "$SERVICE" -a "$1" >/dev/null 2>&1; }
store() { security add-generic-password -U -s "$SERVICE" -a "$1" -w "$2" -T /usr/bin/security; }

print_urls() {
  local token; token="$(security find-generic-password -s "$SERVICE" -a feed-token -w)"
  echo "Airbnb import URLs (Calendar → Availability → Connect calendars):"
  .venv/bin/python - "$PAGES/$token" <<'PY'
import sys, yaml
base = sys.argv[1]
for l in yaml.safe_load(open("config.yaml"))["listings"]:
    print(f"  {l.get('airbnb_title', l['slug'])}\n    {base}/{l['slug']}.ics")
PY
}

case "${1:-}" in
  --urls) print_urls; exit 0 ;;
  --remove) launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true; rm -f "$PLIST"
            echo "Schedule removed."; exit 0 ;;
esac

echo "1/4 Python environment"
# A venv breaks if the folder moves; rebuild it when it no longer runs.
.venv/bin/python -c "" 2>/dev/null || { rm -rf .venv; python3 -m venv .venv; }
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m playwright install chromium >/dev/null

echo "2/4 Keychain (service: $SERVICE)"
if ! have username || [ "${1:-}" = "--reset" ]; then
  read -rp "  Club Wyndham username: " U
  read -rsp "  Club Wyndham password: " P; echo
  store username "$U"; store password "$P"
fi
have feed-token || store feed-token "$(openssl rand -hex 12)"

echo "3/4 GitHub push access"
gh auth setup-git

echo "4/4 Schedule (4x a day; runs on wake if a time was missed)"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/run_sync.sh</string></array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>1</integer><key>Minute</key><integer>23</integer></dict>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>23</integer></dict>
    <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>23</integer></dict>
    <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>23</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "Done. Next: run ./run_sync.sh once in Terminal (enter the texted code if asked)."
echo "Log: $LOG"
echo
print_urls
