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
# User config + per-project state all live under ~/.codex-self-evolution,
# parallel to ~/.claude/. Keeps the plugin's source tree out of the user's
# dotfile concerns and out of every repo they work in.
PLUGIN_HOME="$HOME/.codex-self-evolution"
ENV_FILE="$PLUGIN_HOME/.env.provider"
LEGACY_ENV_FILE="$REPO/.env.provider"
ENV_EXAMPLE="$REPO/.env.provider.example"
# PyPI package name. We pull via `uvx --from <pkg> <entry-point>` so the
# user's environment stays clean (no venv to create/maintain, no global
# pip install, no clone required long-term).
PYPI_PACKAGE="codex-self-evolution-plugin"
ENTRY_POINT="codex-self-evolution"
# Embedded in the hook command as a bash no-op (`:` swallows its args), so the
# uninstall script can grep for it without us needing to introduce a custom
# JSON field that Codex's schema validator might reject.
MARKER="codex-self-evolution-plugin managed"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- preflight ----------
info "preflight checks"

# `uv`/`uvx` is the only runtime we require — no more clone-a-repo +
# create-a-venv + pip-install-editable dance. If the user doesn't have it
# yet, point them at the official installer rather than auto-installing
# (we don't want to surprise-curl a binary into their PATH).
if ! command -v uvx >/dev/null 2>&1; then
    fail "uvx not found on PATH. Install with: brew install uv  (or: curl -LsSf https://astral.sh/uv/install.sh | sh)"
fi
UVX_VERSION=$(uvx --version 2>/dev/null | head -1)
echo "  uvx OK ($UVX_VERSION)"

command -v codex >/dev/null 2>&1 || warn "codex CLI not found on PATH (hook will still be installed; just won't fire until codex is installed)"

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

# ---------- upsert the hooks ----------
info "upserting Stop + SessionStart hooks in $HOOKS_JSON"

python3 - "$HOOKS_JSON" "$MARKER" "$ENV_FILE" "$PYPI_PACKAGE" "$ENTRY_POINT" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
marker = sys.argv[2]
env_file = sys.argv[3]
pypi_package = sys.argv[4]
entry_point = sys.argv[5]

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

# Stop hook: runs the reviewer. Needs .env.provider for MINIMAX_API_KEY etc.
# Timeout 20s handles cold-start uvx cases (first invocation after a plugin
# update ~1-2s, warm ≤1s). --from-stdin detaches the real reviewer subprocess
# so the hook itself just writes a tempfile, spawns, and exits.
stop_command = (
    f"bash -c ': {marker}; "
    f"set -a; . {env_file} 2>/dev/null; set +a; "
    f"exec uvx --from {pypi_package} {entry_point} stop-review --from-stdin'"
)
stop_entry = {
    "hooks": [{"type": "command", "command": stop_command, "timeout": 20}]
}

# SessionStart hook: synchronously assembles stable-background + recall
# policy, emits Codex `hookSpecificOutput.additionalContext` JSON. Does NOT
# need .env.provider (no LLM call). Timeout 15s covers cold uvx; warm hits
# ~150ms for reading a few MD files.
# Verified against codex-cli 0.122.0 that additionalContext actually gets
# injected as DeveloperInstructions in the model session. See
# docs/todo.md 2026-04-21 P0-0 entry for the research trail.
session_start_command = (
    f"bash -c ': {marker}; "
    f"exec uvx --from {pypi_package} {entry_point} session-start --from-stdin'"
)
session_start_entry = {
    "hooks": [{"type": "command", "command": session_start_command, "timeout": 15}]
}


def upsert(event_name, new_entry, legacy_substring=None):
    """Idempotently install `new_entry` into hooks[event_name].

    - If a managed entry (with our marker) already exists: replace in-place.
    - If a legacy hand-installed entry exists (same functional command, no
      marker): upgrade it in-place so repeated installs don't dupe.
    - Otherwise: append. Preserves any third-party entries (vibe-island,
      luna, etc.) in the same event list.
    """
    event_list = hooks.setdefault(event_name, [])
    existing_idx = None
    legacy_idx = None
    for i, entry in enumerate(event_list):
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if marker in cmd:
                existing_idx = i
                break
            if legacy_substring and legacy_substring in cmd:
                legacy_idx = i
        if existing_idx is not None:
            break

    if existing_idx is not None:
        event_list[existing_idx] = new_entry
        print(f"  updated existing managed {event_name} entry at index {existing_idx}")
    elif legacy_idx is not None:
        event_list[legacy_idx] = new_entry
        print(f"  upgraded legacy unmarked {event_name} entry at index {legacy_idx} (was hand-installed)")
    else:
        event_list.append(new_entry)
        print(f"  appended new {event_name} entry (total now {len(event_list)})")


upsert("Stop", stop_entry, legacy_substring="codex_self_evolution.cli stop-review")
upsert("SessionStart", session_start_entry, legacy_substring="codex_self_evolution.cli session-start")

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

# ---------- warm uvx cache ----------
# First uvx invocation downloads the wheel + builds an ephemeral venv
# (~1-2s). We warm before the first Stop/SessionStart fire so the user's
# first Codex session doesn't eat the hook timeout budget on a wheel
# download.
info "warming uvx cache"
uvx --from "$PYPI_PACKAGE" "$ENTRY_POINT" --help >/dev/null 2>&1 || \
    warn "  uvx warmup failed — first hook fire may be slower than steady state"

info "done."
echo ""
echo "Next steps:"
echo "  1. Start a new codex session: codex (or codex exec 'hi')"
echo "  2. After your first turn, inspect the per-project bucket:"
echo "       ls $PLUGIN_HOME/projects/"
echo "       # each repo gets its own dir named after its path (/ → -)"
echo "       ls $PLUGIN_HOME/projects/*/suggestions/pending/"
echo "  3. To remove this hook later: $REPO/scripts/uninstall-codex-hook.sh"
