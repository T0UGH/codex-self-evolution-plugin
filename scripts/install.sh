#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_HOME="${CODEX_SELF_EVOLUTION_HOME:-$HOME/.codex-self-evolution}"
HOOKS_JSON="$HOME/.codex/hooks.json"
INSTALL_SOURCE="${CSEP_INSTALL_SOURCE:-$REPO}"
MARKER="codex-self-evolution-plugin managed"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || fail "uv not found on PATH. Install with: brew install uv"

info "installing local CLI with uv tool"
uv tool install --force "$INSTALL_SOURCE"

TOOL_BIN="$(uv tool dir --bin 2>/dev/null || true)"
if [ -n "$TOOL_BIN" ]; then
    case ":$PATH:" in
        *":$TOOL_BIN:"*) ;;
        *) warn "uv tool bin is not on PATH for this shell: $TOOL_BIN";;
    esac
fi

command -v codex-self-evolution >/dev/null 2>&1 || fail "codex-self-evolution is not visible on PATH after uv tool install"
command -v csep >/dev/null 2>&1 || fail "csep is not visible on PATH after uv tool install"
codex-self-evolution --help >/dev/null
csep --help >/dev/null

mkdir -p "$PLUGIN_HOME"

if [ -f "$HOOKS_JSON" ]; then
    BACKUP="$HOOKS_JSON.bak.$(date +%s)"
    cp "$HOOKS_JSON" "$BACKUP"
    info "backed up $HOOKS_JSON -> $BACKUP"
    python3 - "$HOOKS_JSON" "$MARKER" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
marker = sys.argv[2]
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    print(f"  existing hooks.json is invalid JSON ({exc}); aborting", file=sys.stderr)
    sys.exit(1)

hooks = data.get("hooks")
if isinstance(hooks, dict):
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            entry_hooks = entry.get("hooks", [])
            if not isinstance(entry_hooks, list):
                kept.append(entry)
                continue

            filtered_hooks = [
                h
                for h in entry_hooks
                if not (isinstance(h, dict) and marker in h.get("command", ""))
            ]
            if not filtered_hooks:
                continue

            entry["hooks"] = filtered_hooks
            kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY
fi

info "done"
echo "Enable the Codex plugin with plugins, codex_hooks, and plugin_hooks features."
