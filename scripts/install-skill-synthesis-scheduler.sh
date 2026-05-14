#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_HOME="$HOME/.codex-self-evolution"
LOG_DIR="$PLUGIN_HOME/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL="com.codex-self-evolution.skill-synthesis"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
ENTRY_POINT="${CSEP_ENTRY_POINT:-codex-self-evolution}"
INTERVAL_SECONDS="${CSEP_SKILL_SYNTHESIS_INTERVAL:-14400}"
LOOKBACK_HOURS="${CSEP_SKILL_SYNTHESIS_LOOKBACK_HOURS:-24}"
ARGS=("skill-synthesize" "--mode" "incremental" "--lookback-hours" "$LOOKBACK_HOURS")

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

info "preflight checks"
command -v "$ENTRY_POINT" >/dev/null 2>&1 || fail "$ENTRY_POINT not found on PATH. Run scripts/install.sh first."
ENTRY_POINT_BIN="$(command -v "$ENTRY_POINT")"
ENTRY_POINT_DIR="$(dirname "$ENTRY_POINT_BIN")"
echo "  $ENTRY_POINT OK at $ENTRY_POINT_BIN"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

PI_BIN="$(command -v pi 2>/dev/null || true)"
if [ -n "$PI_BIN" ]; then
    PI_DIR="$(dirname "$PI_BIN")"
    echo "  pi found at $PI_BIN"
else
    PI_DIR=""
    warn "pi not on PATH — skill synthesis agent:pi will not run."
fi

PLIST_PATH_ENV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
for dir in "$ENTRY_POINT_DIR" "$PI_DIR"; do
    [ -z "$dir" ] && continue
    case ":$PLIST_PATH_ENV:" in
        *":$dir:"*) ;;
        *) PLIST_PATH_ENV="$dir:$PLIST_PATH_ENV" ;;
    esac
done

if [ -f "$PLIST_PATH" ]; then
    info "removing previous $LABEL install"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

info "writing $PLIST_PATH"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ENTRY_POINT_BIN</string>
        <string>${ARGS[0]}</string>
        <string>${ARGS[1]}</string>
        <string>${ARGS[2]}</string>
        <string>${ARGS[3]}</string>
        <string>${ARGS[4]}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$PLIST_PATH_ENV</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
    <key>StartInterval</key>
    <integer>$INTERVAL_SECONDS</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/skill-synthesis.launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/skill-synthesis.launchd.stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>60</integer>
</dict>
</plist>
PLIST

info "loading $LABEL into launchd"
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

info "done."
echo ""
echo "Skill synthesis scheduler installed:"
echo "  label:    $LABEL"
echo "  interval: ${INTERVAL_SECONDS}s (override via CSEP_SKILL_SYNTHESIS_INTERVAL)"
echo "  command:  $ENTRY_POINT_BIN ${ARGS[*]}"
echo "  plist:    $PLIST_PATH"
echo "  logs:     $LOG_DIR/skill-synthesis.launchd.{stdout,stderr}.log"
echo ""
echo "Recommended first smoke:"
echo "  codex-self-evolution skill-synthesize --mode full --lookback-days 30 --dry-run"
echo ""
echo "Trigger real run manually:"
echo "  launchctl kickstart gui/\$(id -u)/$LABEL"
echo ""
echo "Remove later: $REPO/scripts/uninstall-skill-synthesis-scheduler.sh"
