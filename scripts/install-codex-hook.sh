#!/usr/bin/env bash
# Install the Codex Stop hook that drives codex-self-evolution-plugin.
#
# Idempotent: running twice leaves exactly one managed entry in
# ~/.codex/hooks.json. Identified by the marker string embedded in the
# hook command so the uninstall script can find it regardless of path.
#
# This script modifies ~/.codex/hooks.json (backed up with a timestamp
# before each write) and does NOT touch ~/.bashrc or ~/.codex/config.toml —
# those belong to the user's shell / global Codex config and may contain
# other tooling's settings.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_JSON="$HOME/.codex/hooks.json"
CONFIG_TOML="$HOME/.codex/config.toml"
VENV="$REPO/.venv"
VENV_PYTHON="$VENV/bin/python"
# User config + per-project state all live under ~/.codex-self-evolution,
# parallel to ~/.claude/. Keeps the plugin's source tree out of the user's
# dotfile concerns and out of every repo they work in.
PLUGIN_HOME="$HOME/.codex-self-evolution"
ENV_FILE="$PLUGIN_HOME/.env.provider"
LEGACY_ENV_FILE="$REPO/.env.provider"
ENV_EXAMPLE="$REPO/.env.provider.example"
# Embedded in the hook command as a bash no-op (`:` swallows its args), so the
# uninstall script can grep for it without us needing to introduce a custom
# JSON field that Codex's schema validator might reject.
MARKER="codex-self-evolution-plugin managed"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- preflight ----------
info "preflight checks"

command -v python3 >/dev/null 2>&1 || fail "python3 not found on PATH"

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
case "$PY_VER" in
    3.11|3.12|3.13|3.14|3.15|3.16) ;;
    *) fail "python3 >= 3.11 required, found $PY_VER" ;;
esac
echo "  python3 $PY_VER OK"

command -v codex >/dev/null 2>&1 || warn "codex CLI not found on PATH (hook will still be installed; just won't fire until codex is installed)"

if [ ! -x "$VENV_PYTHON" ]; then
    warn "$VENV_PYTHON not found"
    read -r -p "    create a venv and pip install -e . now? [y/N] " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        python3 -m venv "$VENV"
        "$VENV_PYTHON" -m pip install --quiet --upgrade pip
        "$VENV_PYTHON" -m pip install --quiet -e "$REPO"
        echo "  venv created and plugin installed editable"
    else
        fail "aborting: venv required so the hook command can run the plugin"
    fi
fi
# Confirm the entry point resolves.
"$VENV_PYTHON" -c 'from codex_self_evolution.cli import main' \
    || fail "venv python cannot import codex_self_evolution.cli (run: $VENV_PYTHON -m pip install -e $REPO)"
echo "  venv python imports CLI OK"

mkdir -p "$PLUGIN_HOME"

# One-shot migration: if the user had the old repo-root .env.provider from
# a previous install, move it into the home config dir. We use `mv` (not copy)
# so there's a single source of truth — accidentally hand-editing the stale
# repo copy would be a confusing failure mode.
if [ ! -f "$ENV_FILE" ] && [ -f "$LEGACY_ENV_FILE" ]; then
    mv "$LEGACY_ENV_FILE" "$ENV_FILE"
    info "migrated legacy .env.provider from $LEGACY_ENV_FILE → $ENV_FILE"
fi

if [ ! -f "$ENV_FILE" ]; then
    warn ".env.provider missing at $ENV_FILE"
    warn "  the hook will still install, but reviewer calls will fail with 401 unless"
    warn "  your shell already exports MINIMAX_API_KEY (or OPENAI_API_KEY / ANTHROPIC_API_KEY)."
    if [ -f "$ENV_EXAMPLE" ]; then
        warn "  tip: cp $ENV_EXAMPLE $ENV_FILE  then fill in the key"
    fi
else
    echo "  .env.provider present at $ENV_FILE"
fi

# ---------- backup ----------
mkdir -p "$HOME/.codex"
BACKUP=""
if [ -f "$HOOKS_JSON" ]; then
    BACKUP="$HOOKS_JSON.bak.$(date +%s)"
    cp "$HOOKS_JSON" "$BACKUP"
    info "backed up $HOOKS_JSON → $BACKUP"
fi

# ---------- upsert the hook ----------
info "upserting Stop hook in $HOOKS_JSON"

python3 - "$HOOKS_JSON" "$REPO" "$MARKER" "$ENV_FILE" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
repo = sys.argv[2]
marker = sys.argv[3]
env_file = sys.argv[4]

if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  existing hooks.json is invalid JSON ({exc})")
        print("  the backup is safe; aborting so you can inspect it manually")
        sys.exit(1)
else:
    data = {}

hooks = data.setdefault("hooks", {})
stop_list = hooks.setdefault("Stop", [])

command = (
    f"bash -c ': {marker}; "
    f"set -a; . {env_file} 2>/dev/null; set +a; "
    f"exec {repo}/.venv/bin/python -m codex_self_evolution.cli stop-review --from-stdin'"
)
new_entry = {
    "hooks": [
        {
            "type": "command",
            "command": command,
            "timeout": 10,
        }
    ]
}

existing_idx = None
legacy_idx = None
for i, entry in enumerate(stop_list):
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if marker in cmd:
            existing_idx = i
            break
        if "codex_self_evolution.cli stop-review" in cmd:
            # Hand-edited entry from before this script existed: same effect,
            # just missing our marker. Treat it as a legacy install and
            # upgrade it in place instead of appending a duplicate.
            legacy_idx = i
    if existing_idx is not None:
        break

if existing_idx is not None:
    stop_list[existing_idx] = new_entry
    print(f"  updated existing managed Stop entry at index {existing_idx}")
elif legacy_idx is not None:
    stop_list[legacy_idx] = new_entry
    print(f"  upgraded legacy unmarked Stop entry at index {legacy_idx} (was hand-installed)")
else:
    stop_list.append(new_entry)
    print(f"  appended new Stop entry (total now {len(stop_list)})")

path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY

# ---------- advisory: config.toml env inheritance ----------
if [ -f "$CONFIG_TOML" ]; then
    if grep -q '^\[shell_environment_policy\]' "$CONFIG_TOML" 2>/dev/null; then
        echo "  [shell_environment_policy] already present in $CONFIG_TOML"
    else
        warn "$CONFIG_TOML has no [shell_environment_policy] section."
        warn "  Recommended: add the two lines below so Codex passes your shell env to hooks:"
        warn "    [shell_environment_policy]"
        warn "    inherit = \"all\""
        warn "  Without this, Codex may strip MINIMAX_API_KEY when spawning the hook process."
    fi
else
    warn "$CONFIG_TOML not found — run codex at least once to create it, then re-run this script"
fi

info "done."
echo ""
echo "Next steps:"
echo "  1. Start a new codex session: codex (or codex exec 'hi')"
echo "  2. After your first turn, inspect the per-project bucket:"
echo "       ls $PLUGIN_HOME/projects/"
echo "       # each repo gets its own dir named after its path (/ → -)"
echo "       ls $PLUGIN_HOME/projects/*/suggestions/pending/"
echo "  3. To remove this hook later: $REPO/scripts/uninstall-codex-hook.sh"
