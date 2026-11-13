#!/bin/bash
# Fetch Club Wyndham availability and publish the Airbnb feeds to GitHub Pages.
#
# Run it once yourself in Terminal (it may text you a code the first time),
# then launchd runs it on a schedule (see setup_mac.sh).
#   ./run_sync.sh            fetch + publish
#   ./run_sync.sh --headed   same, with the browser visible
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1   # write log lines as they happen

SERVICE=wyndham-airbnb-sync
LOG="$HOME/Library/Logs/wyndham-airbnb-sync.log"
kc() {
  security find-generic-password -s "$SERVICE" -a "$1" -w 2>/dev/null || {
    echo "Missing '$1' in Keychain. Run ./setup_mac.sh first." >&2; exit 1; }
}
notify() {
  osascript -e "display notification \"$1\" with title \"Wyndham → Airbnb sync\"" >/dev/null 2>&1 || true
}

echo "=== $(date) ==="
export WYNDHAM_USERNAME="$(kc username)" WYNDHAM_PASSWORD="$(kc password)" FEED_TOKEN="$(kc feed-token)"
export WYNDHAM_PROFILE="$PWD/.wyndham-profile"

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
if ! .venv/bin/python sync.py --out "$OUT/site" "$@"; then
  notify "Sync failed. Details: $LOG"
  exit 1
fi

# Publish: replace the gh-pages branch with this run's feeds (no history kept).
REMOTE="$(git remote get-url origin)"
(
  cd "$OUT/site"
  git init -q -b gh-pages
  git add -A
  git -c user.name="wyndham-airbnb-sync" -c user.email="noreply@users.noreply.github.com" \
      commit -qm "Feeds $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push -q --force "$REMOTE" gh-pages
)
echo "Published to GitHub Pages."
