# Phase 2 Plugin Hooks and Generated Skills Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Codex load this plugin's hooks through the plugin manifest and make generated `csep-*` skills load like normal Codex skills.

**Architecture:** Plugin hook wiring moves from user-level `~/.codex/hooks.json` injection to bundled plugin `hooks.json`. The installer remains, but only prepares a local `uv tool` CLI runtime and cleans old managed hook entries. Generated skills remain compiler-owned, get rendered as valid `SKILL.md` files with YAML frontmatter, and are projected directly under `~/.codex/skills/csep-*/SKILL.md`.

**Tech Stack:** Python 3.11 stdlib, pytest, Bash, `uv tool`, Codex plugin hooks, Codex core-skills loader smoke tests.

---

## Preconditions

- Execute in a dedicated worktree for this phase.
- Start from the current branch that contains `f344225 docs: add phase2 hooks and generated skills plan`.
- Use @systematic-debugging for any failing test that is not immediately explained by the current TDD step.
- Use @verification-before-completion before claiming implementation is complete.

Initial commands:

```bash
cd /Users/bytedance/code/github/codex-self-evolution-plugin
/usr/bin/git status --short --branch
.venv/bin/python -m pytest -q
```

Expected:

- Branch is clean before implementation starts.
- Existing tests pass.

---

### Task 1: Lock Plugin Hook Bundle Shape With Tests

**Files:**
- Create: `tests/test_plugin_bundle_hooks.py`
- Modify later: `.codex-plugin/plugin.json`
- Create later: `.codex-plugin/hooks.json`
- Modify later: `plugins/codex-self-evolution/.codex-plugin/plugin.json`
- Modify later: `plugins/codex-self-evolution/hooks.json`

**Step 1: Write the failing test**

Create `tests/test_plugin_bundle_hooks.py`:

```python
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_root_plugin_manifest_points_to_existing_hooks_file():
    manifest_path = ROOT / ".codex-plugin" / "plugin.json"
    manifest = _load_json(manifest_path)

    assert manifest["hooks"] == "./hooks.json"
    hooks_path = manifest_path.parent / "hooks.json"
    assert hooks_path.exists()


def test_plugin_hooks_use_local_cli_not_uvx_or_tmp_placeholders():
    hooks = _load_json(ROOT / ".codex-plugin" / "hooks.json")["hooks"]

    assert set(hooks) == {"SessionStart", "Stop"}
    session_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    stop_cmd = hooks["Stop"][0]["hooks"][0]["command"]

    assert session_cmd == "codex-self-evolution session-start --from-stdin"
    assert stop_cmd == "codex-self-evolution stop-review --from-stdin"
    assert "uvx" not in json.dumps(hooks)
    assert "/tmp/csep-" not in json.dumps(hooks)


def test_packaged_plugin_copy_matches_root_hook_bundle():
    root_manifest = _load_json(ROOT / ".codex-plugin" / "plugin.json")
    packaged_manifest = _load_json(
        ROOT / "plugins" / "codex-self-evolution" / ".codex-plugin" / "plugin.json"
    )
    root_hooks = _load_json(ROOT / ".codex-plugin" / "hooks.json")
    packaged_hooks = _load_json(ROOT / "plugins" / "codex-self-evolution" / "hooks.json")

    assert packaged_manifest == root_manifest
    assert packaged_hooks == root_hooks
```

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_bundle_hooks.py -q
```

Expected:

- FAIL because `.codex-plugin/hooks.json` does not exist and packaged hooks still use placeholder `/tmp` commands.

**Step 3: Implement minimal hook bundle**

Create `.codex-plugin/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "codex-self-evolution session-start --from-stdin",
            "timeout": 15
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "codex-self-evolution stop-review --from-stdin",
            "timeout": 20
          }
        ]
      }
    ]
  }
}
```

Modify both plugin manifests:

- `.codex-plugin/plugin.json`
- `plugins/codex-self-evolution/.codex-plugin/plugin.json`

Replace command entries and scheduler commands from `uvx --from ...` to local CLI commands:

```json
{
  "name": "session-start",
  "command": "codex-self-evolution session-start --from-stdin"
}
```

Use local CLI for the other commands too:

```json
"codex-self-evolution stop-review --from-stdin"
"codex-self-evolution compile-preflight --state-dir \"$CODEX_STATE_DIR\""
"codex-self-evolution compile --once --state-dir \"$CODEX_STATE_DIR\" --backend agent:opencode"
"codex-self-evolution scan --backend agent:opencode"
"codex-self-evolution status"
"csep recall \"$CODEX_RECALL_QUERY\" --cwd \"$CODEX_CWD\" --state-dir \"$CODEX_STATE_DIR\""
"codex-self-evolution recall-trigger --query \"$CODEX_RECALL_QUERY\" --cwd \"$CODEX_CWD\" --state-dir \"$CODEX_STATE_DIR\""
```

Copy `.codex-plugin/hooks.json` to `plugins/codex-self-evolution/hooks.json`.

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_bundle_hooks.py -q
```

Expected:

- PASS.

**Step 5: Commit**

```bash
/usr/bin/git add tests/test_plugin_bundle_hooks.py .codex-plugin/plugin.json .codex-plugin/hooks.json plugins/codex-self-evolution/.codex-plugin/plugin.json plugins/codex-self-evolution/hooks.json
/usr/bin/git commit -m "feat: use plugin-bundled hooks"
```

---

### Task 2: Replace Hook Installer With UV Tool Runtime Installer

**Files:**
- Create: `tests/test_install_script.py`
- Create: `scripts/install.sh`
- Modify: `scripts/install-codex-hook.sh`
- Modify: `scripts/uninstall-codex-hook.sh`

**Step 1: Write the failing test**

Create `tests/test_install_script.py`:

```python
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_install_script_uses_uv_tool_and_cleans_only_managed_hooks(tmp_path):
    fake_home = tmp_path / "home"
    fake_codex = fake_home / ".codex"
    fake_codex.mkdir(parents=True)
    hooks_json = fake_codex / "hooks.json"
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "third-party stop"}]},
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "bash -c ': codex-self-evolution-plugin managed; exec old-stop'",
                                }
                            ]
                        },
                    ],
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "bash -c ': codex-self-evolution-plugin managed; exec old-start'",
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        "echo \"$@\" >> \"$UV_LOG\"\n"
        "if [ \"$1\" = tool ] && [ \"$2\" = dir ] && [ \"$3\" = --bin ]; then echo \"$FAKE_TOOL_BIN\"; fi\n"
        "exit 0\n",
    )
    _write_executable(fake_bin / "codex-self-evolution", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "csep", "#!/usr/bin/env bash\nexit 0\n")

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "UV_LOG": str(uv_log),
        "FAKE_TOOL_BIN": str(fake_bin),
        "CSEP_INSTALL_SOURCE": str(ROOT),
    }

    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "install.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "tool install --force" in uv_log.read_text(encoding="utf-8")

    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert commands == ["third-party stop"]


def test_install_codex_hook_script_is_compatibility_wrapper():
    text = (ROOT / "scripts" / "install-codex-hook.sh").read_text(encoding="utf-8")
    assert "exec \"$REPO/scripts/install.sh\" \"$@\"" in text
    assert "upserting Stop + SessionStart hooks" not in text
```

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_install_script.py -q
```

Expected:

- FAIL because `scripts/install.sh` does not exist and `install-codex-hook.sh` still mutates `~/.codex/hooks.json`.

**Step 3: Implement `scripts/install.sh`**

Create `scripts/install.sh`:

```bash
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
data = json.loads(path.read_text(encoding="utf-8"))
hooks = data.get("hooks")
if isinstance(hooks, dict):
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            commands = [
                h.get("command", "")
                for h in entry.get("hooks", [])
                if isinstance(h, dict)
            ]
            if any(marker in command for command in commands):
                continue
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
```

Modify `scripts/install-codex-hook.sh` to become a compatibility wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "[warn] install-codex-hook.sh is deprecated; using scripts/install.sh" >&2
exec "$REPO/scripts/install.sh" "$@"
```

Modify `scripts/uninstall-codex-hook.sh` only if needed so it still removes old marker entries and does not assume new installs write hooks.

**Step 4: Run tests and shell syntax checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_install_script.py -q
bash -n scripts/install.sh scripts/install-codex-hook.sh scripts/uninstall-codex-hook.sh
```

Expected:

- PASS.

**Step 5: Commit**

```bash
/usr/bin/git add tests/test_install_script.py scripts/install.sh scripts/install-codex-hook.sh scripts/uninstall-codex-hook.sh
/usr/bin/git commit -m "feat: install local cli with uv"
```

---

### Task 3: Make Scheduler Use The Local CLI

**Files:**
- Modify: `scripts/install-scheduler.sh`
- Modify: `tests/test_scheduler_integration.py`

**Step 1: Write the failing test**

Add or update a test in `tests/test_scheduler_integration.py`:

```python
def test_scheduler_plist_uses_local_cli_not_uvx(tmp_path, monkeypatch):
    # Follow the existing scheduler test style in this file.
    # After installer generation, ProgramArguments must include local
    # codex-self-evolution and must not include uvx.
    ...
    assert "codex-self-evolution" in plist_text
    assert "scan" in plist_text
    assert "agent:opencode" in plist_text
    assert "uvx" not in plist_text
```

Use the existing helpers in `tests/test_scheduler_integration.py`; do not invent a separate plist parser if the file already has one.

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler_integration.py -q
```

Expected:

- FAIL because the scheduler script still uses `uvx --from codex-self-evolution-plugin`.

**Step 3: Update scheduler script**

In `scripts/install-scheduler.sh`, replace scheduled command generation with local CLI:

```bash
ENTRY_POINT="${CSEP_ENTRY_POINT:-codex-self-evolution}"
SCAN_ARGS=("scan" "--backend" "agent:opencode")
```

Ensure generated plist `ProgramArguments` is equivalent to:

```xml
<array>
  <string>codex-self-evolution</string>
  <string>scan</string>
  <string>--backend</string>
  <string>agent:opencode</string>
</array>
```

Keep `uv` install out of the scheduler script. Local CLI installation belongs to `scripts/install.sh`.

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_scheduler_integration.py -q
bash -n scripts/install-scheduler.sh scripts/uninstall-scheduler.sh
```

Expected:

- PASS.

**Step 5: Commit**

```bash
/usr/bin/git add scripts/install-scheduler.sh tests/test_scheduler_integration.py
/usr/bin/git commit -m "feat: run scheduler through local cli"
```

---

### Task 4: Report Plugin Hook Readiness In Diagnostics

**Files:**
- Modify: `src/codex_self_evolution/diagnostics.py`
- Modify: `tests/test_diagnostics.py`

**Step 1: Write the failing test**

Add tests to `tests/test_diagnostics.py`:

```python
def test_status_reports_plugin_hook_bundle_readiness():
    result = diagnostics._check_plugin_hook_bundle(
        Path("plugins/codex-self-evolution")
    )

    assert result["manifest_exists"] is True
    assert result["hooks_file_exists"] is True
    assert result["session_start_declared"] is True
    assert result["stop_declared"] is True
    assert result["uses_local_cli"] is True
    assert result["uses_uvx"] is False
```

Also update the existing collect-status shape test, if present, to expect:

```python
assert "plugin_hooks" in status
assert "legacy_user_hooks" in status
```

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_diagnostics.py -q
```

Expected:

- FAIL because `_check_plugin_hook_bundle` and new status keys do not exist.

**Step 3: Implement diagnostics**

In `src/codex_self_evolution/diagnostics.py`:

- Keep `_check_hooks()` read-only, but treat it as legacy user-hook status.
- Add `_check_plugin_hook_bundle(plugin_root: Path | None = None)`.
- Add both keys to `collect_status()`:

```python
return {
    "timestamp": ...,
    "home": str(home_dir),
    "legacy_user_hooks": _check_hooks(),
    "plugin_hooks": _check_plugin_hook_bundle(),
    ...
}
```

For backwards compatibility, either keep `"hooks": _check_hooks()` for one release or document the rename in the README update task. Prefer keeping `"hooks"` as an alias to avoid breaking current consumers:

```python
legacy_hooks = _check_hooks()
return {
    "hooks": legacy_hooks,
    "legacy_user_hooks": legacy_hooks,
    "plugin_hooks": _check_plugin_hook_bundle(),
    ...
}
```

`_check_plugin_hook_bundle()` should:

- read `plugins/codex-self-evolution/.codex-plugin/plugin.json`
- resolve its `hooks` path
- parse the hook file
- report SessionStart and Stop command strings
- set `uses_local_cli=True` when commands start with `codex-self-evolution `
- set `uses_uvx=True` when any command contains `uvx`
- never raise on missing or malformed files

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_diagnostics.py -q
```

Expected:

- PASS.

**Step 5: Commit**

```bash
/usr/bin/git add src/codex_self_evolution/diagnostics.py tests/test_diagnostics.py
/usr/bin/git commit -m "feat: report plugin hook readiness"
```

---

### Task 5: Render Valid SKILL.md Files

**Files:**
- Create: `src/codex_self_evolution/managed_skills/validation.py`
- Modify: `src/codex_self_evolution/managed_skills/publish.py`
- Modify: `tests/test_managed_skill_publish.py`

**Step 1: Write the failing tests**

Update `tests/test_managed_skill_publish.py`:

```python
def test_publish_global_skills_writes_valid_skill_frontmatter(tmp_path):
    result = publish_global_skills(
        [
            {
                "skill_id": "trace-debug",
                "title": "Trace Debug",
                "description": "This skill should be used when debugging repeated trace workflows with local command evidence.",
                "content": "## Workflow\n\n1. Inspect the trace id.\n2. Run the local log lookup command.\n3. Summarize the exact evidence.",
                "action": "create",
            }
        ],
        [_entry("trace-debug")],
        skills_root=tmp_path / "skills",
    )

    target = tmp_path / "skills" / "csep-trace-debug" / "SKILL.md"
    assert result["published"] == [str(target)]
    content = target.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "name: Trace Debug" in content
    assert "description: This skill should be used when debugging repeated trace workflows" in content
    assert "---\n\n# Trace Debug" in content
    assert "managed-by: codex-self-evolution-plugin" in content


def test_publish_global_skills_skips_missing_description(tmp_path):
    result = publish_global_skills(
        [
            {
                "skill_id": "thin",
                "title": "Thin",
                "content": "## Workflow\n\n1. Do something repeatable with evidence.",
                "action": "create",
            }
        ],
        [_entry("thin")],
        skills_root=tmp_path / "skills",
    )

    assert result["published"] == []
    assert result["skipped"][0]["reason"] == "missing_description"
```

Update the existing projection assertions from:

```python
tmp_path / "skills" / "csep-managed" / "csep-trace-debug" / "SKILL.md"
```

to:

```python
tmp_path / "skills" / "csep-trace-debug" / "SKILL.md"
```

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_managed_skill_publish.py -q
```

Expected:

- FAIL because publisher writes no YAML frontmatter and still uses `csep-managed/`.

**Step 3: Implement renderer and validator**

Create `src/codex_self_evolution/managed_skills/validation.py`:

```python
from __future__ import annotations

import re
from typing import Any


def validate_publishable_skill(item: dict[str, Any]) -> tuple[bool, str | None]:
    action = str(item.get("action") or "").strip().lower()
    if action == "retire":
        return True, None

    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()
    content = str(item.get("content") or "").strip()

    if not title:
        return False, "missing_title"
    if not description:
        return False, "missing_description"
    if "use" not in description.lower() and "when" not in description.lower():
        return False, "weak_description"
    words = [word for word in re.split(r"\s+", content) if word]
    if len(words) < 12:
        return False, "low_signal"
    if not any(marker in content.lower() for marker in ("workflow", "steps", "run ", "inspect", "verify", "check")):
        return False, "low_signal"
    return True, None
```

In `src/codex_self_evolution/managed_skills/publish.py`:

- import `validate_publishable_skill`
- change target dir from `root / "csep-managed" / global_id` to `root / global_id`
- render YAML frontmatter:

```python
def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_skill(title: str, source_skill_id: str, description: str, content: str) -> str:
    return (
        "---\n"
        f"name: {_yaml_quote(title.strip())}\n"
        f"description: {_yaml_quote(description.strip())}\n"
        "---\n\n"
        f"# {title.strip()}\n\n"
        "<!-- managed-by: codex-self-evolution-plugin; "
        f"source-skill-id: {source_skill_id}; do not edit by hand -->\n\n"
        f"{content.strip()}\n"
    )
```

Also remove old nested projections when publishing the new direct projection:

```python
old_target_dir = root / GLOBAL_NAMESPACE / global_id
if old_target_dir.exists():
    shutil.rmtree(old_target_dir)
```

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_managed_skill_publish.py -q
```

Expected:

- PASS.

**Step 5: Commit**

```bash
/usr/bin/git add src/codex_self_evolution/managed_skills/validation.py src/codex_self_evolution/managed_skills/publish.py tests/test_managed_skill_publish.py
/usr/bin/git commit -m "feat: render generated skills as codex skills"
```

---

### Task 6: Promote Skill Candidates From Memory And Recall Inputs

**Files:**
- Modify: `src/codex_self_evolution/compiler/skills.py`
- Modify: `tests/test_compiler_skills.py`

**Step 1: Write the failing tests**

Add to `tests/test_compiler_skills.py`:

```python
def test_compile_skills_accepts_skill_candidate_from_memory_update():
    suggestions = [
        Suggestion(
            family="memory_updates",
            summary="Trace workflow",
            details={
                "scope": "global",
                "content": "Repeated trace debugging workflow.",
                "skill_candidate": {
                    "skill_id": "trace-debug",
                    "title": "Trace Debug",
                    "description": "This skill should be used when debugging repeated trace workflows with local command evidence.",
                    "content": "## Workflow\n\n1. Inspect the trace id.\n2. Run the local log lookup command.\n3. Verify the evidence before answering.",
                },
            },
        )
    ]

    compiled, discarded = compile_skills(suggestions)

    assert discarded == []
    assert compiled[0]["skill_id"] == "trace-debug"
    assert compiled[0]["description"].startswith("This skill should be used when")


def test_compile_skills_accepts_skill_candidate_from_recall_candidate():
    suggestions = [
        Suggestion(
            family="recall_candidate",
            summary="Use focused recall",
            details={
                "content": "Recall should be queried for non-trivial repo tasks.",
                "skill_candidate": {
                    "skill_id": "focused-recall",
                    "title": "Focused Recall",
                    "description": "This skill should be used when a repo task may depend on prior local context.",
                    "content": "## Workflow\n\n1. Build a focused query.\n2. Run csep recall with the query.\n3. Use the result only as supporting context.",
                },
            },
        )
    ]

    compiled, discarded = compile_skills(suggestions)

    assert discarded == []
    assert compiled[0]["skill_id"] == "focused-recall"
```

Update the existing `skill_action` test to include `description`.

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_compiler_skills.py -q
```

Expected:

- FAIL because `compile_skills()` only reads `skill_action` suggestions.

**Step 3: Implement candidate extraction**

In `src/codex_self_evolution/compiler/skills.py`:

- Add `_candidate_from_skill_action(item: Suggestion)`.
- Add `_candidate_from_embedded_skill_candidate(item: Suggestion)`.
- Keep `skill_action` ownership semantics.
- For memory/recall sources, only promote when `details.skill_candidate` is a mapping. Do not infer a skill from every memory fact in the script backend.

Candidate shape:

```python
{
    "action": "create",
    "skill_id": "...",
    "title": "...",
    "description": "...",
    "content": "...",
}
```

Rules:

- Default embedded candidate action to `"create"`.
- Normalize `skill_id` through `_normalize_skill_id()`.
- Reject create/patch/edit when `description` or `content` is missing.
- Discard with reasons: `missing_description`, `missing_content`, `unsupported_action`, `low_signal`, `ownership_violation`.

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_compiler_skills.py -q
```

Expected:

- PASS.

**Step 5: Commit**

```bash
/usr/bin/git add src/codex_self_evolution/compiler/skills.py tests/test_compiler_skills.py
/usr/bin/git commit -m "feat: extract generated skill candidates"
```

---

### Task 7: Add Description To Agent Compile Contract

**Files:**
- Modify: `src/codex_self_evolution/compiler/agent_io.py`
- Modify: `src/codex_self_evolution/review/prompt.md`
- Modify: `tests/test_agent_compile_io.py`

**Step 1: Write the failing tests**

Update `tests/test_agent_compile_io.py` happy path:

```python
"compiled_skills": [
    {
        "skill_id": "alpha",
        "title": "Alpha",
        "description": "This skill should be used when alpha workflows repeat.",
        "content": "## Workflow\n\n1. Inspect alpha.\n2. Verify alpha.",
        "action": "create",
    }
],
```

Assert:

```python
assert result["compiled_skills"][0]["description"].startswith("This skill should be used")
```

Add a rejection test:

```python
def test_parse_agent_compile_response_rejects_create_skill_without_description():
    raw = json.dumps(
        {
            "compiled_skills": [
                {"skill_id": "x", "title": "X", "content": "## Workflow\n\n1. Do it.", "action": "create"}
            ]
        }
    )
    with pytest.raises(AgentResponseError):
        parse_agent_compile_response(raw)
```

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent_compile_io.py -q
```

Expected:

- FAIL because `_parse_compiled_skills()` drops or does not require `description`.

**Step 3: Implement schema change**

In `src/codex_self_evolution/compiler/agent_io.py`:

- Update `COMPILE_CONTRACT` to describe `CompiledSkill.description`.
- In `_parse_compiled_skills()`, require `description` for non-retire actions:

```python
description = str(item.get("description", "")).strip()
if action != "retire" and not description:
    raise AgentResponseError("compiled_skills create/edit/patch entries require description")
```

- Include `"description": description` in the normalized dict.

In `src/codex_self_evolution/review/prompt.md`, update `skill_action` details to include:

```markdown
- `description`: a concrete trigger description, preferably "This skill should be used when ..."
```

Also tell the reviewer/compiler that memory and recall candidates may include:

```markdown
- `skill_candidate`: optional object with `skill_id`, `title`, `description`, and `content`
```

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent_compile_io.py -q
```

Expected:

- PASS.

**Step 5: Commit**

```bash
/usr/bin/git add src/codex_self_evolution/compiler/agent_io.py src/codex_self_evolution/review/prompt.md tests/test_agent_compile_io.py
/usr/bin/git commit -m "feat: require skill trigger descriptions"
```

---

### Task 8: Update End-To-End Skill Projection

**Files:**
- Modify: `tests/test_end_to_end.py`
- Modify if needed: `src/codex_self_evolution/compiler/engine.py`
- Modify if needed: `src/codex_self_evolution/managed_skills/publish.py`

**Step 1: Update the failing end-to-end test**

In `tests/test_end_to_end.py`, update the seeded `skill_action` details:

```python
"skill_action": [
    {
        "summary": "Add test skill",
        "details": {
            "action": "create",
            "skill_id": "test-skill",
            "title": "Test Skill",
            "description": "This skill should be used when running focused tests before a broader regression pass.",
            "content": "## Workflow\n\n1. Run the smallest focused test first.\n2. Expand to the relevant suite.\n3. Report exact commands and results.",
        },
    }
],
```

Update projection assertion:

```python
assert (tmp_path / "codex-skills" / "csep-test-skill" / "SKILL.md").exists()
```

Add:

```python
skill_doc = (tmp_path / "codex-skills" / "csep-test-skill" / "SKILL.md").read_text(encoding="utf-8")
assert skill_doc.startswith("---\n")
assert "description:" in skill_doc
```

**Step 2: Run test to verify it fails if earlier tasks missed integration**

Run:

```bash
.venv/bin/python -m pytest tests/test_end_to_end.py -q
```

Expected:

- PASS if Tasks 5-7 are integrated correctly.
- If it fails, use the failure to fix integration only. Do not weaken the test.

**Step 3: Run adjacent tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_managed_skill_publish.py tests/test_compiler_skills.py tests/test_agent_compile_io.py tests/test_end_to_end.py -q
```

Expected:

- PASS.

**Step 4: Commit**

```bash
/usr/bin/git add tests/test_end_to_end.py src/codex_self_evolution/compiler/engine.py src/codex_self_evolution/managed_skills/publish.py
/usr/bin/git commit -m "test: cover generated skill projection end to end"
```

If `engine.py` or `publish.py` were not changed in this task, omit them from `git add`.

---

### Task 9: Update README And Getting Started Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/todo.md`
- Modify: `docs/implementation-plans/2026-04-30-phase2-plugin-hooks-and-generated-skills-plan.md`

**Step 1: Write the doc changes**

Update install sections to say:

```markdown
scripts/install.sh installs the local CLI with `uv tool install`.
It does not inject new entries into `~/.codex/hooks.json`; hooks are loaded
from the Codex plugin manifest.
```

Update generated skills documentation:

```markdown
Active generated skills are projected to:

`~/.codex/skills/csep-<skill-id>/SKILL.md`

They are managed copies. Edit source suggestions or retire the skill through
the compiler pipeline instead of editing projected files by hand.
```

Update stale references:

- remove "Stop + SessionStart hooks in `~/.codex/hooks.json`"
- replace `install-codex-hook.sh` primary install instructions with `scripts/install.sh`
- leave `install-codex-hook.sh` only as compatibility/deprecated
- update `csep-managed/csep-*` references to `csep-*`

**Step 2: Check docs for stale strings**

Run:

```bash
rg -n "install-codex-hook|csep-managed|~/.codex/hooks.json|uvx --from codex-self-evolution-plugin" README.md docs plugins .codex-plugin scripts
```

Expected:

- Remaining hits are either historical docs, compatibility script comments, or explicitly marked legacy/deprecated.

**Step 3: Commit**

```bash
/usr/bin/git add README.md docs/getting-started.md docs/todo.md docs/implementation-plans/2026-04-30-phase2-plugin-hooks-and-generated-skills-plan.md
/usr/bin/git commit -m "docs: update phase2 runtime docs"
```

---

### Task 10: Full Verification And Codex Smoke

**Files:**
- No source changes expected.
- If a smoke helper is created, add it under `scripts/`.

**Step 1: Run project test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected:

- PASS.

**Step 2: Run shell syntax checks**

Run:

```bash
bash -n scripts/install.sh scripts/install-codex-hook.sh scripts/uninstall-codex-hook.sh scripts/install-scheduler.sh scripts/uninstall-scheduler.sh
```

Expected:

- PASS with no output.

**Step 3: Run local Codex skill loader smoke**

Use the local Codex repo already refreshed to latest `main`:

```bash
cd /Users/bytedance/code/github/codex/codex-rs
cargo test -p codex-core-skills skills_for_cwd_loads_repo_user_and_extra_roots_with_local_fs
```

Expected:

- PASS.

Then create a temporary generated skill under the real Codex skill root:

```bash
SMOKE_DIR="$HOME/.codex/skills/csep-smoke-loader"
mkdir -p "$SMOKE_DIR"
cat > "$SMOKE_DIR/SKILL.md" <<'EOF'
---
name: CSEP Smoke Loader
description: This skill should be used when verifying generated CSEP skill loading.
---

# CSEP Smoke Loader

## Workflow

1. Confirm this generated skill appears in the Codex skills list.
2. Remove this smoke directory after verification.
EOF
```

Start a fresh Codex session and ask:

```text
$CSEP Smoke Loader
```

Expected:

- Codex loads the smoke skill body like any normal explicitly mentioned skill.

Cleanup:

```bash
rm -rf "$HOME/.codex/skills/csep-smoke-loader"
```

**Step 4: Run plugin hook smoke**

Enable features in the test Codex config:

```toml
[features]
plugins = true
codex_hooks = true
plugin_hooks = true

[plugins."codex-self-evolution@local"]
enabled = true
```

Use the real plugin install path that Codex expects. Then start a new Codex session and verify:

- `SessionStart` adds recall contract context.
- `Stop` writes pending suggestions.
- No new csep entries are injected into `~/.codex/hooks.json`.

**Step 5: Check final git state**

Run:

```bash
/usr/bin/git status --short --branch
/usr/bin/git log --oneline -8
```

Expected:

- Only intentional commits are present.
- Working tree is clean.

**Step 6: Commit smoke helper if added**

If a helper script was added:

```bash
/usr/bin/git add scripts/<helper-name>.sh
/usr/bin/git commit -m "test: add phase2 smoke helper"
```

---

## Final Completion Criteria

Phase 2 is complete only when:

- Plugin hook definitions live in the plugin bundle and use local CLI commands.
- `scripts/install.sh` uses `uv tool install` and does not inject new hooks.
- Old csep user-level hooks are removed safely by marker.
- Scheduler uses the local CLI.
- Diagnostics reports plugin hook readiness.
- Generated skills render valid YAML-frontmatter `SKILL.md` files.
- Active generated skills project to `~/.codex/skills/csep-*/SKILL.md`.
- Low-signal or missing-description generated skills do not publish.
- Memory and recall inputs can carry skill candidates into the compiler.
- Project tests pass.
- Local Codex skill loader smoke passes.
- Manual Codex CLI smoke confirms generated skill loading.
