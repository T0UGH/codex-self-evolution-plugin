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
# where `opencode` typically lives. We detect opencode's directory at install
# time and bake it into EnvironmentVariables.PATH so the agent:opencode
# backend actually runs. If opencode isn't on PATH, we warn but continue
# (scan will fall back to script backend automatically).

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$REPO/.venv/bin/python"
PLUGIN_HOME="$HOME/.codex-self-evolution"
LOG_DIR="$PLUGIN_HOME/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL="com.codex-self-evolution.preflight"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
# Default: drain every 5 minutes. Matches the old hand-edited plist and is
# a reasonable tradeoff — compile itself takes seconds to minutes, and
# suggestions sitting in pending/ cost nothing until they're compiled.
INTERVAL_SECONDS="${CSEP_SCHEDULER_INTERVAL:-300}"
# Default: use agent:opencode for semantic-merge; scan auto-falls-back to
# script if opencode is unavailable at runtime.
BACKEND="${CSEP_SCHEDULER_BACKEND:-agent:opencode}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- preflight ----------
info "preflight checks"
[ -x "$VENV_PYTHON" ] || fail "$VENV_PYTHON not executable. Run scripts/install-codex-hook.sh first (it sets up the venv)."
"$VENV_PYTHON" -c 'from codex_self_evolution.cli import main' \
    || fail "venv python cannot import codex_self_evolution.cli"
echo "  venv python OK"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

# ---------- detect opencode PATH ----------
# User's shell probably has opencode on PATH; launchd's doesn't. We grab
# whatever dir the user's current shell sees and prepend it.
OPENCODE_BIN="$(command -v opencode 2>/dev/null || true)"
if [ -n "$OPENCODE_BIN" ]; then
    OPENCODE_DIR="$(dirname "$OPENCODE_BIN")"
    echo "  opencode found at $OPENCODE_BIN"
else
    OPENCODE_DIR=""
    warn "opencode not on PATH — scheduler will use script backend fallback."
    warn "  to enable agent:opencode: npm i -g opencode-ai (or brew install opencode),"
    warn "  then re-run this script so the path gets baked into the plist."
fi
# launchd default PATH is narrow. Always include /opt/homebrew/bin (Apple
# Silicon) and /usr/local/bin (Intel) even if opencode wasn't found today —
# user may install it later without re-running this script.
PLIST_PATH_ENV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if [ -n "$OPENCODE_DIR" ]; then
    case ":$PLIST_PATH_ENV:" in
        *":$OPENCODE_DIR:"*) ;;  # already included
        *) PLIST_PATH_ENV="$OPENCODE_DIR:$PLIST_PATH_ENV" ;;
    esac
fi

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
        <string>$VENV_PYTHON</string>
        <string>-m</string>
        <string>codex_self_evolution.cli</string>
        <string>scan</string>
        <string>--backend</string>
        <string>$BACKEND</string>
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
echo "  backend:  $BACKEND (override via CSEP_SCHEDULER_BACKEND)"
echo "  plist:    $PLIST_PATH"
echo "  logs:     $LOG_DIR/launchd.{stdout,stderr}.log"
echo ""
echo "Verify:"
echo "  launchctl list | grep codex-self-evolution"
echo "  # first run fires in ~\${INTERVAL_SECONDS}s; trigger manually with:"
echo "  launchctl kickstart gui/\$(id -u)/$LABEL"
echo ""
echo "Remove later: $REPO/scripts/uninstall-scheduler.sh"
