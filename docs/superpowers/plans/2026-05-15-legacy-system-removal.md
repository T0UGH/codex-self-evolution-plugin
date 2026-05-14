# Legacy System Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除旧 reviewer / compiler / skill synthesis / compiler recall 链路，只保留 session-start、session-stop、session reflection、session recall 和 `csep recall`。

**Architecture:** 先收 CLI 和 plugin manifest 的对外入口，让旧命令不可见；再收内部依赖，把旧包、旧 config、旧 status 字段和旧 tests 删除；最后清 README / getting-started / pyproject / scripts，并用 grep 验证当前系统面没有 legacy 命中。`csep recall` 只走 `session_recall` SQLite/FTS，不再 fallback 到 `recall/index.json`。

**Tech Stack:** Python 3.11+ stdlib, argparse, pytest, TOML via `tomllib`, Codex plugin manifest JSON, local `uv run pytest -q` test entrypoint.

---

## Scope Check

这份计划实现 `docs/superpowers/specs/2026-05-15-legacy-review-compile-synthesis-removal-design.md`。虽然涉及 CLI、config、status、docs、tests 多个面，但它们围绕一个目标：删除旧系统并收口到新 session 级链路。不要在这份计划里实现新的 trigger policy 行为；那已经有独立 plan。

## File Structure

### 保留并修改

- `src/codex_self_evolution/cli.py`：保留 `session-start`、新增 `session-stop`、保留 `session-reflect` / `status` / `config` / `migrate-worktrees`，删除旧命令和旧 imports。
- `src/codex_self_evolution/csep.py`：保留短命令 `recall`、`session-archive`、`session-ingest`，但 `recall` 只调用 session recall workflow。
- `src/codex_self_evolution/hooks/session_start.py`：继续注入 memory 和 recall contract，但 recall 文档路径迁到 `session_recall/`。
- `src/codex_self_evolution/session_recall/workflow.py`：新增，从当前 `recall/workflow.py` 拆出并删除旧 `search_recall()` fallback。
- `src/codex_self_evolution/session_recall/policy.md`：由 `recall/policy.md` 移入。
- `src/codex_self_evolution/session_recall/session_recall.md`：由 `recall/session_recall.md` 移入。
- `src/codex_self_evolution/session_reflection/runner.py`：把 `codex_skills_dir()` helper 来源从 `managed_skills.publish` 改到新 helper。
- `src/codex_self_evolution/skill_paths.py`：新增，只负责解析 Codex skills 根目录。
- `src/codex_self_evolution/config.py`：删除旧 scheduler / skill synthesis / managed skill / suggestions path 常量和 `Paths` 字段。
- `src/codex_self_evolution/config_file.py`：删除 reviewer / compile / scheduler / skill_synthesis schema 和 legacy env override，只保留新系统 config。
- `src/codex_self_evolution/config_file_template.py`：删除旧配置段。
- `src/codex_self_evolution/diagnostics.py`：删除 scheduler、skill synthesis、suggestion bucket、compiler receipt、old recent activity 汇总。
- `src/codex_self_evolution/storage.py`：删除 suggestion queue 状态机，只保留 atomic write/read、repo fingerprint、memory file helpers。
- `plugins/codex-self-evolution/.codex-plugin/{plugin.json,hooks.json}`：对外命令收口。
- `.codex-plugin/{plugin.json,hooks.json}`：根 manifest 同步。
- `src/codex_self_evolution/plugin_bundle/.codex-plugin/{plugin.json,hooks.json}`：打包 manifest 同步。
- `pyproject.toml`：删除旧 package data、更新描述。
- `Makefile`：删除 `preflight` target。
- `README.md`、`docs/getting-started.md`、`docs/2026-05-15-memory-skill-session-recall-architecture.html`：当前入口文档清理 legacy 说明。

### 删除

- `src/codex_self_evolution/review/`
- `src/codex_self_evolution/compiler/`
- `src/codex_self_evolution/skill_synthesis/`
- `src/codex_self_evolution/managed_skills/`
- `src/codex_self_evolution/hooks/stop_review.py`
- `src/codex_self_evolution/hooks/codex_bridge.py`
- `src/codex_self_evolution/recall/`
- `scripts/install-scheduler.sh`
- `scripts/uninstall-scheduler.sh`
- `scripts/install-skill-synthesis-scheduler.sh`
- `scripts/uninstall-skill-synthesis-scheduler.sh`
- `scripts/docker-e2e.sh`
- `tests/fixtures/compiler_replay/`
- 旧 reviewer / compiler / scheduler / skill synthesis / compiler recall 测试文件。

### 测试文件收口

- `tests/test_session_reflection_cli.py`：改 `stop-review` 为 `session-stop`，补旧命令非法断言。
- `tests/test_plugin_bundle_hooks.py`：manifest 只允许新命令。
- `tests/test_csep_cli.py`：用 `session-archive` 建库后测 `csep recall`，不再写 `recall/index.json`。
- `tests/test_session_recall_cli.py`：保留；该文件已经通过 `csep` 入口覆盖 session recall。
- `tests/test_config_file.py`：重写成新 config schema 测试。
- `tests/test_cli_config.py`：删除 profile / migrate legacy 分支，保留 `path` / `init` / `show` / `validate`。
- `tests/test_diagnostics.py`、`tests/test_session_reflection_diagnostics.py`：删旧 status 字段断言，新增无 legacy keys 断言。
- `tests/test_session_start.py`、`tests/test_session_start_codex_hook.py`：更新 recall contract 路径和 runtime 字段断言。

## Implementation Tasks

### Task 1: CLI Front Door Rename And Legacy Command Removal

**Files:**
- Modify: `src/codex_self_evolution/cli.py`
- Modify: `tests/test_session_reflection_cli.py`
- Delete: `tests/test_stop_review.py`

- [ ] **Step 1: Write failing tests for `session-stop` and removed legacy commands**

Replace the `stop-review` invocations in `tests/test_session_reflection_cli.py` with `session-stop`, and add this test at the end of the file:

```python
def test_legacy_cli_commands_are_removed() -> None:
    """Legacy reviewer/compiler/synthesis/old recall commands are not registered."""
    removed = [
        "stop-review",
        "compile",
        "compile-preflight",
        "scan",
        "skill-synthesize",
        "eval-compiler",
        "recall",
        "recall-trigger",
    ]
    for command in removed:
        with pytest.raises(SystemExit) as exc:
            cli.main([command])
        assert exc.value.code == 2
```

Also rename the first test function and invocation:

```python
def test_session_stop_from_stdin_spawns_session_reflect_job(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Queued Stop payloads spawn a detached session-reflect worker."""
    captured: dict[str, Any] = {}

    def fake_enqueue(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
        captured["payload"] = payload
        captured["home"] = home
        return {"status": "queued", "job_id": "job-123"}

    class FakePopen:
        """Capture subprocess construction without starting a real worker."""

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            """Record argv and detach options passed by the hook."""
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(cli, "enqueue_reflection_from_payload", fake_enqueue)
    monkeypatch.setattr(cli, "_spawn_session_archive_from_stop_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_codex_payload())))

    exit_code = cli.main(["session-stop", "--from-stdin", "--state-dir", "/tmp/csep-home"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    assert captured["payload"]["session_id"] == "parent-1"
    assert captured["home"] == "/tmp/csep-home"
    assert captured["argv"][:6] == [
        sys.executable,
        "-m",
        "codex_self_evolution.cli",
        "session-reflect",
        "--job",
        "job-123",
    ]
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_cli.py
```

Expected before implementation: failures because `session-stop` is unknown and `stop-review` still exists.

- [ ] **Step 3: Remove legacy parser entries and add `session-stop`**

In `src/codex_self_evolution/cli.py`, delete these imports:

```python
from .compiler.engine import preflight_compile, run_compile, scan_all_projects
from .compiler.replay import evaluate_compiler_fixture
from .config import DEFAULT_SCAN_MAX_RUNS_PER_PROJECT
from .hooks.stop_review import stop_review
from .recall.search import search_recall
from .recall.workflow import (
    evaluate_session_recall,
    render_focused_recall_markdown,
)
from .skill_synthesis.runner import run_skill_synthesis
```

Then add this parser block where `stop_parser = subparsers.add_parser("stop-review")` currently lives:

```python
    stop_parser = subparsers.add_parser("session-stop")
    stop_parser.add_argument("--state-dir")
    stop_parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read a Codex native Stop hook JSON payload from stdin, archive the session, "
             "evaluate the deterministic reflection trigger policy, enqueue a session-reflection "
             "job when needed, and emit {\"continue\": true}.",
    )
```

Delete the parser blocks for:

```text
compile
compile-preflight
scan
skill-synthesize
recall
recall-trigger
eval-compiler
```

- [ ] **Step 4: Rename Stop handler and dispatch**

Rename `_handle_stop_from_stdin` to `_handle_session_stop_from_stdin`, keep its body, and update the `main()` dispatch to:

```python
        elif args.command == "session-stop":
            if args.from_stdin:
                exit_code = _handle_session_stop_from_stdin(args)
                _log_command(logger, args.command, started, exit_code=exit_code, mode="from_stdin")
                return exit_code
            parser.error("session-stop requires --from-stdin")
```

Delete these dispatch branches completely:

```python
        elif args.command == "stop-review":
            ...
        elif args.command == "compile":
            ...
        elif args.command == "compile-preflight":
            ...
        elif args.command == "scan":
            ...
        elif args.command == "skill-synthesize":
            ...
        elif args.command == "recall":
            ...
        elif args.command == "recall-trigger":
            ...
        elif args.command == "eval-compiler":
            ...
```

Delete helper functions that only served removed commands:

```text
_run_stop_review
_compile_runtime_options
_render_v2_from_loaded
_render_migrated_toml
```

Keep `_spawn_session_archive_from_stop_payload`, `_handle_session_start_from_stdin`, `_handle_session_reflect`, config subcommand helpers, TOML escaping helpers if still used by config init/show.

- [ ] **Step 5: Simplify observability extras**

Replace `_observability_extras()` with:

```python
def _observability_extras(command: str | None, result: object) -> dict:
    """Return compact per-command log extras for retained commands only."""
    if not isinstance(result, dict):
        return {}
    if command == "session-stop":
        return {"mode": "from_stdin"}
    if command == "session-reflect":
        status = result.get("status")
        job_id = result.get("job_id")
        extras: dict[str, object] = {}
        if status:
            extras["status"] = status
        if job_id:
            extras["job_id"] = job_id
        return extras
    return {}
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_cli.py
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/codex_self_evolution/cli.py tests/test_session_reflection_cli.py
git commit -m "feat: replace stop-review cli with session-stop"
```

### Task 2: Plugin Manifest Command Surface

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `.codex-plugin/hooks.json`
- Modify: `plugins/codex-self-evolution/.codex-plugin/plugin.json`
- Modify: `plugins/codex-self-evolution/.codex-plugin/hooks.json`
- Modify: `src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json`
- Modify: `src/codex_self_evolution/plugin_bundle/.codex-plugin/hooks.json`
- Modify: `tests/test_plugin_bundle_hooks.py`

- [ ] **Step 1: Write failing manifest tests**

Update `tests/test_plugin_bundle_hooks.py::test_plugin_hooks_use_local_cli_not_uvx_or_tmp_placeholders` so Stop expects `session-stop`:

```python
    assert stop_cmd == "codex-self-evolution session-stop --from-stdin"
```

Replace `test_plugin_manifest_commands_use_local_cli_not_uvx` command assertion with:

```python
    commands = {entry["name"]: entry["command"] for entry in manifest["commands"]}
    assert commands == {
        "session-start": "codex-self-evolution session-start --from-stdin",
        "session-stop": "codex-self-evolution session-stop --from-stdin",
        "status": "codex-self-evolution status",
        "session-reflect-status": "codex-self-evolution session-reflect --status",
        "recall": (
            'csep recall "$CODEX_RECALL_QUERY" --cwd "$CODEX_CWD" '
            '--state-dir "$CODEX_STATE_DIR"'
        ),
    }
    assert "scheduler" not in manifest
    assert "stop-review" not in json.dumps(manifest)
    assert "compile-preflight" not in json.dumps(manifest)
    assert "skill-synthesize" not in json.dumps(manifest)
```

Delete `test_plugin_manifest_exposes_skill_synthesize_command`.

Update `test_default_plugin_root_falls_back_to_package_bundle` fixture hook command to:

```python
"command": "codex-self-evolution session-stop --from-stdin",
```

- [ ] **Step 2: Run manifest tests and see them fail**

Run:

```bash
uv run pytest -q tests/test_plugin_bundle_hooks.py
```

Expected before manifest edit: failures showing `stop-review` and scheduler fields still present.

- [ ] **Step 3: Rewrite all three `hooks.json` files**

All three hooks files must have this Stop command:

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
            "command": "codex-self-evolution session-stop --from-stdin",
            "timeout": 20
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Rewrite all three `plugin.json` files**

Use the same JSON shape in root, packaged, and source bundle manifests:

```json
{
  "name": "codex-self-evolution",
  "version": "0.7.8",
  "description": "Session-level self-evolution loop with stable memory, session reflection, focused session recall, and csep-reflect skill generation.",
  "author": {
    "name": "T0UGH",
    "url": "https://github.com/T0UGH"
  },
  "homepage": "https://github.com/T0UGH/codex-self-evolution-plugin",
  "license": "MIT",
  "commands": [
    {
      "name": "session-start",
      "command": "codex-self-evolution session-start --from-stdin"
    },
    {
      "name": "session-stop",
      "command": "codex-self-evolution session-stop --from-stdin"
    },
    {
      "name": "status",
      "command": "codex-self-evolution status"
    },
    {
      "name": "session-reflect-status",
      "command": "codex-self-evolution session-reflect --status"
    },
    {
      "name": "recall",
      "command": "csep recall \"$CODEX_RECALL_QUERY\" --cwd \"$CODEX_CWD\" --state-dir \"$CODEX_STATE_DIR\""
    }
  ],
  "hooks": "./.codex-plugin/hooks.json"
}
```

- [ ] **Step 5: Run manifest tests**

Run:

```bash
uv run pytest -q tests/test_plugin_bundle_hooks.py
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add .codex-plugin/plugin.json .codex-plugin/hooks.json \
  plugins/codex-self-evolution/.codex-plugin/plugin.json \
  plugins/codex-self-evolution/.codex-plugin/hooks.json \
  src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json \
  src/codex_self_evolution/plugin_bundle/.codex-plugin/hooks.json \
  tests/test_plugin_bundle_hooks.py
git commit -m "feat: expose only session reflection plugin commands"
```

### Task 3: Session Recall Only, No Compiler Recall Fallback

**Files:**
- Create: `src/codex_self_evolution/session_recall/workflow.py`
- Move: `src/codex_self_evolution/recall/policy.md` -> `src/codex_self_evolution/session_recall/policy.md`
- Move: `src/codex_self_evolution/recall/session_recall.md` -> `src/codex_self_evolution/session_recall/session_recall.md`
- Modify: `src/codex_self_evolution/csep.py`
- Modify: `src/codex_self_evolution/hooks/session_start.py`
- Delete: `src/codex_self_evolution/recall/search.py`
- Delete: `src/codex_self_evolution/recall/workflow.py`
- Delete: `src/codex_self_evolution/recall/__init__.py`
- Modify: `tests/test_csep_cli.py`
- Delete: `tests/test_recall_search.py`

- [ ] **Step 1: Replace old csep recall tests**

Replace `tests/test_csep_cli.py` with:

```python
import json

from codex_self_evolution import csep


def test_csep_recall_defaults_to_markdown_from_session_recall(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "pytest workflow from sqlite recall"}) + "\n",
        encoding="utf-8",
    )

    assert csep.main([
        "session-archive",
        "--transcript-path",
        str(transcript),
        "--cwd",
        str(repo),
        "--session-id",
        "s1",
    ]) == 0
    capsys.readouterr()

    exit_code = csep.main(["recall", "pytest workflow", "--cwd", str(repo)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("## Focused Recall")
    assert "Status: matched" in out
    assert "pytest workflow from sqlite recall" in out


def test_csep_recall_does_not_fallback_to_legacy_recall_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    legacy_recall = state / "recall"
    legacy_recall.mkdir(parents=True)
    (legacy_recall / "index.json").write_text(
        json.dumps({
            "records": [
                {
                    "id": "legacy",
                    "summary": "legacy pytest workflow",
                    "content": "this must not be returned",
                    "source_paths": [],
                    "repo_fingerprint": "legacy",
                    "cwd": str(repo),
                }
            ]
        }),
        encoding="utf-8",
    )

    exit_code = csep.main([
        "recall",
        "legacy pytest workflow",
        "--cwd",
        str(repo),
        "--state-dir",
        str(state),
        "--format",
        "json",
    ])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "no_match"
    assert out["count"] == 0
    assert out["results"] == []
```

- [ ] **Step 2: Run failing recall tests**

Run:

```bash
uv run pytest -q tests/test_csep_cli.py tests/test_session_recall_cli.py
```

Expected before implementation: first test may pass, second fails because fallback reads `recall/index.json`.

- [ ] **Step 3: Create `session_recall.workflow` without fallback**

Create `src/codex_self_evolution/session_recall/workflow.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config_file import load_config
from .archive import default_db_path
from .render import budget_payload, render_markdown
from .repo import collect_repo_metadata
from .store import SessionRecallStore


def build_focused_recall(
    query: str,
    cwd: str | Path,
    state_dir: str | Path | None = None,
    top_k: int = 3,
    *,
    global_scope: bool = False,
    recent: bool = False,
    before: int = 3,
    after: int = 5,
    budget_chars: int = 12000,
    message_chars: int = 1200,
    tool_message_chars: int = 600,
    current_session_id: str = "",
) -> dict[str, Any]:
    """Build model-readable recall from the session_recall SQLite/FTS store only."""
    config = load_config().config
    if not config.session_recall.enabled:
        return _empty_payload(query=query, scope="global" if global_scope else "repo")

    db_path = default_db_path(state_dir=state_dir)
    if not db_path.exists():
        return _empty_payload(query=query, scope="global" if global_scope else "repo")

    meta = collect_repo_metadata(cwd)
    store = SessionRecallStore(db_path)
    try:
        if recent:
            session_results = store.recent(
                repo_fingerprint=meta["repo_fingerprint"],
                global_scope=global_scope,
                limit=top_k,
            )
            payload = budget_payload(
                query=query,
                scope="global" if global_scope else "repo",
                results=session_results,
                budget_chars=budget_chars,
                message_chars=message_chars,
                tool_message_chars=tool_message_chars,
                recent=True,
            )
            payload["recent"] = True
            return payload
        session_results = store.search(
            query=query,
            repo_fingerprint=meta["repo_fingerprint"],
            global_scope=global_scope,
            limit=top_k,
            before=before,
            after=after,
            current_session_id=current_session_id,
        )
        return budget_payload(
            query=query,
            scope="global" if global_scope else "repo",
            results=session_results,
            budget_chars=budget_chars,
            message_chars=message_chars,
            tool_message_chars=tool_message_chars,
        )
    finally:
        store.close()


def render_focused_recall_markdown(payload: dict[str, Any]) -> str:
    """Render session recall output for direct model consumption."""
    return render_markdown(payload)


def _empty_payload(*, query: str, scope: str) -> dict[str, Any]:
    return {
        "query": query,
        "scope": scope,
        "count": 0,
        "results": [],
        "focused_recall": "",
        "status": "no_match",
        "budget": {
            "budget_chars": 0,
            "used_chars": 0,
            "truncated": False,
            "truncation_reason": "",
        },
    }
```

- [ ] **Step 4: Update imports and docs paths**

In `src/codex_self_evolution/csep.py`, change:

```python
from .recall.workflow import build_focused_recall, render_focused_recall_markdown
```

to:

```python
from .session_recall.workflow import build_focused_recall, render_focused_recall_markdown
```

Move the markdown files:

```bash
mkdir -p src/codex_self_evolution/session_recall
git mv src/codex_self_evolution/recall/policy.md src/codex_self_evolution/session_recall/policy.md
git mv src/codex_self_evolution/recall/session_recall.md src/codex_self_evolution/session_recall/session_recall.md
```

In `src/codex_self_evolution/hooks/session_start.py`, replace:

```python
policy = (PACKAGE_ROOT / "recall" / "policy.md").read_text(encoding="utf-8")
session_recall_skill = (PACKAGE_ROOT / "recall" / "session_recall.md").read_text(encoding="utf-8")
```

with:

```python
policy = (PACKAGE_ROOT / "session_recall" / "policy.md").read_text(encoding="utf-8")
session_recall_skill = (PACKAGE_ROOT / "session_recall" / "session_recall.md").read_text(encoding="utf-8")
```

- [ ] **Step 5: Delete old recall package**

Delete:

```bash
rm -f src/codex_self_evolution/recall/search.py
rm -f src/codex_self_evolution/recall/workflow.py
rm -f src/codex_self_evolution/recall/__init__.py
rmdir src/codex_self_evolution/recall
rm -f tests/test_recall_search.py
```

- [ ] **Step 6: Run focused recall tests**

Run:

```bash
uv run pytest -q tests/test_csep_cli.py tests/test_session_recall_cli.py tests/test_session_start.py tests/test_session_start_codex_hook.py
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/codex_self_evolution/csep.py \
  src/codex_self_evolution/hooks/session_start.py \
  src/codex_self_evolution/session_recall/workflow.py \
  src/codex_self_evolution/session_recall/policy.md \
  src/codex_self_evolution/session_recall/session_recall.md \
  tests/test_csep_cli.py tests/test_session_recall_cli.py \
  tests/test_session_start.py tests/test_session_start_codex_hook.py
git add -u src/codex_self_evolution/recall tests/test_recall_search.py
git commit -m "feat: use session recall as the only recall backend"
```

### Task 4: Config Schema Cleanup

**Files:**
- Modify: `src/codex_self_evolution/config_file.py`
- Modify: `src/codex_self_evolution/config_file_template.py`
- Modify: `src/codex_self_evolution/cli.py`
- Modify: `tests/test_config_file.py`
- Modify: `tests/test_cli_config.py`
- Delete: `tests/test_skill_synthesis_config.py`
- Delete: `tests/test_api_key_env.py`

- [ ] **Step 1: Replace config schema tests**

Replace the default assertions in `tests/test_config_file.py` with this minimal retained-schema suite:

```python
from pathlib import Path

import pytest

from codex_self_evolution.config_file import ConfigError, config_to_dict, get_config_path, load_config


def _write_config(home: Path, toml: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(toml, encoding="utf-8")
    return path


def test_missing_config_returns_new_system_defaults(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config_exists is False
    assert loaded.config.schema_version == 2
    assert loaded.config.session_reflection.enabled is True
    assert loaded.config.session_reflection.backend == "codex-app-server"
    assert loaded.config.session_reflection.skill_prefix == "csep-reflect-"
    assert loaded.config.session_recall.enabled is True
    assert loaded.config.session_recall.stop_hook_archive is True
    assert loaded.sources["session_reflection.enabled"] == "default"
    assert loaded.sources["session_recall.enabled"] == "default"
    assert loaded.warnings == []


def test_toml_values_apply_to_new_system_sections(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2

[session_reflection]
enabled = false
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = false
sandbox = "workspace-write"
approval_policy = "on-request"
skill_prefix = "csep-reflect-"
timeout_seconds = 120
max_concurrent_jobs = 1

[session_reflection.trigger]
enabled = false
memory_stop_interval = 5
memory_context_chars = 12000
skill_tool_call_interval = 20
high_signal_immediate = false
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 600

[session_recall]
enabled = false
stop_hook_archive = false

[log]
retention_days = 3
""")
    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config
    assert cfg.session_reflection.enabled is False
    assert cfg.session_reflection.ephemeral is False
    assert cfg.session_reflection.sandbox == "workspace-write"
    assert cfg.session_reflection.approval_policy == "on-request"
    assert cfg.session_reflection.timeout_seconds == 120.0
    assert cfg.session_reflection.trigger.enabled is False
    assert cfg.session_reflection.trigger.memory_stop_interval == 5
    assert cfg.session_reflection.trigger.skill_tool_call_interval == 20
    assert cfg.session_recall.enabled is False
    assert cfg.session_recall.stop_hook_archive is False
    assert cfg.log.retention_days == 3


def test_legacy_sections_warn_and_do_not_appear_in_resolved_config(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer]
provider = "minimax"

[compile]
backend = "agent:pi"

[scheduler]
backend = "agent:pi"

[skill_synthesis]
enabled = true
""")
    loaded = load_config(home=tmp_path, env={})
    resolved = config_to_dict(loaded.config)
    assert "reviewer" not in resolved
    assert "compile" not in resolved
    assert "scheduler" not in resolved
    assert "skill_synthesis" not in resolved
    assert any("unknown top-level config key: reviewer" in warning for warning in loaded.warnings)
    assert any("unknown top-level config key: compile" in warning for warning in loaded.warnings)
    assert any("unknown top-level config key: scheduler" in warning for warning in loaded.warnings)
    assert any("unknown top-level config key: skill_synthesis" in warning for warning in loaded.warnings)


def test_invalid_session_reflection_skill_prefix_warns(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[session_reflection]
skill_prefix = "csep-synth-"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.session_reflection.skill_prefix == "csep-synth-"
    assert any("session_reflection.skill_prefix" in warning for warning in loaded.warnings)
```

- [ ] **Step 2: Run config tests and see failures**

Run:

```bash
uv run pytest -q tests/test_config_file.py tests/test_cli_config.py
```

Expected before implementation: many failures because old config fields still exist.

- [ ] **Step 3: Reduce config dataclasses**

In `src/codex_self_evolution/config_file.py`, delete these dataclasses:

```text
SubprocessReviewerConfig
ReviewerConfig
OpencodeCompileConfig
PiCompileConfig
CompileConfig
SchedulerConfig
SkillSynthesisAgentConfig
SkillSynthesisConfig
```

Change `PluginConfig` to:

```python
@dataclass
class PluginConfig:
    """Resolved runtime configuration for the retained session-level system."""

    schema_version: int = 2
    session_reflection: SessionReflectionConfig = field(default_factory=SessionReflectionConfig)
    session_recall: SessionRecallConfig = field(default_factory=SessionRecallConfig)
    log: LogConfig = field(default_factory=LogConfig)
```

Delete constants that only validate removed sections:

```text
ALLOWED_PROVIDERS
ALLOWED_PAYLOAD_MODES
ALLOWED_RESPONSE_FORMATS
ALLOWED_COMPILE_BACKENDS
ALLOWED_SKILL_SYNTHESIS_BACKENDS
ALLOWED_SKILL_SYNTHESIS_MODES
_NEW_ENV_MAP entries for reviewer/compile
legacy provider env maps
```

Keep:

```python
ALLOWED_SESSION_REFLECTION_BACKENDS = {"codex-app-server"}
ALLOWED_SESSION_REFLECTION_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
ALLOWED_SESSION_REFLECTION_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}
ALLOWED_SESSION_REFLECTION_TRIGGER_MODES = {"one_shot_active", "evidence_first"}
```

- [ ] **Step 4: Delete legacy load branches**

Inside `load_config()`, delete parse blocks for:

```text
schema 1 reviewer lifting
profiles
reviewer
compile
scheduler
skill_synthesis
legacy env overrides
```

Retain and test only:

```text
schema_version
session_reflection
session_reflection.trigger
session_recall
log
unknown key warnings
api-key-shaped value warnings
```

The top-level known keys list must be:

```python
_KNOWN_TOP_LEVEL_KEYS = {
    "schema_version",
    "session_reflection",
    "session_recall",
    "log",
}
```

- [ ] **Step 5: Remove obsolete config CLI subcommands**

In `src/codex_self_evolution/cli.py`, delete config subcommands:

```text
use
list-profiles
migrate-to-v2
migrate-from-env
```

Keep:

```text
config path
config init
config show
config validate
```

In `_handle_config_subcommand()`, delete branches for removed subcommands.

- [ ] **Step 6: Update config template**

In `src/codex_self_evolution/config_file_template.py`, remove old sections and keep only:

```toml
# ~/.codex-self-evolution/config.toml

schema_version = 2

[session_reflection]
enabled = true
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = true
sandbox = "danger-full-access"
approval_policy = "never"
skill_prefix = "csep-reflect-"
timeout_seconds = 900
max_concurrent_jobs = 1

[session_reflection.trigger]
enabled = true
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 1800

[session_recall]
enabled = true
stop_hook_archive = true

[log]
retention_days = 14
```

- [ ] **Step 7: Run config tests**

Run:

```bash
uv run pytest -q tests/test_config_file.py tests/test_cli_config.py tests/test_session_reflection_config.py
```

Expected: pass after updating tests to new schema.

- [ ] **Step 8: Commit**

```bash
git add src/codex_self_evolution/config_file.py src/codex_self_evolution/config_file_template.py \
  src/codex_self_evolution/cli.py tests/test_config_file.py tests/test_cli_config.py \
  tests/test_session_reflection_config.py
git add -u tests/test_skill_synthesis_config.py tests/test_api_key_env.py
git commit -m "feat: remove legacy reviewer compiler config"
```

### Task 5: Status And Diagnostics Cleanup

**Files:**
- Modify: `src/codex_self_evolution/diagnostics.py`
- Create: `src/codex_self_evolution/skill_paths.py`
- Modify: `src/codex_self_evolution/session_reflection/runner.py`
- Modify: `tests/test_diagnostics.py`
- Modify: `tests/test_session_reflection_diagnostics.py`
- Delete: `tests/test_diagnostics_skill_synthesis.py`

- [ ] **Step 1: Add focused status tests**

Add this test to `tests/test_diagnostics.py`:

```python
def test_status_contains_only_retained_runtime_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Status no longer reports scheduler, compiler queues, or skill synthesis."""
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    status = diagnostics.collect_status(home=tmp_path)

    assert "scheduler" not in status
    assert "skill_synthesis" not in status
    assert "buckets" not in status
    assert "recent_activity" not in status
    assert "skills" not in status
    assert "session_reflection" in status
    assert "session_recall" in status
    assert "plugin_hooks" in status
```

Update plugin hook diagnostics expectations so Stop command is:

```python
assert result["stop_command"] == "codex-self-evolution session-stop --from-stdin"
```

- [ ] **Step 2: Run diagnostics tests and see failures**

Run:

```bash
uv run pytest -q tests/test_diagnostics.py tests/test_session_reflection_diagnostics.py
```

Expected before implementation: failures because legacy fields still exist.

- [ ] **Step 3: Add `skill_paths.py`**

Create `src/codex_self_evolution/skill_paths.py`:

```python
from __future__ import annotations

import os
from pathlib import Path


CODEX_SKILLS_DIR_ENV = "CSEP_CODEX_SKILLS_DIR"


def codex_skills_dir(override: str | Path | None = None) -> Path:
    """Resolve the Codex skills root used by session reflection outputs."""
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get(CODEX_SKILLS_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".codex" / "skills"
```

Update `src/codex_self_evolution/session_reflection/runner.py` import:

```python
from ..skill_paths import codex_skills_dir
```

- [ ] **Step 4: Remove legacy diagnostics fields**

In `src/codex_self_evolution/diagnostics.py`, delete imports:

```python
from .config import PROJECTS_SUBDIR, get_home_dir, is_archived_bucket
from .managed_skills.publish import codex_skills_dir
from .skill_synthesis.inventory import read_skills_inventory
```

Replace with:

```python
from .config import get_home_dir
```

Delete constants:

```text
LAUNCHD_LABEL
SKILL_SYNTHESIS_LAUNCHD_LABEL
```

Change `collect_status()` return shape to:

```python
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": str(home_dir),
        "legacy_user_hooks": legacy_hooks,
        "plugin_hooks": _check_plugin_hook_bundle(),
        "session_reflection": session_reflection_status(home=home_dir),
        "session_recall": _check_session_recall(home_dir),
        "env_provider": _check_env_provider(home_dir),
        "tools": _check_tools(),
    }
```

Delete these functions:

```text
_check_scheduler
_check_launchd_label
_check_skill_synthesis
_check_skill_counts
_list_buckets
_inspect_bucket
_count_json
_read_last_receipt
_read_skill_synthesis_receipt
_recent_activity
_classify_retry_reason
_merge_stop_review
_merge_scan
```

Update module docstring to describe plugin hooks, session reflection, session recall, env provider, and tools only.

- [ ] **Step 5: Run diagnostics tests**

Run:

```bash
uv run pytest -q tests/test_diagnostics.py tests/test_session_reflection_diagnostics.py tests/test_session_reflection_runner.py
```

Expected: pass after test updates.

- [ ] **Step 6: Commit**

```bash
git add src/codex_self_evolution/diagnostics.py src/codex_self_evolution/skill_paths.py \
  src/codex_self_evolution/session_reflection/runner.py \
  tests/test_diagnostics.py tests/test_session_reflection_diagnostics.py tests/test_session_reflection_runner.py
git add -u tests/test_diagnostics_skill_synthesis.py
git commit -m "feat: remove legacy diagnostics surfaces"
```

### Task 6: Storage, Schemas, Paths, And Legacy Package Deletion

**Files:**
- Modify: `src/codex_self_evolution/config.py`
- Modify: `src/codex_self_evolution/storage.py`
- Modify: `src/codex_self_evolution/schemas.py`
- Modify: `src/codex_self_evolution/hooks/session_start.py`
- Modify: `src/codex_self_evolution/session_reflection/runner.py`
- Delete: old packages and old tests listed in this task.

- [ ] **Step 1: Run import grep before deleting**

Run:

```bash
rg -n "codex_self_evolution\\.(review|compiler|skill_synthesis|managed_skills)|SuggestionEnvelope|ReviewerOutput|CompilerReceipt|RecallRecord|SkillManifestEntry" src tests
```

Expected before deletion: many hits. Use this output as the deletion checklist.

- [ ] **Step 2: Reduce `Paths` and runtime dirs**

In `src/codex_self_evolution/config.py`, delete legacy constants:

```text
DEFAULT_BATCH_SIZE
DEFAULT_SCAN_MAX_RUNS_PER_PROJECT
MANAGED_SKILLS_DIRNAME
SKILL_SYNTHESIS_SUBDIR
SYNTH_SKILL_PREFIX
```

Change `Paths` to:

```python
@dataclass(frozen=True)
class Paths:
    repo_root: Path
    plugin_root: Path
    state_dir: Path
    memory_dir: Path
```

Change `build_paths()` return to:

```python
    return Paths(
        repo_root=resolved_repo,
        plugin_root=plugin_root,
        state_dir=resolved_state,
        memory_dir=resolved_state / "memory",
    )
```

- [ ] **Step 3: Reduce `storage.py` to retained helpers**

Keep these functions:

```text
ensure_runtime_dirs
file_lock
atomic_write_json
atomic_write_text
load_json
repo_fingerprint
load_memory_files
write_memory_file
```

Change `ensure_runtime_dirs()` to:

```python
def ensure_runtime_dirs(paths: Paths) -> None:
    """Create only retained per-project runtime directories."""
    paths.memory_dir.mkdir(parents=True, exist_ok=True)
```

Delete functions:

```text
list_suggestions
list_all_suggestions
append_pending_suggestion
update_suggestion
move_suggestion
claim_suggestions
finalize_suggestion
has_pending_work
```

If `file_lock` is only used by deleted compiler paths after this task, delete it after confirming:

```bash
rg -n "file_lock" src tests
```

- [ ] **Step 4: Reduce `schemas.py`**

Keep `SchemaError` and schema helpers still used by `session_reflection.validation`. Delete:

```text
Suggestion
ReviewerOutput
SuggestionEnvelope
RecallRecord
SkillManifestEntry
CompilerReceipt
SuggestionFamily
ALLOWED_FAMILIES
```

After deletion, run:

```bash
rg -n "from codex_self_evolution.schemas|SchemaError" src/codex_self_evolution/session_reflection tests/test_session_reflection_validation.py
```

Expected retained hits should only import `SchemaError` where needed.

- [ ] **Step 5: Delete old packages and scripts**

Run:

```bash
rm -rf src/codex_self_evolution/review
rm -rf src/codex_self_evolution/compiler
rm -rf src/codex_self_evolution/skill_synthesis
rm -rf src/codex_self_evolution/managed_skills
rm -f src/codex_self_evolution/hooks/stop_review.py
rm -f src/codex_self_evolution/hooks/codex_bridge.py
rm -f scripts/install-scheduler.sh scripts/uninstall-scheduler.sh
rm -f scripts/install-skill-synthesis-scheduler.sh scripts/uninstall-skill-synthesis-scheduler.sh
rm -f scripts/docker-e2e.sh
rm -rf tests/fixtures/compiler_replay
```

- [ ] **Step 6: Delete old tests**

Run:

```bash
rm -f tests/test_agent_compile_io.py
rm -f tests/test_agent_compiler_backend.py
rm -f tests/test_agent_opencode_invoker.py
rm -f tests/test_codex_bridge.py
rm -f tests/test_compile_context.py
rm -f tests/test_compiler_memory.py
rm -f tests/test_compiler_recall.py
rm -f tests/test_compiler_replay.py
rm -f tests/test_compiler_skill_disabled.py
rm -f tests/test_compiler_skills.py
rm -f tests/test_end_to_end.py
rm -f tests/test_managed_skill_publish.py
rm -f tests/test_memory_action_stats.py
rm -f tests/test_reviewer_retry.py
rm -f tests/test_reviewer_runner.py
rm -f tests/test_scan.py
rm -f tests/test_scheduler_integration.py
rm -f tests/test_script_fallback_merge.py
rm -f tests/test_skill_synthesis_agent.py
rm -f tests/test_skill_synthesis_cli.py
rm -f tests/test_skill_synthesis_evidence.py
rm -f tests/test_skill_synthesis_inventory.py
rm -f tests/test_skill_synthesis_runner.py
rm -f tests/test_skill_synthesis_scheduler.py
rm -f tests/test_skill_synthesis_validation.py
rm -f tests/test_stop_review.py
rm -f tests/test_storage_state_machine.py
rm -f tests/test_subprocess_reviewer.py
rm -f tests/test_writer.py
```

- [ ] **Step 7: Fix retained imports**

Run:

```bash
rg -n "review|compiler|skill_synthesis|managed_skills|SuggestionEnvelope|ReviewerOutput|CompilerReceipt|RecallRecord|SkillManifestEntry|recall/index.json|search_recall|stop-review" src tests
```

For each hit in `src/` or retained `tests/`, either remove the import/assertion or rewrite it to the new `session-stop` / `session_recall` / `csep-reflect-*` terminology.

- [ ] **Step 8: Run retained tests**

Run:

```bash
uv run pytest -q
```

Expected: failures only from docs/package-data/config references not yet cleaned in later tasks. If a failure imports deleted packages from retained runtime code, fix it in this task before committing.

- [ ] **Step 9: Commit**

```bash
git add src tests scripts
git commit -m "refactor: delete legacy reviewer compiler synthesis code"
```

### Task 7: Packaging, Makefile, Install Script, And Documentation Cleanup

**Files:**
- Modify: `pyproject.toml`
- Modify: `Makefile`
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/2026-05-15-memory-skill-session-recall-architecture.html`
- Modify: `scripts/install.sh`
- Modify: `tests/test_install_script.py`

- [ ] **Step 1: Update package metadata tests**

In `tests/test_plugin_bundle_hooks.py::test_pyproject_includes_package_plugin_bundle_data`, update package-data expectations:

```python
    assert "session_recall/policy.md" in package_data
    assert "session_recall/session_recall.md" in package_data
    assert "review/prompt.md" not in package_data
    assert "recall/policy.md" not in package_data
    assert "recall/session_recall.md" not in package_data
```

- [ ] **Step 2: Update `pyproject.toml`**

Change project description to:

```toml
description = "Session-level self-evolution loop for OpenAI Codex: stable memory, session reflection, and focused session recall, zero runtime dependencies."
```

Change package data to:

```toml
codex_self_evolution = [
    "session_recall/policy.md",
    "session_recall/session_recall.md",
    "plugin_bundle/.codex-plugin/plugin.json",
    "plugin_bundle/.codex-plugin/hooks.json",
]
```

- [ ] **Step 3: Update `Makefile`**

Remove `preflight` from `.PHONY`, and delete:

```make
preflight:
	$(PYTHON) -m codex_self_evolution.cli compile-preflight --state-dir data
```

If `docker-e2e` depends on deleted `scripts/docker-e2e.sh`, remove `docker-e2e` from `.PHONY` and delete the target:

```make
docker-e2e: docker-build docker-run
```

- [ ] **Step 4: Update install script expectations**

Run:

```bash
rg -n "stop-review|compile-preflight|scan|skill-synthesize|install-scheduler|install-skill-synthesis" scripts tests/test_install_script.py
```

Expected before cleanup: hits in install messages or tests. Update `scripts/install.sh` and `tests/test_install_script.py` so install only validates CLI install and plugin cache refresh. The install script must not mention scheduler installation.

- [ ] **Step 5: Clean current entry docs**

In `README.md`, remove current-use sections that instruct:

```text
scripts/install-scheduler.sh
scripts/uninstall-scheduler.sh
scripts/install-skill-synthesis-scheduler.sh
scripts/uninstall-skill-synthesis-scheduler.sh
codex-self-evolution compile-preflight
codex-self-evolution compile
codex-self-evolution scan
codex-self-evolution skill-synthesize
codex-self-evolution stop-review
codex-self-evolution recall-trigger
codex-self-evolution recall
```

Replace the runtime overview with this fenced text block:

```text
SessionStart hook
  -> codex-self-evolution session-start --from-stdin
  -> inject USER.md / MEMORY.md / recall contract

Stop hook
  -> codex-self-evolution session-stop --from-stdin
  -> archive session
  -> evaluate trigger policy
  -> maybe enqueue session-reflect worker

csep recall
  -> query session_recall SQLite/FTS
  -> return bounded session windows
```

In `docs/getting-started.md`, keep only:

```text
install
config init/show/validate
session-start smoke
session-stop smoke
csep session-archive
csep session-ingest
csep recall
status
```

In `docs/2026-05-15-memory-skill-session-recall-architecture.html`, remove the legacy reviewer / `SuggestionEnvelope` / `skill-synthesize` lane and keep memory, session reflection skill, and session recall lanes.

- [ ] **Step 6: Run docs grep**

Run:

```bash
rg -n "stop-review|compile-preflight|skill-synthesize|install-scheduler|install-skill-synthesis|SuggestionEnvelope|pending suggestions|recall/index.json|search_recall|recall-trigger|RecallRecord" README.md pyproject.toml Makefile scripts src tests plugins .codex-plugin
```

Expected: no output.

- [ ] **Step 7: Run package and manifest tests**

Run:

```bash
uv run pytest -q tests/test_plugin_bundle_hooks.py tests/test_install_script.py
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml Makefile README.md docs/getting-started.md \
  docs/2026-05-15-memory-skill-session-recall-architecture.html \
  scripts/install.sh tests/test_install_script.py tests/test_plugin_bundle_hooks.py
git commit -m "docs: remove legacy runtime instructions"
```

### Task 8: Full Verification And Cleanup

**Files:**
- All retained source, test, manifest, docs touched by earlier tasks.

- [ ] **Step 1: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all retained tests pass.

- [ ] **Step 2: Run legacy surface grep**

Run:

```bash
rg -n "stop-review|compile-preflight|skill-synthesize|install-scheduler|install-skill-synthesis|SuggestionEnvelope|pending suggestions|recall/index.json|search_recall|recall-trigger|RecallRecord" README.md pyproject.toml Makefile scripts src tests plugins .codex-plugin
```

Expected: no output.

- [ ] **Step 3: Verify deleted directories are gone**

Run:

```bash
test ! -d src/codex_self_evolution/review
test ! -d src/codex_self_evolution/compiler
test ! -d src/codex_self_evolution/skill_synthesis
test ! -d src/codex_self_evolution/managed_skills
test ! -d src/codex_self_evolution/recall
```

Expected: all commands exit 0.

- [ ] **Step 4: Verify retained CLI surface**

Run:

```bash
uv run codex-self-evolution --help
uv run csep --help
```

Expected `codex-self-evolution --help` lists retained commands only:

```text
session-start
session-stop
session-reflect
status
config
migrate-worktrees
```

Expected `csep --help` lists:

```text
recall
session-archive
session-ingest
```

- [ ] **Step 5: Verify removed CLI commands fail**

Run:

```bash
for cmd in stop-review compile compile-preflight scan skill-synthesize eval-compiler recall recall-trigger; do
  if uv run codex-self-evolution "$cmd" >/tmp/csep-legacy-check.out 2>&1; then
    echo "unexpected success: $cmd"
    exit 1
  fi
done
```

Expected: loop exits 0 with no output.

- [ ] **Step 6: Verify session-stop smoke**

Run:

```bash
printf '{"session_id":"smoke","turn_id":"t1","transcript_path":"/tmp/missing.jsonl","cwd":"%s","hook_event_name":"Stop","model":"gpt-5.4"}' "$PWD" \
  | uv run codex-self-evolution session-stop --from-stdin
```

Expected:

```json
{"continue": true}
```

The output may also contain a `warning` key if app-server or payload context is unavailable; that is acceptable only if `continue` is `true` and the process exits 0.

- [ ] **Step 7: Commit final cleanup if needed**

```bash
git status --short
git add README.md pyproject.toml Makefile scripts src tests plugins .codex-plugin docs
git commit -m "chore: verify legacy system removal"
```

Only run this commit if Step 1-6 required additional edits after the previous commits.

## Self-Review

- Spec coverage: Tasks 1-2 cover CLI and plugin command removal; Task 3 covers recall consolidation; Task 4 covers config cleanup; Task 5 covers diagnostics; Task 6 covers code package deletion; Task 7 covers docs/package/scripts; Task 8 covers final tests and grep.
- Placeholder scan: no unfinished marker words and no unspecified edge-case instructions. Each code-changing task names files, test commands, expected outcomes, and concrete snippets.
- Type consistency: `session-stop`, `session-reflect`, `session_recall`, `csep recall`, `csep-reflect-*`, and removed command names are consistent across tasks.
