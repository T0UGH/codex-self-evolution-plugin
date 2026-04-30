#!/usr/bin/env bash
# Remove legacy codex-self-evolution-plugin hook entries. New installs declare
# hooks in the plugin bundle; this script only cleans old managed entries marked
# with the embedded marker string.
#
# Intentionally does NOT touch:
#   - ~/.bashrc (MINIMAX_* exports are the user's shell config)
#   - ~/.codex/config.toml (shell_environment_policy may benefit other tools)
#   - the repo itself (.venv / .env.provider stay put)
set -euo pipefail

HOOKS_JSON="$HOME/.codex/hooks.json"
MARKER="codex-self-evolution-plugin managed"
CSEP_BIN="${CSEP_BIN_DIR:-$HOME/.local/bin}/csep"
CSEP_WRAPPER_MARKER="codex-self-evolution-plugin managed csep wrapper"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }

if [ -f "$HOOKS_JSON" ]; then
    command -v python3 >/dev/null 2>&1 || { warn "python3 not found; cannot edit hooks.json safely"; exit 1; }

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

hooks = data.get("hooks", {})
touched_events = []

for event, entries in list(hooks.items()):
    if not isinstance(entries, list):
        continue

    kept = []
    removed_here = 0
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
        removed_here += len(entry_hooks) - len(filtered_hooks)
        if not filtered_hooks:
            continue

        entry["hooks"] = filtered_hooks
        kept.append(entry)
    if removed_here:
        touched_events.append((event, removed_here))
    hooks[event] = kept

removed_total = sum(n for _, n in touched_events)

# Drop now-empty event lists so hooks.json stays tidy.
for event in list(hooks.keys()):
    if not hooks[event]:
        del hooks[event]

path.write_text(json.dumps(data, indent=2), encoding="utf-8")

if removed_total == 0:
    print("  no managed hook entries found — nothing changed")
else:
    for event, n in touched_events:
        print(f"  removed {n} managed entry from {event}")
    print(f"  {removed_total} total")
PY
else
    info "no $HOOKS_JSON — no legacy hook entries to remove"
fi

if [ -f "$CSEP_BIN" ] && grep -q "$CSEP_WRAPPER_MARKER" "$CSEP_BIN" 2>/dev/null; then
    rm -f "$CSEP_BIN"
    echo "  removed managed csep wrapper: $CSEP_BIN"
fi
info "done."
echo ""
echo "Note (not auto-removed, edit by hand if you want to fully clean up):"
echo "  - ~/.bashrc:   export MINIMAX_API_KEY / MINIMAX_REGION"
echo "  - ~/.codex/config.toml: [shell_environment_policy] inherit = \"all\""
echo "  - repo:        .env.provider, .venv"
echo "Both are harmless if left in place and may also serve other tools."
