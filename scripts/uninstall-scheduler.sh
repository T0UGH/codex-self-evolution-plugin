#!/usr/bin/env bash
# Remove the launchd scheduler installed by install-scheduler.sh.
#
# Only touches com.codex-self-evolution.preflight — does NOT remove
# ~/.codex/hooks.json entries (that's uninstall-codex-hook.sh) or any
# neighboring launchd jobs.

set -euo pipefail

LABEL="com.codex-self-evolution.preflight"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }

if [ ! -f "$PLIST_PATH" ]; then
    info "no $PLIST_PATH — nothing to uninstall"
    exit 0
fi

info "unloading $LABEL from launchd"
# "No such process" is fine if the agent was already manually unloaded.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

info "removing $PLIST_PATH"
rm -f "$PLIST_PATH"

info "done."
echo ""
echo "Note (not auto-removed):"
echo "  - ~/.codex-self-evolution/logs/launchd.{stdout,stderr}.log"
echo "    (keep them for post-mortem; rm by hand if you want a clean slate)"
