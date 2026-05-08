#!/usr/bin/env bash
# Install the launchd scheduler that runs codex-self-evolution `scan` at a
# fixed interval. Drains every per-project bucket under
# ~/.codex-self-evolution/projects/* on each tick.
#
# Idempotent: re-running this replaces any previously-installed version
# cleanly (bootout → new plist → bootstrap). Neighboring launchd jobs are
# never touched because we address ours by an exact Label.
#
# PATH on launchd user agents is very minimal by default
# (/usr/bin:/bin:/usr/sbin:/sbin) — it does NOT include Homebrew or /usr/local
# where local agent CLIs typically live. We detect pi's directory at install
# time and bake it into EnvironmentVariables.PATH so the agent:pi backend
# actually runs. If pi isn't on PATH, we warn but continue; scan will fail
# visibly instead of silently falling back to script.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_HOME="$HOME/.codex-self-evolution"
LOG_DIR="$PLUGIN_HOME/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL="com.codex-self-evolution.preflight"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
ENTRY_POINT="${CSEP_ENTRY_POINT:-codex-self-evolution}"
SCAN_MAX_RUNS_PER_PROJECT="${CSEP_SCAN_MAX_RUNS_PER_PROJECT:-3}"
SCAN_ARGS=("scan" "--backend" "agent:pi" "--max-runs-per-project" "$SCAN_MAX_RUNS_PER_PROJECT")
# Default: drain every 5 minutes. Matches the old hand-edited plist and is
# a reasonable tradeoff — compile itself takes seconds to minutes, and
# suggestions sitting in pending/ cost nothing until they're compiled.
INTERVAL_SECONDS="${CSEP_SCHEDULER_INTERVAL:-300}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- preflight ----------
info "preflight checks"
command -v "$ENTRY_POINT" >/dev/null 2>&1 || \
    fail "$ENTRY_POINT not found on PATH. Run scripts/install.sh first."
ENTRY_POINT_BIN="$(command -v "$ENTRY_POINT")"
ENTRY_POINT_DIR="$(dirname "$ENTRY_POINT_BIN")"
echo "  $ENTRY_POINT OK at $ENTRY_POINT_BIN"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

# ---------- detect pi PATH ----------
# User's shell probably has pi on PATH; launchd's doesn't. We grab
# whatever dir the user's current shell sees and prepend it.
PI_BIN="$(command -v pi 2>/dev/null || true)"
if [ -n "$PI_BIN" ]; then
    PI_DIR="$(dirname "$PI_BIN")"
    echo "  pi found at $PI_BIN"
else
    PI_DIR=""
    warn "pi not on PATH — scheduler agent:pi will not run."
    warn "  install pi or adjust PATH,"
    warn "  then re-run this script so the path gets baked into the plist."
fi
# launchd default PATH is narrow. Always include /opt/homebrew/bin (Apple
# Silicon) and /usr/local/bin (Intel) even if pi wasn't found today —
# user may install it later without re-running this script.
PLIST_PATH_ENV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
for dir in "$ENTRY_POINT_DIR" "$PI_DIR"; do
    [ -z "$dir" ] && continue
    case ":$PLIST_PATH_ENV:" in
        *":$dir:"*) ;;  # already present
        *) PLIST_PATH_ENV="$dir:$PLIST_PATH_ENV" ;;
    esac
done

# ---------- clean up any previous install ----------
# bootout is idempotent-ish: noop if the service isn't loaded. Stderr is
# noisy ("No such process") when that's the case — hide it.
if [ -f "$PLIST_PATH" ]; then
    info "removing previous $LABEL install"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# ---------- write plist ----------
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
        <string>${SCAN_ARGS[0]}</string>
        <string>${SCAN_ARGS[1]}</string>
        <string>${SCAN_ARGS[2]}</string>
        <string>${SCAN_ARGS[3]}</string>
        <string>${SCAN_ARGS[4]}</string>
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
    <string>$LOG_DIR/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/launchd.stderr.log</string>

    <!-- ThrottleInterval caps retry frequency if the job exits non-zero. -->
    <key>ThrottleInterval</key>
    <integer>60</integer>
</dict>
</plist>
PLIST

# ---------- load ----------
info "loading $LABEL into launchd"
# bootstrap is the modern replacement for `load -w`. domain-target gui/<uid>
# means "user's GUI session" — same as the old "user" domain for LaunchAgents.
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

info "done."
echo ""
echo "Scheduler installed:"
echo "  label:    $LABEL"
echo "  interval: ${INTERVAL_SECONDS}s (override via CSEP_SCHEDULER_INTERVAL)"
echo "  max runs: ${SCAN_MAX_RUNS_PER_PROJECT}/project (override via CSEP_SCAN_MAX_RUNS_PER_PROJECT)"
echo "  command:  $ENTRY_POINT_BIN ${SCAN_ARGS[*]}"
echo "  plist:    $PLIST_PATH"
echo "  logs:     $LOG_DIR/launchd.{stdout,stderr}.log"
echo ""
echo "Verify:"
echo "  launchctl list | grep codex-self-evolution"
echo "  # first run fires in ~\${INTERVAL_SECONDS}s; trigger manually with:"
echo "  launchctl kickstart gui/\$(id -u)/$LABEL"
echo ""
echo "Remove later: $REPO/scripts/uninstall-scheduler.sh"
