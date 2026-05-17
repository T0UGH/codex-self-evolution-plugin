#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CSEP_BIN="${CSEP_BIN:-csep}"
PYTHON="${PYTHON:-python3.11}"
STATE_DIR="${CSEP_SMOKE_STATE_DIR:-}"
CLEANUP_STATE=0

if [ -z "$STATE_DIR" ]; then
  STATE_DIR="$(mktemp -d /tmp/csep-runtime-smoke.XXXXXX)"
  CLEANUP_STATE=1
fi

cleanup() {
  if [ "$CLEANUP_STATE" = "1" ]; then
    rm -rf "$STATE_DIR"
  fi
}
trap cleanup EXIT

log() {
  printf '==> %s\n' "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'missing required command: %s\n' "$1" >&2
    exit 127
  fi
}

assert_json() {
  "$PYTHON" - "$@" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
kind = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))

if kind == "session-start":
    output = data.get("hookSpecificOutput") or {}
    if output.get("hookEventName") != "SessionStart":
        raise SystemExit(f"invalid SessionStart output: {data}")
    if not isinstance(output.get("additionalContext"), str):
        raise SystemExit("SessionStart missing additionalContext")
elif kind == "archive":
    if data.get("status") != "archived":
        raise SystemExit(f"archive did not complete: {data}")
elif kind == "recall":
    if data.get("status") != "matched" or int(data.get("count") or 0) < 1:
        raise SystemExit(f"recall did not match: {data}")
elif kind == "claude-sync":
    if data.get("status") != "completed" or data.get("source") != "claude_code":
        raise SystemExit(f"Claude sync did not complete: {data}")
    if int(data.get("processed_successfully") or 0) < 1:
        raise SystemExit(f"Claude sync archived no sessions: {data}")
elif kind == "status":
    tool = (data.get("tools") or {}).get("csep") or {}
    missing = [
        key for key in ("installed_version", "runtime_version", "source_version", "pypi_latest_version")
        if not tool.get(key)
    ]
    if missing:
        raise SystemExit(f"status missing CSEP version fields {missing}: {tool}")
else:
    raise SystemExit(f"unknown assertion kind: {kind}")
PY
}

require_command "$CSEP_BIN"
require_command "$PYTHON"
mkdir -p "$STATE_DIR"

log "using state dir: $STATE_DIR"
log "checking versions"
"$CSEP_BIN" --version

if [ "${CSEP_SMOKE_SKIP_CONFIG:-0}" != "1" ]; then
  log "validating config"
  "$CSEP_BIN" config validate >/dev/null
fi

log "checking status version matrix"
"$CSEP_BIN" status > "$STATE_DIR/status.json"
assert_json "$STATE_DIR/status.json" status

log "checking SessionStart hook output"
printf '{"cwd":"%s","session_id":"runtime-smoke-start"}\n' "$ROOT" \
  | "$CSEP_BIN" session-start --from-stdin --state-dir "$STATE_DIR" \
  > "$STATE_DIR/session-start.json"
assert_json "$STATE_DIR/session-start.json" session-start

log "creating smoke transcripts"
"$PYTHON" - "$STATE_DIR" "$ROOT" <<'PY'
import json
import sys
from pathlib import Path

state = Path(sys.argv[1])
root = Path(sys.argv[2])

codex_transcript = state / "codex-smoke.jsonl"
codex_transcript.write_text(
    "\n".join([
        json.dumps({"type": "session_meta", "payload": {"id": "runtime-smoke-codex", "cwd": str(root)}}),
        json.dumps({"role": "user", "content": "csep runtime smoke archive needle"}),
    ]) + "\n",
    encoding="utf-8",
)
(state / "codex-payload.json").write_text(
    json.dumps({
        "session_id": "runtime-smoke-codex",
        "cwd": str(root),
        "transcript_path": str(codex_transcript),
    }),
    encoding="utf-8",
)

claude_project = state / "claude" / "projects" / "-runtime-smoke"
claude_project.mkdir(parents=True, exist_ok=True)
(claude_project / "claude-smoke.jsonl").write_text(
    json.dumps({
        "type": "user",
        "sessionId": "runtime-smoke-claude",
        "uuid": "runtime-smoke-claude-u1",
        "cwd": str(root),
        "timestamp": "2026-05-17T00:00:00.000Z",
        "message": {"role": "user", "content": "csep runtime smoke claude needle"},
    }) + "\n",
    encoding="utf-8",
)
PY

log "checking archive + recall"
"$CSEP_BIN" session-archive --from-hook-payload "$STATE_DIR/codex-payload.json" --state-dir "$STATE_DIR" \
  > "$STATE_DIR/archive.json"
assert_json "$STATE_DIR/archive.json" archive
"$CSEP_BIN" recall "runtime smoke archive needle" --cwd "$ROOT" --state-dir "$STATE_DIR" --format json \
  > "$STATE_DIR/recall-codex.json"
assert_json "$STATE_DIR/recall-codex.json" recall

log "checking Claude Code sync + recall"
"$CSEP_BIN" recall sync-claude --root "$STATE_DIR/claude/projects" --state-dir "$STATE_DIR" --limit-files 1 --format json \
  > "$STATE_DIR/sync-claude.json"
assert_json "$STATE_DIR/sync-claude.json" claude-sync
"$CSEP_BIN" recall "runtime smoke claude needle" --cwd "$ROOT" --state-dir "$STATE_DIR" --format json \
  > "$STATE_DIR/recall-claude.json"
assert_json "$STATE_DIR/recall-claude.json" recall

log "runtime smoke passed"
