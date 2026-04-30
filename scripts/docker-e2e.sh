#!/usr/bin/env bash
set -euo pipefail

ROOT=/app
TMP_ROOT=$(mktemp -d)
REPO_DIR="$TMP_ROOT/repo"
STATE_DIR="$TMP_ROOT/state"
PAYLOAD_PATH="$TMP_ROOT/stop_payload.json"

mkdir -p "$REPO_DIR" "$STATE_DIR"

echo "[docker-e2e] running pytest"
python -m pytest -q

echo "[docker-e2e] session-start"
python -m codex_self_evolution.cli session-start --cwd "$REPO_DIR" --state-dir "$STATE_DIR" >/tmp/session-start.json

echo "[docker-e2e] building stop payload"
python - <<'PY' "$REPO_DIR" "$PAYLOAD_PATH"
import json
import sys
from pathlib import Path

repo_dir = Path(sys.argv[1])
payload_path = Path(sys.argv[2])
payload = {
    "thread_id": "docker-thread",
    "turn_id": "turn-1",
    "cwd": str(repo_dir),
    "transcript": "created a durable recall and skill for docker e2e",
    "thread_read_output": "repo specific detail from docker",
    "reviewer_provider": "dummy",
    "provider_stub_response": {
        "memory_updates": [
            {"summary": "User preference", "details": {"content": "Prefer concise summaries", "scope": "user"}},
            {"summary": "Keep pytest focused", "details": {"content": "Run focused pytest before full suite", "scope": "global"}}
        ],
        "recall_candidate": [
            {"summary": "Focused pytest", "details": {"content": "Run focused pytest before full suite", "source_paths": ["tests/test_end_to_end.py"]}}
        ],
        "skill_action": [
            {"summary": "Add test skill", "details": {"action": "create", "skill_id": "test-skill", "title": "Test Skill", "content": "Run focused tests before a broader regression pass."}}
        ]
    }
}
payload_path.write_text(json.dumps(payload), encoding="utf-8")
PY

echo "[docker-e2e] stop-review"
python -m codex_self_evolution.cli stop-review --hook-payload "$PAYLOAD_PATH" --state-dir "$STATE_DIR" >/tmp/stop-review.json

echo "[docker-e2e] compile-preflight"
python -m codex_self_evolution.cli compile-preflight --state-dir "$STATE_DIR" >/tmp/compile-preflight.json

echo "[docker-e2e] compile"
python -m codex_self_evolution.cli compile --once --state-dir "$STATE_DIR" --backend agent:opencode >/tmp/compile.json

echo "[docker-e2e] recall-trigger"
python -m codex_self_evolution.cli recall-trigger --query "remember focused pytest workflow" --cwd "$REPO_DIR" --state-dir "$STATE_DIR" --format json >/tmp/recall-trigger.json

echo "[docker-e2e] validating artifacts"
test -f "$STATE_DIR/memory/USER.md"
test -f "$STATE_DIR/memory/MEMORY.md"
test -f "$STATE_DIR/skills/managed/test-skill.md"
test -f "$STATE_DIR/compiler/last_receipt.json"

echo "[docker-e2e] success"
cat /tmp/compile.json
