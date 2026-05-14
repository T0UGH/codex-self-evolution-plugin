# Session Reflection Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default Stop-review path with a fast, asynchronous session reflection worker that forks the current Codex thread through app-server and validates direct memory / `csep-reflect-*` skill writes.

**Architecture:** Add a focused `codex_self_evolution.session_reflection` package for config, paths, job state, recursion guard, app-server JSON-RPC client, prompt construction, receipt validation, and orchestration. Keep the existing `stop-review` command name for hook compatibility, but make `stop-review --from-stdin` enqueue a session-reflection job and spawn `session-reflect --job <job_id>` instead of spawning the legacy reviewer. The legacy reviewer implementation remains callable through the non-stdin `stop-review --hook-payload` path for debug only.

**Tech Stack:** Python 3.11 stdlib, pytest via `uv run pytest`, Codex app-server v2 JSON-RPC through `codex app-server proxy`, existing config / storage helpers, existing plugin manifest layout.

---

## Scope And Existing Context

This plan implements `docs/superpowers/specs/2026-05-14-session-reflection-worker-design.md`.

Verified local context:

- Current worktree: `/Users/bytedance/code/github/codex-self-evolution-plugin_feature_session_reflection_appserver`
- Branch: `feature/session-reflection-appserver`
- Baseline design commit: `64223e7 docs: design session reflection worker`
- Local Codex CLI: `codex-cli 0.130.0`
- `codex app-server generate-json-schema` confirms `thread/fork`, `turn/start`, `ThreadForkParams.threadSource`, `ThreadSource.memory_consolidation`, `ephemeral`, `sandbox`, and `approvalPolicy`.
- Correct test runner on this machine is `uv run pytest ...`, not bare `python -m pytest`.

Assumptions for MVP:

- Foreground Stop hook must always print `{"continue": true}` quickly.
- `stop-review --from-stdin` becomes the default reflection enqueue path.
- `stop-review --hook-payload` keeps the old reviewer behavior as a manual debug path.
- `session-reflect --hook-payload <file>` replays a raw Codex Stop payload in foreground for debug.
- `session-reflect --job <job_id>` runs one queued job in foreground; the Stop hook uses this command in a detached child.
- App-server transport uses `codex app-server proxy` over stdio by default; tests inject a fake JSON-RPC transport and do not require a real app-server.

## File Structure

Create these files:

- `src/codex_self_evolution/session_reflection/__init__.py`  
  Package marker and public exports.
- `src/codex_self_evolution/session_reflection/paths.py`  
  Resolve global session reflection directories under `<home>/session_reflection/`.
- `src/codex_self_evolution/session_reflection/models.py`  
  Dataclasses and JSON serialization helpers for jobs, receipts, and skip reasons.
- `src/codex_self_evolution/session_reflection/state.py`  
  Create/update jobs, child registry, parent job lookup, lock state, and latest status.
- `src/codex_self_evolution/session_reflection/guard.py`  
  Recursion guard at hook entry: thread source, child registry, transcript markers, existing parent job, global lock.
- `src/codex_self_evolution/session_reflection/prompt.py`  
  Build the reflection child prompt and fixed marker contract.
- `src/codex_self_evolution/session_reflection/app_server.py`  
  Minimal JSON-RPC client around `codex app-server proxy`, plus injectable fake transport tests.
- `src/codex_self_evolution/session_reflection/validation.py`  
  Validate receipt schema, memory path boundaries, hash consistency, skill namespace/frontmatter/sections, and invalid markers.
- `src/codex_self_evolution/session_reflection/runner.py`  
  Orchestrate enqueue, background worker run, app-server fork/start, receipt validation, and status output.
- `tests/test_session_reflection_config.py`
- `tests/test_session_reflection_state.py`
- `tests/test_session_reflection_guard.py`
- `tests/test_session_reflection_prompt.py`
- `tests/test_session_reflection_app_server.py`
- `tests/test_session_reflection_validation.py`
- `tests/test_session_reflection_runner.py`
- `tests/test_session_reflection_cli.py`

Modify these files:

- `src/codex_self_evolution/config.py`  
  Add session reflection constants.
- `src/codex_self_evolution/config_file.py`  
  Add `[session_reflection]` config dataclass, loader, source tracking, and validation warnings.
- `src/codex_self_evolution/config_file_template.py`  
  Add default session reflection config.
- `src/codex_self_evolution/cli.py`  
  Add `session-reflect` subcommand and change `stop-review --from-stdin` to spawn it.
- `src/codex_self_evolution/hooks/codex_bridge.py`  
  Preserve raw `threadSource` / `source` passthrough for recursion guard diagnostics if present.
- `src/codex_self_evolution/diagnostics.py`  
  Add session reflection status summary and recent activity kind.
- `src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json`
- `plugins/codex-self-evolution/.codex-plugin/plugin.json`  
  Add manual `session-reflect status` command.
- `README.md`  
  Update lifecycle wording from Stop reviewer to session reflection worker.
- `docs/getting-started.md`  
  Add debug / status commands and failure inspection paths.

## Task 1: Config And Paths

**Files:**
- Modify: `src/codex_self_evolution/config.py`
- Modify: `src/codex_self_evolution/config_file.py`
- Modify: `src/codex_self_evolution/config_file_template.py`
- Create: `src/codex_self_evolution/session_reflection/__init__.py`
- Create: `src/codex_self_evolution/session_reflection/paths.py`
- Create: `tests/test_session_reflection_config.py`

- [ ] **Step 1: Write failing config and path tests**

Create `tests/test_session_reflection_config.py`:

```python
from __future__ import annotations

from pathlib import Path

from codex_self_evolution.config_file import config_to_dict, load_config
from codex_self_evolution.config_file_template import CONFIG_TEMPLATE
from codex_self_evolution.session_reflection.paths import build_session_reflection_paths


def _write_config(home: Path, text: str) -> None:
    """Write a config.toml fixture under the supplied CSEP home."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def test_session_reflection_defaults_are_enabled(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.session_reflection

    assert cfg.enabled is True
    assert cfg.backend == "codex-app-server"
    assert cfg.model == "gpt-5.3-codex-spark"
    assert cfg.ephemeral is True
    assert cfg.sandbox == "danger-full-access"
    assert cfg.approval_policy == "never"
    assert cfg.skill_prefix == "csep-reflect-"
    assert cfg.timeout_seconds == 900.0
    assert cfg.max_concurrent_jobs == 1
    assert cfg.replace_stop_reviewer is True
    assert loaded.sources["session_reflection.model"] == "default"


def test_session_reflection_toml_values_apply(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[session_reflection]
enabled = false
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = false
sandbox = "workspace-write"
approval_policy = "never"
skill_prefix = "csep-reflect-"
timeout_seconds = 1200
max_concurrent_jobs = 1
replace_stop_reviewer = true
""")

    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.session_reflection

    assert cfg.enabled is False
    assert cfg.ephemeral is False
    assert cfg.sandbox == "workspace-write"
    assert cfg.timeout_seconds == 1200.0
    assert loaded.sources["session_reflection.ephemeral"] == "config.toml"


def test_session_reflection_invalid_backend_warns(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[session_reflection]
enabled = true
backend = "codex-exec"
model = "gpt-5.3-codex-spark"
""")

    loaded = load_config(home=tmp_path, env={})

    assert loaded.config.session_reflection.backend == "codex-exec"
    assert any("session_reflection.backend" in warning for warning in loaded.warnings)


def test_config_to_dict_includes_session_reflection(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    data = config_to_dict(loaded.config)

    assert data["session_reflection"]["backend"] == "codex-app-server"
    assert data["session_reflection"]["skill_prefix"] == "csep-reflect-"


def test_session_reflection_template_contains_defaults() -> None:
    assert "[session_reflection]" in CONFIG_TEMPLATE
    assert 'backend = "codex-app-server"' in CONFIG_TEMPLATE
    assert 'model = "gpt-5.3-codex-spark"' in CONFIG_TEMPLATE
    assert 'threadSource = "memory_consolidation"' not in CONFIG_TEMPLATE


def test_build_session_reflection_paths(tmp_path: Path) -> None:
    paths = build_session_reflection_paths(home=tmp_path, job_id="job-1")

    assert paths.root == tmp_path / "session_reflection"
    assert paths.jobs_dir == paths.root / "jobs"
    assert paths.job_path == paths.jobs_dir / "job-1.json"
    assert paths.run_dir == paths.root / "runs" / "job-1"
    assert paths.receipt_path == paths.run_dir / "receipt.json"
    assert paths.child_threads_dir == paths.root / "child_threads"
    assert paths.locks_dir == paths.root / "locks"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_session_reflection_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_self_evolution.session_reflection'`.

- [ ] **Step 3: Add config constants and path module**

In `src/codex_self_evolution/config.py`, add near the skill synthesis constants:

```python
SESSION_REFLECTION_SUBDIR = "session_reflection"
REFLECT_SKILL_PREFIX = "csep-reflect-"
```

Create `src/codex_self_evolution/session_reflection/__init__.py`:

```python
"""Session reflection worker package.

This package owns the Stop-time Codex app-server fork path. The legacy
reviewer remains in ``codex_self_evolution.review`` for manual debug only.
"""
```

Create `src/codex_self_evolution/session_reflection/paths.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import SESSION_REFLECTION_SUBDIR, get_home_dir


@dataclass(frozen=True)
class SessionReflectionPaths:
    """Filesystem layout for global session reflection state."""

    home: Path
    root: Path
    jobs_dir: Path
    runs_dir: Path
    child_threads_dir: Path
    locks_dir: Path
    latest_path: Path
    job_path: Path
    run_dir: Path
    receipt_path: Path
    prompt_path: Path
    events_path: Path
    validation_path: Path
    global_lock_path: Path


def build_session_reflection_paths(
    home: str | Path | None = None,
    job_id: str = "",
) -> SessionReflectionPaths:
    """Resolve all session reflection paths for ``job_id`` without creating them."""
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    root = home_dir / SESSION_REFLECTION_SUBDIR
    run_dir = root / "runs" / job_id if job_id else root / "runs"
    return SessionReflectionPaths(
        home=home_dir,
        root=root,
        jobs_dir=root / "jobs",
        runs_dir=root / "runs",
        child_threads_dir=root / "child_threads",
        locks_dir=root / "locks",
        latest_path=root / "latest.json",
        job_path=root / "jobs" / f"{job_id}.json" if job_id else root / "jobs",
        run_dir=run_dir,
        receipt_path=run_dir / "receipt.json",
        prompt_path=run_dir / "prompt.txt",
        events_path=run_dir / "events.jsonl",
        validation_path=run_dir / "validation.json",
        global_lock_path=root / "locks" / "global.lock",
    )
```

- [ ] **Step 4: Add config dataclasses and loader wiring**

In `src/codex_self_evolution/config_file.py`, add after `SkillSynthesisConfig`:

```python
@dataclass
class SessionReflectionConfig:
    """Config for Stop-time session reflection through Codex app-server."""

    enabled: bool = True
    backend: str = "codex-app-server"
    model: str = "gpt-5.3-codex-spark"
    ephemeral: bool = True
    sandbox: str = "danger-full-access"
    approval_policy: str = "never"
    skill_prefix: str = "csep-reflect-"
    timeout_seconds: float = 900.0
    max_concurrent_jobs: int = 1
    replace_stop_reviewer: bool = True
```

Add the field to `PluginConfig`:

```python
    session_reflection: SessionReflectionConfig = field(default_factory=SessionReflectionConfig)
```

Add constants near existing allowed sets:

```python
ALLOWED_SESSION_REFLECTION_BACKENDS = {"codex-app-server"}
ALLOWED_SESSION_REFLECTION_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
ALLOWED_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}
```

Add loader logic after the `skill_synthesis` section:

```python
    # --- session_reflection ---
    reflection_toml = raw_toml.get("session_reflection", {}) or {}
    reflection_enabled = reflection_toml.get("enabled")
    if isinstance(reflection_enabled, bool):
        config.session_reflection.enabled = reflection_enabled
        sources["session_reflection.enabled"] = "config.toml"
    else:
        sources["session_reflection.enabled"] = "default"

    config.session_reflection.backend, sources["session_reflection.backend"] = _resolve(
        field_path="session_reflection.backend",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("backend"),
        default=config.session_reflection.backend,
    )
    config.session_reflection.model, sources["session_reflection.model"] = _resolve(
        field_path="session_reflection.model",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("model"),
        default=config.session_reflection.model,
    )
    ephemeral = reflection_toml.get("ephemeral")
    if isinstance(ephemeral, bool):
        config.session_reflection.ephemeral = ephemeral
        sources["session_reflection.ephemeral"] = "config.toml"
    else:
        sources["session_reflection.ephemeral"] = "default"
    config.session_reflection.sandbox, sources["session_reflection.sandbox"] = _resolve(
        field_path="session_reflection.sandbox",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("sandbox"),
        default=config.session_reflection.sandbox,
    )
    config.session_reflection.approval_policy, sources["session_reflection.approval_policy"] = _resolve(
        field_path="session_reflection.approval_policy",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("approval_policy"),
        default=config.session_reflection.approval_policy,
    )
    config.session_reflection.skill_prefix, sources["session_reflection.skill_prefix"] = _resolve(
        field_path="session_reflection.skill_prefix",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("skill_prefix"),
        default=config.session_reflection.skill_prefix,
    )
    config.session_reflection.timeout_seconds, sources["session_reflection.timeout_seconds"] = _resolve_number(
        "session_reflection.timeout_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("timeout_seconds"),
        default=config.session_reflection.timeout_seconds,
        cast=float,
    )
    config.session_reflection.max_concurrent_jobs, sources["session_reflection.max_concurrent_jobs"] = _resolve_number(
        "session_reflection.max_concurrent_jobs",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("max_concurrent_jobs"),
        default=config.session_reflection.max_concurrent_jobs,
        cast=int,
    )
    replace_stop = reflection_toml.get("replace_stop_reviewer")
    if isinstance(replace_stop, bool):
        config.session_reflection.replace_stop_reviewer = replace_stop
        sources["session_reflection.replace_stop_reviewer"] = "config.toml"
    else:
        sources["session_reflection.replace_stop_reviewer"] = "default"

    if config.session_reflection.enabled:
        if config.session_reflection.backend not in ALLOWED_SESSION_REFLECTION_BACKENDS:
            warnings.append(
                "session_reflection.backend must be codex-app-server in v1; "
                f"got {config.session_reflection.backend!r}"
            )
        if config.session_reflection.sandbox not in ALLOWED_SESSION_REFLECTION_SANDBOXES:
            warnings.append("session_reflection.sandbox is not a supported Codex sandbox value")
        if config.session_reflection.approval_policy not in ALLOWED_APPROVAL_POLICIES:
            warnings.append("session_reflection.approval_policy is not a supported Codex approval policy")
        if config.session_reflection.skill_prefix != "csep-reflect-":
            warnings.append("session_reflection.skill_prefix must remain csep-reflect- in v1")
        if config.session_reflection.timeout_seconds <= 0:
            warnings.append("session_reflection.timeout_seconds must be positive")
        if config.session_reflection.max_concurrent_jobs != 1:
            warnings.append("session_reflection.max_concurrent_jobs must be 1 in v1")
```

- [ ] **Step 5: Add template block**

In `src/codex_self_evolution/config_file_template.py`, insert before `[log]`:

```toml
# ===========================================================================
# [session_reflection] — Stop-time memory and skill reflection
# ===========================================================================

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
replace_stop_reviewer = true
```

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_session_reflection_config.py -q
```

Expected: PASS.

Commit:

```bash
git add src/codex_self_evolution/config.py src/codex_self_evolution/config_file.py src/codex_self_evolution/config_file_template.py src/codex_self_evolution/session_reflection tests/test_session_reflection_config.py
git commit -m "feat: add session reflection config"
```

## Task 2: Job State And Recursion Guard

**Files:**
- Create: `src/codex_self_evolution/session_reflection/models.py`
- Create: `src/codex_self_evolution/session_reflection/state.py`
- Create: `src/codex_self_evolution/session_reflection/guard.py`
- Create: `tests/test_session_reflection_state.py`
- Create: `tests/test_session_reflection_guard.py`

- [ ] **Step 1: Write failing state tests**

Create `tests/test_session_reflection_state.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.session_reflection.state import (
    create_job_from_payload,
    find_existing_parent_job,
    register_child_thread,
    update_job_status,
)


def _payload(repo: Path) -> dict[str, object]:
    """Return a raw Codex Stop payload fixture."""
    transcript = repo / "rollout.jsonl"
    transcript.write_text('{"role":"assistant","content":"done"}\n', encoding="utf-8")
    return {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(repo),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
    }


def test_create_job_from_payload_writes_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    job = create_job_from_payload(_payload(repo), home=tmp_path)

    assert job["schema_version"] == 1
    assert job["parent_session_id"] == "parent-1"
    assert job["parent_turn_id"] == "turn-1"
    assert job["cwd"] == str(repo)
    assert job["status"] == "queued"
    job_path = tmp_path / "session_reflection" / "jobs" / f"{job['job_id']}.json"
    assert json.loads(job_path.read_text(encoding="utf-8"))["job_id"] == job["job_id"]


def test_find_existing_parent_job_detects_queued_and_succeeded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)

    assert find_existing_parent_job("parent-1", home=tmp_path)["job_id"] == created["job_id"]

    update_job_status(created["job_id"], "succeeded", home=tmp_path)

    assert find_existing_parent_job("parent-1", home=tmp_path)["status"] == "succeeded"


def test_register_child_thread_writes_registry(tmp_path: Path) -> None:
    register_child_thread(
        child_thread_id="child-1",
        parent_session_id="parent-1",
        job_id="job-1",
        home=tmp_path,
    )

    registry = tmp_path / "session_reflection" / "child_threads" / "child-1.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    assert data["child_thread_id"] == "child-1"
    assert data["thread_source"] == "memory_consolidation"
```

- [ ] **Step 2: Write failing guard tests**

Create `tests/test_session_reflection_guard.py`:

```python
from __future__ import annotations

from pathlib import Path

from codex_self_evolution.session_reflection.guard import evaluate_recursion_guard
from codex_self_evolution.session_reflection.state import create_job_from_payload, register_child_thread


def _payload(repo: Path, **overrides: object) -> dict[str, object]:
    """Return a raw Codex Stop payload with optional overrides."""
    transcript = repo / "rollout.jsonl"
    transcript.write_text('{"role":"assistant","content":"normal"}\n', encoding="utf-8")
    payload: dict[str, object] = {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(repo),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
    }
    payload.update(overrides)
    return payload


def test_guard_skips_memory_consolidation_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    decision = evaluate_recursion_guard(
        _payload(repo, threadSource="memory_consolidation"),
        home=tmp_path,
    )

    assert decision.skip is True
    assert decision.reason == "thread_source_memory_consolidation"


def test_guard_skips_child_registry_hit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    register_child_thread(
        child_thread_id="parent-1",
        parent_session_id="root-parent",
        job_id="job-1",
        home=tmp_path,
    )

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is True
    assert decision.reason == "child_thread_registry"


def test_guard_skips_transcript_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = repo / "rollout.jsonl"
    transcript.write_text('{"role":"user","content":"CSEP_REFLECTION_CHILD=1"}\n', encoding="utf-8")

    decision = evaluate_recursion_guard(
        _payload(repo, transcript_path=str(transcript)),
        home=tmp_path,
    )

    assert decision.skip is True
    assert decision.reason == "reflection_marker"


def test_guard_skips_existing_parent_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    create_job_from_payload(payload, home=tmp_path)

    decision = evaluate_recursion_guard(payload, home=tmp_path)

    assert decision.skip is True
    assert decision.reason == "parent_job_exists"


def test_guard_allows_normal_parent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is False
    assert decision.reason == ""
```

- [ ] **Step 3: Run focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_session_reflection_state.py tests/test_session_reflection_guard.py -q
```

Expected: FAIL with missing `models`, `state`, and `guard` symbols.

- [ ] **Step 4: Implement job models and state helpers**

Create `src/codex_self_evolution/session_reflection/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardDecision:
    """Result of the Stop-entry recursion guard."""

    skip: bool
    reason: str = ""
    detail: str = ""
```

Create `src/codex_self_evolution/session_reflection/state.py`:

```python
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..storage import atomic_write_json, load_json
from .paths import build_session_reflection_paths

ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_PARENT_STATUSES = {"succeeded"}


def utc_timestamp() -> str:
    """Return a second-granularity UTC timestamp for persisted state."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_job_id(parent_session_id: str, parent_turn_id: str, created_at: str) -> str:
    """Build a deterministic-looking job id from parent identity and time."""
    digest = hashlib.sha1(f"{parent_session_id}:{parent_turn_id}:{created_at}".encode("utf-8")).hexdigest()[:8]
    compact = created_at.replace("-", "").replace(":", "").replace("T", "T").replace("Z", "Z")
    return f"{compact}-{digest}"


def create_job_from_payload(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
    """Create and persist a queued session reflection job from a raw Stop payload."""
    created_at = utc_timestamp()
    parent_session_id = str(payload.get("session_id") or payload.get("thread_id") or "unknown-thread")
    parent_turn_id = str(payload.get("turn_id") or "")
    job_id = make_job_id(parent_session_id, parent_turn_id, created_at)
    paths = build_session_reflection_paths(home=home, job_id=job_id)
    job = {
        "schema_version": 1,
        "job_id": job_id,
        "parent_session_id": parent_session_id,
        "parent_turn_id": parent_turn_id,
        "parent_transcript_path": str(payload.get("transcript_path") or payload.get("codex_transcript_path") or ""),
        "cwd": str(payload.get("cwd") or "."),
        "model": "gpt-5.3-codex-spark",
        "status": "queued",
        "created_at": created_at,
        "updated_at": created_at,
        "raw_payload": payload,
    }
    atomic_write_json(paths.job_path, job)
    atomic_write_json(paths.latest_path, job)
    return job


def load_job(job_id: str, *, home: str | Path | None = None) -> dict[str, Any]:
    """Load a persisted session reflection job."""
    raw = load_json(build_session_reflection_paths(home=home, job_id=job_id).job_path)
    if not isinstance(raw, dict):
        raise ValueError(f"job {job_id} is not a JSON object")
    return raw


def update_job_status(job_id: str, status: str, *, home: str | Path | None = None, **fields: Any) -> dict[str, Any]:
    """Update one job status and extra fields atomically."""
    job = load_job(job_id, home=home)
    job.update(fields)
    job["status"] = status
    job["updated_at"] = utc_timestamp()
    paths = build_session_reflection_paths(home=home, job_id=job_id)
    atomic_write_json(paths.job_path, job)
    atomic_write_json(paths.latest_path, job)
    return job


def list_jobs(*, home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return all readable job objects sorted by filename."""
    paths = build_session_reflection_paths(home=home)
    jobs: list[dict[str, Any]] = []
    if not paths.jobs_dir.is_dir():
        return jobs
    for path in sorted(paths.jobs_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            jobs.append(raw)
    return jobs


def find_existing_parent_job(parent_session_id: str, *, home: str | Path | None = None) -> dict[str, Any] | None:
    """Find active or already-succeeded job for the same parent session."""
    for job in reversed(list_jobs(home=home)):
        if job.get("parent_session_id") != parent_session_id:
            continue
        if job.get("status") in ACTIVE_JOB_STATUSES | TERMINAL_PARENT_STATUSES:
            return job
    return None


def register_child_thread(
    *,
    child_thread_id: str,
    parent_session_id: str,
    job_id: str,
    home: str | Path | None = None,
) -> Path:
    """Persist child thread metadata for recursion prevention."""
    paths = build_session_reflection_paths(home=home)
    registry = paths.child_threads_dir / f"{child_thread_id}.json"
    atomic_write_json(registry, {
        "schema_version": 1,
        "child_thread_id": child_thread_id,
        "parent_session_id": parent_session_id,
        "job_id": job_id,
        "thread_source": "memory_consolidation",
        "created_at": utc_timestamp(),
    })
    return registry


def child_registry_path(session_id: str, *, home: str | Path | None = None) -> Path:
    """Return the registry path for a possible child session id."""
    return build_session_reflection_paths(home=home).child_threads_dir / f"{session_id}.json"


def write_global_lock(*, home: str | Path | None = None) -> Path:
    """Create the global worker lock file."""
    paths = build_session_reflection_paths(home=home)
    atomic_write_json(paths.global_lock_path, {"created_at": utc_timestamp(), "pid": os.getpid()})
    return paths.global_lock_path
```

- [ ] **Step 5: Implement recursion guard**

Create `src/codex_self_evolution/session_reflection/guard.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import GuardDecision
from .paths import build_session_reflection_paths
from .state import child_registry_path, find_existing_parent_job

REFLECTION_MARKERS = ("CSEP_REFLECTION_JOB_ID=", "CSEP_REFLECTION_CHILD=1")


def evaluate_recursion_guard(
    payload: dict[str, Any],
    *,
    home: str | Path | None = None,
) -> GuardDecision:
    """Return whether a Stop payload must skip session reflection."""
    thread_source = str(payload.get("threadSource") or payload.get("thread_source") or payload.get("source") or "")
    if thread_source == "memory_consolidation":
        return GuardDecision(True, "thread_source_memory_consolidation", thread_source)

    session_id = str(payload.get("session_id") or payload.get("thread_id") or "")
    if session_id and child_registry_path(session_id, home=home).is_file():
        return GuardDecision(True, "child_thread_registry", session_id)

    transcript_path = str(payload.get("transcript_path") or payload.get("codex_transcript_path") or "")
    if transcript_path and _transcript_has_marker(Path(transcript_path)):
        return GuardDecision(True, "reflection_marker", transcript_path)

    if session_id and find_existing_parent_job(session_id, home=home):
        return GuardDecision(True, "parent_job_exists", session_id)

    paths = build_session_reflection_paths(home=home)
    if paths.global_lock_path.exists():
        return GuardDecision(True, "global_lock", str(paths.global_lock_path))

    return GuardDecision(False)


def _transcript_has_marker(path: Path) -> bool:
    """Check a transcript file for reflection markers without parsing the whole session."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    return any(marker in text for marker in REFLECTION_MARKERS)
```

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_session_reflection_state.py tests/test_session_reflection_guard.py -q
```

Expected: PASS.

Commit:

```bash
git add src/codex_self_evolution/session_reflection tests/test_session_reflection_state.py tests/test_session_reflection_guard.py
git commit -m "feat: add reflection job guard"
```

## Task 3: Prompt And Receipt Validation

**Files:**
- Create: `src/codex_self_evolution/session_reflection/prompt.py`
- Create: `src/codex_self_evolution/session_reflection/validation.py`
- Create: `tests/test_session_reflection_prompt.py`
- Create: `tests/test_session_reflection_validation.py`

- [ ] **Step 1: Write failing prompt tests**

Create `tests/test_session_reflection_prompt.py`:

```python
from __future__ import annotations

from pathlib import Path

from codex_self_evolution.session_reflection.prompt import build_reflection_prompt


def test_reflection_prompt_contains_markers_and_boundaries(tmp_path: Path) -> None:
    prompt = build_reflection_prompt(
        job_id="job-1",
        parent_session_id="parent-1",
        cwd=tmp_path,
        memory_user_path=tmp_path / "memory" / "USER.md",
        memory_project_path=tmp_path / "memory" / "MEMORY.md",
        skills_root=tmp_path / "skills",
        receipt_path=tmp_path / "runs" / "job-1" / "receipt.json",
    )

    assert "CSEP_REFLECTION_JOB_ID=job-1" in prompt
    assert "CSEP_REFLECTION_CHILD=1" in prompt
    assert str(tmp_path / "memory" / "USER.md") in prompt
    assert str(tmp_path / "skills" / "csep-reflect-*") in prompt
    assert "fact | rule | preference | workflow | duplicate | transient | sensitive" in prompt
    assert '"memory_changes": []' in prompt
    assert '"skill_changes": []' in prompt
```

- [ ] **Step 2: Write failing validation tests**

Create `tests/test_session_reflection_validation.py`:

```python
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from codex_self_evolution.session_reflection.validation import validate_receipt


def _active_skill_text(name: str = "csep-reflect-alpha") -> str:
    """Return a valid active csep-reflect skill document."""
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use when repeated alpha reflection needs concrete command evidence.\n"
        "---\n\n"
        "# Alpha Reflection\n\n"
        "## Skill Decision\n\n"
        "- Why this is a skill: repeated sessions need the same command workflow.\n"
        "- Why not memory: the value is procedural, not a static fact or preference.\n"
        "- Existing skill boundary: no existing skill covers this sequence.\n\n"
        "## When to Use\n\nUse this when alpha reflection needs repeated command evidence.\n\n"
        "## Inputs\n\n- Repository path.\n- Receipt path.\n\n"
        "## Workflow\n\n1. Run the command.\n2. Check the receipt.\n3. Verify the output.\n\n"
        "## Verification\n\nConfirm the receipt has the expected job id.\n\n"
        "## Failure Handling\n\nIf the receipt is missing, stop and report the path.\n"
    )


def _sha(path: Path) -> str:
    """Return the sha256 digest for a test fixture file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_validate_receipt_accepts_memory_and_reflect_skill(tmp_path: Path) -> None:
    memory = tmp_path / "project" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Use stable config.\n", encoding="utf-8")
    skill = tmp_path / "skills" / "csep-reflect-alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_active_skill_text(), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "job_id": "job-1",
        "parent_session_id": "parent-1",
        "child_thread_id": "child-1",
        "status": "succeeded",
        "memory_changes": [{
            "path": str(memory),
            "scope": "global",
            "action": "add",
            "before_hash": "",
            "after_hash": _sha(memory),
            "summary": "record stable config",
        }],
        "skill_changes": [{
            "skill_id": "csep-reflect-alpha",
            "path": str(skill),
            "action": "create",
            "evidence_kind": "workflow",
            "why_skill_not_memory": "workflow",
            "existing_skill_overlap": "none",
            "before_hash": "",
            "after_hash": _sha(skill),
        }],
        "skipped_candidates": [],
        "validation_notes": [],
        "errors": [],
        "started_at": "2026-05-14T12:00:00Z",
        "finished_at": "2026-05-14T12:01:00Z",
    }), encoding="utf-8")

    result = validate_receipt(
        receipt,
        memory_roots=[memory.parent],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "succeeded"
    assert result["invalid_skills"] == []
    assert result["boundary_violations"] == []


def test_validate_receipt_rejects_skill_outside_namespace(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "manual-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_active_skill_text("manual-skill"), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "job_id": "job-1",
        "parent_session_id": "parent-1",
        "child_thread_id": "child-1",
        "status": "succeeded",
        "memory_changes": [],
        "skill_changes": [{"skill_id": "manual-skill", "path": str(skill), "action": "create", "after_hash": _sha(skill)}],
        "skipped_candidates": [],
        "validation_notes": [],
        "errors": [],
        "started_at": "2026-05-14T12:00:00Z",
        "finished_at": "2026-05-14T12:01:00Z",
    }), encoding="utf-8")

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "failed"
    assert result["boundary_violations"][0]["reason"] == "skill_outside_namespace"
```

- [ ] **Step 3: Run focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_session_reflection_prompt.py tests/test_session_reflection_validation.py -q
```

Expected: FAIL with missing `prompt` and `validation` modules.

- [ ] **Step 4: Implement prompt builder**

Create `src/codex_self_evolution/session_reflection/prompt.py`:

```python
from __future__ import annotations

from pathlib import Path


def build_reflection_prompt(
    *,
    job_id: str,
    parent_session_id: str,
    cwd: str | Path,
    memory_user_path: str | Path,
    memory_project_path: str | Path,
    skills_root: str | Path,
    receipt_path: str | Path,
) -> str:
    """Build the bounded instruction contract for the reflection child."""
    return (
        "You are the codex-self-evolution session reflection worker.\n\n"
        f"CSEP_REFLECTION_JOB_ID={job_id}\n"
        "CSEP_REFLECTION_CHILD=1\n\n"
        f"Parent session id: {parent_session_id}\n"
        f"Repository cwd: {Path(cwd)}\n"
        f"User memory file: {Path(memory_user_path)}\n"
        f"Project memory file: {Path(memory_project_path)}\n"
        f"Writable skill namespace: {Path(skills_root)}/csep-reflect-*\n"
        f"Required receipt path: {Path(receipt_path)}\n\n"
        "Your only durable outputs are memory and skills.\n"
        "Classify every candidate as: fact | rule | preference | workflow | duplicate | transient | sensitive.\n"
        "Write facts, rules, and preferences only to the memory files above.\n"
        "Create or edit active skills only for reusable workflows under csep-reflect-*.\n"
        "Skip transient and sensitive candidates. Do not write secrets, tokens, cookies, private ids, or internal links.\n\n"
        "Every active SKILL.md must include Skill Decision, When to Use, Inputs, Workflow, Verification, and Failure Handling.\n"
        "The frontmatter name must match the csep-reflect-* directory name.\n\n"
        "Write receipt.json with this exact top-level shape:\n"
        "{\n"
        "  \"schema_version\": 1,\n"
        f"  \"job_id\": \"{job_id}\",\n"
        f"  \"parent_session_id\": \"{parent_session_id}\",\n"
        "  \"child_thread_id\": \"<current child thread id>\",\n"
        "  \"status\": \"succeeded|partial|failed|skipped\",\n"
        "  \"memory_changes\": [],\n"
        "  \"skill_changes\": [],\n"
        "  \"skipped_candidates\": [],\n"
        "  \"validation_notes\": [],\n"
        "  \"errors\": [],\n"
        "  \"started_at\": \"<UTC ISO timestamp>\",\n"
        "  \"finished_at\": \"<UTC ISO timestamp>\"\n"
        "}\n\n"
        "After writing files and receipt, reply with a one-sentence summary only."
    )
```

- [ ] **Step 5: Implement validation**

Create `src/codex_self_evolution/session_reflection/validation.py` with these responsibilities:

```python
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..storage import atomic_write_json

REQUIRED_SKILL_SECTIONS = (
    "## Skill Decision",
    "## When to Use",
    "## Inputs",
    "## Workflow",
    "## Verification",
    "## Failure Handling",
)


def validate_receipt(
    receipt_path: Path,
    *,
    memory_roots: list[Path],
    skills_root: Path,
    skill_prefix: str,
) -> dict[str, Any]:
    """Validate child receipt and downgrade status on boundary or artifact failures."""
    if not receipt_path.is_file():
        return {"status": "failed", "reason": "receipt_missing", "invalid_skills": [], "boundary_violations": []}
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "failed", "reason": "receipt_invalid_json", "error": str(exc), "invalid_skills": [], "boundary_violations": []}
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        return {"status": "failed", "reason": "receipt_schema", "invalid_skills": [], "boundary_violations": []}

    boundary_violations = _memory_boundary_violations(receipt.get("memory_changes"), memory_roots)
    boundary_violations.extend(_skill_boundary_violations(receipt.get("skill_changes"), skills_root, skill_prefix))
    hash_mismatches = _hash_mismatches(receipt.get("memory_changes")) + _hash_mismatches(receipt.get("skill_changes"))
    invalid_skills = _invalid_skills(receipt.get("skill_changes"), skills_root, skill_prefix)

    status = str(receipt.get("status") or "failed")
    if boundary_violations:
        status = "failed"
    elif invalid_skills or hash_mismatches:
        status = "partial"
    elif not receipt.get("memory_changes") and not receipt.get("skill_changes"):
        status = "skipped_empty"

    result = {
        "status": status,
        "receipt_status": receipt.get("status"),
        "boundary_violations": boundary_violations,
        "hash_mismatches": hash_mismatches,
        "invalid_skills": invalid_skills,
    }
    _write_invalid_markers(invalid_skills, receipt.get("job_id"))
    return result


def _memory_boundary_violations(changes: Any, memory_roots: list[Path]) -> list[dict[str, str]]:
    """Return memory changes outside allowed memory roots or filenames."""
    if not isinstance(changes, list):
        return [{"reason": "memory_changes_not_list", "path": ""}]
    roots = [root.resolve(strict=False) for root in memory_roots]
    violations: list[dict[str, str]] = []
    for item in changes:
        path = Path(str(item.get("path") if isinstance(item, dict) else "")).expanduser()
        if path.name not in {"USER.md", "MEMORY.md"}:
            violations.append({"reason": "memory_filename", "path": str(path)})
            continue
        if not any(_under(path, root) for root in roots):
            violations.append({"reason": "memory_outside_root", "path": str(path)})
    return violations


def _skill_boundary_violations(changes: Any, skills_root: Path, skill_prefix: str) -> list[dict[str, str]]:
    """Return skill changes outside the allowed csep-reflect namespace."""
    if not isinstance(changes, list):
        return [{"reason": "skill_changes_not_list", "path": ""}]
    root = skills_root.resolve(strict=False)
    violations: list[dict[str, str]] = []
    for item in changes:
        path = Path(str(item.get("path") if isinstance(item, dict) else "")).expanduser()
        if path.name != "SKILL.md" or not _under(path, root):
            violations.append({"reason": "skill_outside_root", "path": str(path)})
            continue
        if not path.parent.name.startswith(skill_prefix):
            violations.append({"reason": "skill_outside_namespace", "path": str(path)})
    return violations


def _hash_mismatches(changes: Any) -> list[dict[str, str]]:
    """Return receipt items whose after_hash does not match the file bytes."""
    if not isinstance(changes, list):
        return []
    mismatches: list[dict[str, str]] = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or "")).expanduser()
        expected = str(item.get("after_hash") or "")
        if not expected or not path.is_file():
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            mismatches.append({"path": str(path), "expected": expected, "actual": actual})
    return mismatches


def _invalid_skills(changes: Any, skills_root: Path, skill_prefix: str) -> list[dict[str, str]]:
    """Validate csep-reflect SKILL.md files referenced by the receipt."""
    if not isinstance(changes, list):
        return []
    invalid: list[dict[str, str]] = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or "")).expanduser()
        if not path.is_file() or not _under(path, skills_root.resolve(strict=False)):
            continue
        reason = _validate_skill_doc(path, skill_prefix)
        if reason:
            invalid.append({"path": str(path), "reason": reason})
    return invalid


def _validate_skill_doc(path: Path, skill_prefix: str) -> str:
    """Return an invalid reason for a generated skill, or empty string."""
    text = path.read_text(encoding="utf-8")
    if not re.search(r"^---\n.*?\n---\n", text, re.DOTALL):
        return "missing_frontmatter"
    if f"name: {path.parent.name}" not in text.split("---", 2)[1]:
        return "name_mismatch"
    if not path.parent.name.startswith(skill_prefix):
        return "outside_namespace"
    for section in REQUIRED_SKILL_SECTIONS:
        if section not in text:
            return "missing_required_section"
    return ""


def _write_invalid_markers(invalid_skills: list[dict[str, str]], job_id: object) -> None:
    """Write .csep-invalid.json markers beside invalid generated skills."""
    for item in invalid_skills:
        skill_path = Path(item["path"])
        atomic_write_json(skill_path.parent / ".csep-invalid.json", {
            "schema_version": 1,
            "job_id": str(job_id or ""),
            "skill_path": str(skill_path),
            "reason": item["reason"],
        })


def _under(path: Path, root: Path) -> bool:
    """Return whether ``path`` resolves under ``root`` without requiring existence."""
    try:
        return path.resolve(strict=False).is_relative_to(root)
    except OSError:
        return False
```

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_session_reflection_prompt.py tests/test_session_reflection_validation.py -q
```

Expected: PASS.

Commit:

```bash
git add src/codex_self_evolution/session_reflection tests/test_session_reflection_prompt.py tests/test_session_reflection_validation.py
git commit -m "feat: validate reflection outputs"
```

## Task 4: App-Server Client And Worker Orchestration

**Files:**
- Create: `src/codex_self_evolution/session_reflection/app_server.py`
- Create: `src/codex_self_evolution/session_reflection/runner.py`
- Create: `tests/test_session_reflection_app_server.py`
- Create: `tests/test_session_reflection_runner.py`

- [ ] **Step 1: Write failing app-server client tests**

Create `tests/test_session_reflection_app_server.py`:

```python
from __future__ import annotations

from codex_self_evolution.session_reflection.app_server import ReflectionAppServerClient


class FakeTransport:
    """In-memory JSON-RPC transport for app-server tests."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.requests.append({"method": method, "params": params})
        if method == "thread/fork":
            return {"id": "child-1", "sessionId": "child-session-1", "threadSource": "memory_consolidation"}
        if method == "turn/start":
            return {"id": "turn-1", "threadId": params["threadId"]}
        raise AssertionError(method)


def test_fork_thread_sends_required_params() -> None:
    transport = FakeTransport()
    client = ReflectionAppServerClient(transport=transport)

    result = client.fork_thread(
        parent_thread_id="parent-1",
        transcript_path="/tmp/rollout.jsonl",
        cwd="/tmp/repo",
        model="gpt-5.3-codex-spark",
        ephemeral=True,
        sandbox="danger-full-access",
        approval_policy="never",
    )

    assert result["child_thread_id"] == "child-1"
    first = transport.requests[0]
    assert first["method"] == "thread/fork"
    params = first["params"]
    assert params["threadId"] == "parent-1"
    assert params["model"] == "gpt-5.3-codex-spark"
    assert params["cwd"] == "/tmp/repo"
    assert params["threadSource"] == "memory_consolidation"
    assert params["ephemeral"] is True
    assert params["sandbox"] == "danger-full-access"
    assert params["approvalPolicy"] == "never"


def test_start_turn_sends_marker_prompt() -> None:
    transport = FakeTransport()
    client = ReflectionAppServerClient(transport=transport)

    result = client.start_reflection_turn(
        child_thread_id="child-1",
        cwd="/tmp/repo",
        prompt="CSEP_REFLECTION_CHILD=1",
        model="gpt-5.3-codex-spark",
        sandbox="danger-full-access",
        approval_policy="never",
    )

    assert result["turn_id"] == "turn-1"
    second = transport.requests[0]
    assert second["method"] == "turn/start"
    params = second["params"]
    assert params["threadId"] == "child-1"
    assert params["input"] == [{"type": "text", "text": "CSEP_REFLECTION_CHILD=1"}]
    assert params["model"] == "gpt-5.3-codex-spark"
    assert params["sandboxPolicy"] == {"mode": "danger-full-access"}
    assert params["approvalPolicy"] == "never"
```

- [ ] **Step 2: Write failing runner tests**

Create `tests/test_session_reflection_runner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.session_reflection.runner import enqueue_reflection_from_payload, run_reflection_job


class FakeClient:
    """Fake app-server client that writes a valid receipt during turn/start."""

    def __init__(self, receipt_status: str = "succeeded") -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.receipt_status = receipt_status

    def fork_thread(self, **kwargs):
        self.calls.append(("fork", kwargs))
        return {"child_thread_id": "child-1", "raw": {"id": "child-1"}}

    def start_reflection_turn(self, **kwargs):
        self.calls.append(("turn", kwargs))
        prompt = str(kwargs["prompt"])
        marker = "Required receipt path: "
        receipt_path = Path(prompt.split(marker, 1)[1].splitlines()[0])
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({
            "schema_version": 1,
            "job_id": "job",
            "parent_session_id": "parent-1",
            "child_thread_id": "child-1",
            "status": self.receipt_status,
            "memory_changes": [],
            "skill_changes": [],
            "skipped_candidates": [],
            "validation_notes": [],
            "errors": [],
            "started_at": "2026-05-14T12:00:00Z",
            "finished_at": "2026-05-14T12:00:01Z",
        }), encoding="utf-8")
        return {"turn_id": "turn-1", "raw": {"id": "turn-1"}}


def _payload(repo: Path) -> dict[str, object]:
    """Return a raw Codex Stop payload fixture."""
    transcript = repo / "rollout.jsonl"
    transcript.write_text('{"role":"assistant","content":"done"}\n', encoding="utf-8")
    return {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(repo),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
    }


def test_enqueue_reflection_from_payload_creates_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = enqueue_reflection_from_payload(_payload(repo), home=tmp_path)

    assert result["status"] == "queued"
    assert result["job_id"]


def test_run_reflection_job_forks_and_starts_turn(tmp_path: Path, monkeypatch) -> None:
    skills_root = tmp_path / "skills"
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(skills_root))
    repo = tmp_path / "repo"
    repo.mkdir()
    queued = enqueue_reflection_from_payload(_payload(repo), home=tmp_path)
    client = FakeClient()

    result = run_reflection_job(queued["job_id"], home=tmp_path, client=client)

    assert result["status"] in {"succeeded", "skipped_empty"}
    assert [name for name, _ in client.calls] == ["fork", "turn"]
    registry = tmp_path / "session_reflection" / "child_threads" / "child-1.json"
    assert registry.exists()
```

- [ ] **Step 3: Run focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_session_reflection_app_server.py tests/test_session_reflection_runner.py -q
```

Expected: FAIL with missing `app_server` and `runner` symbols.

- [ ] **Step 4: Implement app-server transport and client**

Create `src/codex_self_evolution/session_reflection/app_server.py` with:

```python
from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any, Protocol


class JsonRpcTransport(Protocol):
    """Transport boundary used by tests and the real app-server proxy."""

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON-RPC request and return the result object."""


class AppServerError(RuntimeError):
    """Raised when app-server transport or protocol handling fails."""


class ProxyTransport:
    """JSON-RPC transport backed by ``codex app-server proxy`` over stdio."""

    def __init__(self, *, codex_binary: str = "codex", timeout_seconds: float = 900.0) -> None:
        """Store process settings for app-server proxy requests."""
        self.codex_binary = codex_binary
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a single request to the running app-server control socket."""
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        proc = subprocess.run(
            [self.codex_binary, "app-server", "proxy"],
            input=json.dumps(payload) + "\n",
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if proc.returncode != 0:
            raise AppServerError(f"app-server proxy exited {proc.returncode}: {proc.stderr.strip()[:400]}")
        for line in proc.stdout.splitlines():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == payload["id"]:
                if message.get("error"):
                    raise AppServerError(str(message["error"]))
                result = message.get("result")
                if isinstance(result, dict):
                    return result
                raise AppServerError("app-server result is not an object")
        raise AppServerError("app-server response missing")


class ReflectionAppServerClient:
    """Small typed wrapper for the app-server methods used by reflection."""

    def __init__(self, *, transport: JsonRpcTransport | None = None, timeout_seconds: float = 900.0) -> None:
        """Create a client with injectable transport for tests."""
        self.transport = transport or ProxyTransport(timeout_seconds=timeout_seconds)

    def fork_thread(
        self,
        *,
        parent_thread_id: str,
        transcript_path: str,
        cwd: str,
        model: str,
        ephemeral: bool,
        sandbox: str,
        approval_policy: str,
    ) -> dict[str, Any]:
        """Fork the parent thread as a memory-consolidation child."""
        params: dict[str, Any] = {
            "threadId": parent_thread_id,
            "model": model,
            "cwd": cwd,
            "threadSource": "memory_consolidation",
            "ephemeral": ephemeral,
            "sandbox": sandbox,
            "approvalPolicy": approval_policy,
        }
        if transcript_path:
            params["path"] = transcript_path
        raw = self.transport.request("thread/fork", params)
        child_thread_id = str(raw.get("id") or raw.get("threadId") or raw.get("sessionId") or "")
        if not child_thread_id:
            raise AppServerError("thread/fork response missing child id")
        return {"child_thread_id": child_thread_id, "raw": raw}

    def start_reflection_turn(
        self,
        *,
        child_thread_id: str,
        cwd: str,
        prompt: str,
        model: str,
        sandbox: str,
        approval_policy: str,
    ) -> dict[str, Any]:
        """Start the reflection turn on the forked child thread."""
        raw = self.transport.request("turn/start", {
            "threadId": child_thread_id,
            "cwd": cwd,
            "model": model,
            "approvalPolicy": approval_policy,
            "sandboxPolicy": {"mode": sandbox},
            "input": [{"type": "text", "text": prompt}],
        })
        turn_id = str(raw.get("id") or raw.get("turnId") or "")
        return {"turn_id": turn_id, "raw": raw}
```

- [ ] **Step 5: Implement runner**

Create `src/codex_self_evolution/session_reflection/runner.py` with:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import REFLECT_SKILL_PREFIX, build_paths
from ..config_file import load_config
from ..managed_skills.publish import codex_skills_dir
from ..storage import atomic_write_json, atomic_write_text
from .app_server import ReflectionAppServerClient
from .guard import evaluate_recursion_guard
from .paths import build_session_reflection_paths
from .prompt import build_reflection_prompt
from .state import (
    create_job_from_payload,
    load_job,
    register_child_thread,
    update_job_status,
    write_global_lock,
)
from .validation import validate_receipt


def enqueue_reflection_from_payload(
    payload: dict[str, Any],
    *,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Apply guard and create a queued reflection job for a Stop payload."""
    loaded = load_config(home=Path(home).expanduser().resolve() if home else None)
    cfg = loaded.config.session_reflection
    if not cfg.enabled:
        return {"status": "skipped", "reason": "disabled"}
    decision = evaluate_recursion_guard(payload, home=home)
    if decision.skip:
        return {"status": "skipped", "reason": decision.reason, "detail": decision.detail}
    return create_job_from_payload(payload, home=home)


def run_reflection_job(
    job_id: str,
    *,
    home: str | Path | None = None,
    client: ReflectionAppServerClient | None = None,
) -> dict[str, Any]:
    """Run one queued reflection job to completion and validate its receipt."""
    loaded = load_config(home=Path(home).expanduser().resolve() if home else None)
    cfg = loaded.config.session_reflection
    paths = build_session_reflection_paths(home=home, job_id=job_id)
    job = update_job_status(job_id, "running", home=home)
    lock_path = write_global_lock(home=home)
    try:
        repo_paths = build_paths(repo_root=job.get("cwd") or ".", state_dir=None)
        skills_root = codex_skills_dir()
        prompt = build_reflection_prompt(
            job_id=job_id,
            parent_session_id=str(job.get("parent_session_id") or ""),
            cwd=str(job.get("cwd") or "."),
            memory_user_path=repo_paths.memory_dir / "USER.md",
            memory_project_path=repo_paths.memory_dir / "MEMORY.md",
            skills_root=skills_root,
            receipt_path=paths.receipt_path,
        )
        atomic_write_text(paths.prompt_path, prompt)
        app = client or ReflectionAppServerClient(timeout_seconds=cfg.timeout_seconds)
        fork = app.fork_thread(
            parent_thread_id=str(job.get("parent_session_id") or ""),
            transcript_path=str(job.get("parent_transcript_path") or ""),
            cwd=str(job.get("cwd") or "."),
            model=cfg.model,
            ephemeral=cfg.ephemeral,
            sandbox=cfg.sandbox,
            approval_policy=cfg.approval_policy,
        )
        child_thread_id = str(fork["child_thread_id"])
        register_child_thread(
            child_thread_id=child_thread_id,
            parent_session_id=str(job.get("parent_session_id") or ""),
            job_id=job_id,
            home=home,
        )
        turn = app.start_reflection_turn(
            child_thread_id=child_thread_id,
            cwd=str(job.get("cwd") or "."),
            prompt=prompt,
            model=cfg.model,
            sandbox=cfg.sandbox,
            approval_policy=cfg.approval_policy,
        )
        validation = validate_receipt(
            paths.receipt_path,
            memory_roots=[repo_paths.memory_dir],
            skills_root=skills_root,
            skill_prefix=cfg.skill_prefix or REFLECT_SKILL_PREFIX,
        )
        atomic_write_json(paths.validation_path, validation)
        result = update_job_status(
            job_id,
            str(validation["status"]),
            home=home,
            child_thread_id=child_thread_id,
            turn_id=turn.get("turn_id"),
            validation=validation,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - worker failures must be captured in state
        return update_job_status(job_id, "failed", home=home, error=f"{type(exc).__name__}: {exc}")
    finally:
        if lock_path.exists():
            lock_path.unlink()


def session_reflection_status(*, home: str | Path | None = None) -> dict[str, Any]:
    """Return a compact read-only summary for CLI and diagnostics."""
    paths = build_session_reflection_paths(home=home)
    latest = None
    if paths.latest_path.is_file():
        try:
            import json
            latest = json.loads(paths.latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            latest = None
    return {
        "root": str(paths.root),
        "exists": paths.root.exists(),
        "latest": latest,
        "global_lock": str(paths.global_lock_path) if paths.global_lock_path.exists() else None,
    }
```

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_session_reflection_app_server.py tests/test_session_reflection_runner.py -q
```

Expected: PASS.

Commit:

```bash
git add src/codex_self_evolution/session_reflection tests/test_session_reflection_app_server.py tests/test_session_reflection_runner.py
git commit -m "feat: run reflection worker"
```

## Task 5: CLI And Stop Hook Integration

**Files:**
- Modify: `src/codex_self_evolution/cli.py`
- Modify: `src/codex_self_evolution/hooks/codex_bridge.py`
- Create: `tests/test_session_reflection_cli.py`
- Modify: `tests/test_codex_bridge.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_session_reflection_cli.py`:

```python
from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

from codex_self_evolution import cli


def _payload(tmp_path: Path) -> dict[str, object]:
    """Return a Codex Stop payload fixture for CLI tests."""
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"role":"assistant","content":"done"}\n', encoding="utf-8")
    return {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
    }


def test_stop_from_stdin_spawns_session_reflect_job(monkeypatch, capsys, tmp_path: Path) -> None:
    captured = {}

    def fake_enqueue(payload, **kwargs):
        captured["payload"] = payload
        captured["enqueue_kwargs"] = kwargs
        return {"status": "queued", "job_id": "job-1"}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["popen_kwargs"] = kwargs

    monkeypatch.setattr(cli, "enqueue_reflection_from_payload", fake_enqueue)
    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_payload(tmp_path))))

    exit_code = cli.main(["stop-review", "--from-stdin", "--state-dir", str(tmp_path / "state")])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    assert captured["payload"]["session_id"] == "parent-1"
    assert captured["argv"][1:4] == ["-m", "codex_self_evolution.cli", "session-reflect"]
    assert "--job" in captured["argv"]
    assert "job-1" in captured["argv"]
    assert "--home" in captured["argv"]
    assert captured["popen_kwargs"]["start_new_session"] is True


def test_stop_from_stdin_does_not_spawn_when_guard_skips(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "enqueue_reflection_from_payload", lambda payload, **kwargs: {"status": "skipped", "reason": "disabled"})
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not spawn")))
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_payload(tmp_path))))

    exit_code = cli.main(["stop-review", "--from-stdin"])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["continue"] is True


def test_session_reflect_job_cli_runs_worker(monkeypatch, capsys, tmp_path: Path) -> None:
    captured = {}

    def fake_run(job_id, **kwargs):
        captured["job_id"] = job_id
        captured.update(kwargs)
        return {"status": "succeeded", "job_id": job_id}

    monkeypatch.setattr(cli, "run_reflection_job", fake_run)

    exit_code = cli.main(["session-reflect", "--job", "job-1", "--home", str(tmp_path)])

    assert exit_code == 0
    assert captured["job_id"] == "job-1"
    assert captured["home"] == str(tmp_path)
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
```

Modify `tests/test_codex_bridge.py` with a passthrough assertion:

```python
def test_map_codex_stop_payload_preserves_thread_source_for_debug() -> None:
    result = map_codex_stop_payload(_codex_payload(threadSource="memory_consolidation"))

    assert result["codex_thread_source"] == "memory_consolidation"
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_session_reflection_cli.py tests/test_codex_bridge.py -q
```

Expected: FAIL because `session-reflect` parser and imports do not exist, and `codex_thread_source` is not preserved.

- [ ] **Step 3: Update Codex bridge passthrough**

In `src/codex_self_evolution/hooks/codex_bridge.py`, add to `mapped`:

```python
        "codex_thread_source": str(
            codex_payload.get("threadSource")
            or codex_payload.get("thread_source")
            or codex_payload.get("source")
            or ""
        ),
```

- [ ] **Step 4: Add CLI imports and parser**

In `src/codex_self_evolution/cli.py`, add imports:

```python
from .session_reflection.runner import (
    enqueue_reflection_from_payload,
    run_reflection_job,
    session_reflection_status,
)
```

Add parser setup after `stop_parser`:

```python
    reflect_parser = subparsers.add_parser(
        "session-reflect",
        help="Run or inspect Stop-time session reflection jobs.",
    )
    reflect_group = reflect_parser.add_mutually_exclusive_group(required=True)
    reflect_group.add_argument("--hook-payload")
    reflect_group.add_argument("--job")
    reflect_group.add_argument("--status", action="store_true")
    reflect_parser.add_argument("--home")
```

- [ ] **Step 5: Change Stop hook foreground path**

Replace `_handle_stop_from_stdin` internals after JSON validation with:

```python
    result = enqueue_reflection_from_payload(codex_payload, home=args.state_dir)
    if result.get("status") == "queued" and result.get("job_id"):
        child_argv = [
            sys.executable,
            "-m",
            "codex_self_evolution.cli",
            "session-reflect",
            "--job",
            str(result["job_id"]),
        ]
        if args.state_dir:
            child_argv.extend(["--home", args.state_dir])
        log_dir = Path(tempfile.gettempdir()) / "codex-self-evolution"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"session-reflect-{os.getpid()}-{int(os.times()[4])}.log"
        try:
            log_handle = open(log_path, "w", encoding="utf-8")
        except OSError:
            log_handle = subprocess.DEVNULL  # type: ignore[assignment]
        try:
            subprocess.Popen(
                child_argv,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            print(json.dumps({"continue": True, "warning": f"failed to spawn reflection worker: {exc}"}))
            return 0
    print(json.dumps({"continue": True}))
    return 0
```

Important: leave `_run_stop_review()` and non-stdin `stop-review --hook-payload` unchanged so old reviewer remains manually callable.

- [ ] **Step 6: Add session-reflect command dispatch**

In `main()`, add before `compile`:

```python
        elif args.command == "session-reflect":
            if args.status:
                result = session_reflection_status(home=args.home)
            elif args.hook_payload:
                payload = json.loads(Path(args.hook_payload).read_text(encoding="utf-8"))
                queued = enqueue_reflection_from_payload(payload, home=args.home)
                if queued.get("status") == "queued" and queued.get("job_id"):
                    result = run_reflection_job(str(queued["job_id"]), home=args.home)
                else:
                    result = queued
            else:
                result = run_reflection_job(args.job, home=args.home)
```

- [ ] **Step 7: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_session_reflection_cli.py tests/test_codex_bridge.py -q
```

Expected: PASS.

Commit:

```bash
git add src/codex_self_evolution/cli.py src/codex_self_evolution/hooks/codex_bridge.py tests/test_session_reflection_cli.py tests/test_codex_bridge.py
git commit -m "feat: wire reflection stop hook"
```

## Task 6: Diagnostics, Plugin Metadata, And Docs

**Files:**
- Modify: `src/codex_self_evolution/diagnostics.py`
- Modify: `src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json`
- Modify: `plugins/codex-self-evolution/.codex-plugin/plugin.json`
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `tests/test_diagnostics.py`
- Create: `tests/test_session_reflection_diagnostics.py`

- [ ] **Step 1: Write failing diagnostics test**

Create `tests/test_session_reflection_diagnostics.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.diagnostics import collect_status


def test_status_reports_session_reflection_latest(tmp_path: Path) -> None:
    root = tmp_path / "session_reflection"
    root.mkdir()
    (root / "latest.json").write_text(json.dumps({
        "job_id": "job-1",
        "status": "failed",
        "error": "app-server unavailable",
    }), encoding="utf-8")

    status = collect_status(home=tmp_path)

    assert status["session_reflection"]["exists"] is True
    assert status["session_reflection"]["latest"]["job_id"] == "job-1"
    assert status["session_reflection"]["latest"]["status"] == "failed"
```

- [ ] **Step 2: Run diagnostics test and verify it fails**

Run:

```bash
uv run pytest tests/test_session_reflection_diagnostics.py -q
```

Expected: FAIL with missing `session_reflection` key.

- [ ] **Step 3: Add diagnostics summary**

In `src/codex_self_evolution/diagnostics.py`, import:

```python
from .session_reflection.runner import session_reflection_status
```

Add to `collect_status()` output:

```python
        "session_reflection": session_reflection_status(home=home_dir),
```

Add `"session-reflect"` to `_LOG_KINDS`:

```python
_LOG_KINDS = {"stop-review", "session-reflect", "scan", "compile", "migrate-worktrees", "session-start", "status"}
```

- [ ] **Step 4: Update plugin command metadata**

In both plugin manifests, add a command entry after `stop-review`:

```json
    {
      "name": "session-reflect-status",
      "command": "codex-self-evolution session-reflect --status"
    },
```

Do not change `hooks.json`; Stop still invokes `codex-self-evolution stop-review --from-stdin`.

- [ ] **Step 5: Update docs**

In `README.md`, update lifecycle text so it says:

```markdown
Stop
  -> 快速创建 session reflection job
  -> 后台通过 Codex app-server fork 当前 thread
  -> child 写入 memory / csep-reflect-* skill / receipt
  -> 父进程后验校验 receipt 和写入边界
```

Add command references:

```markdown
| `codex-self-evolution session-reflect --status` | 查看最近一次 session reflection job、失败原因和 lock 状态。 |
| `codex-self-evolution session-reflect --hook-payload <file>` | 前台重放某次 Codex Stop payload，便于调试 app-server fork 和 receipt 校验。 |
```

In `docs/getting-started.md`, add a short debug section:

```markdown
## Session Reflection 调试

Stop hook 默认只负责快速返回，并在后台启动 `session-reflect --job <job_id>`。

常用检查命令：

```bash
codex-self-evolution session-reflect --status
codex-self-evolution status
```

失败时优先看：

```text
~/.codex-self-evolution/session_reflection/latest.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/receipt.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/validation.json
/tmp/codex-self-evolution/session-reflect-*.log
```
```

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_session_reflection_diagnostics.py tests/test_plugin_bundle_hooks.py -q
```

Expected: PASS.

Commit:

```bash
git add src/codex_self_evolution/diagnostics.py src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json plugins/codex-self-evolution/.codex-plugin/plugin.json README.md docs/getting-started.md tests/test_session_reflection_diagnostics.py
git commit -m "docs: expose reflection status"
```

## Task 7: Full Verification And Local Smoke

**Files:**
- No new production files.
- Optional test fixture: `tests/fixtures/session_reflection/stop_payload.json` if repeated local smoke needs a stable payload.

- [ ] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 3: Run local install**

Run:

```bash
scripts/install.sh
```

Expected: completes without errors and refreshes the local plugin cache.

- [ ] **Step 4: Run read-only status smoke**

Run:

```bash
codex-self-evolution session-reflect --status
codex-self-evolution status
```

Expected: both commands print JSON and do not expose secrets.

- [ ] **Step 5: Run isolated enqueue smoke without real app-server**

Create a temporary payload manually under `/tmp` and run:

```bash
uv run codex-self-evolution session-reflect --hook-payload /tmp/csep-session-reflection-stop.json --home /tmp/csep-session-reflection-home
```

Expected when no running app-server is available: command exits non-zero only if an unhandled exception leaks. The desired MVP behavior is JSON output with `status = "failed"` and an app-server error captured in `latest.json`.

- [ ] **Step 6: Review changed files**

Run:

```bash
git status --short
git diff --stat
```

Expected changed files are limited to the planned source, tests, manifests, and docs.

- [ ] **Step 7: Final commit if any verification fixes were needed**

If verification required small fixes, commit them:

```bash
git add <fixed-files>
git commit -m "test: stabilize session reflection worker"
```

## Self-Review Checklist

Spec coverage:

- Stop hook quick return: Task 5 tests `stop-review --from-stdin` prints `{"continue": true}`.
- Default spawn reflection worker, not old reviewer: Task 5 changes foreground path to spawn `session-reflect --job`.
- Invalid payload non-blocking: existing malformed JSON behavior remains in `_handle_stop_from_stdin`; keep it covered by existing `test_cli_from_stdin_tolerates_malformed_json`.
- Recursion guard: Task 2 covers thread source, child registry, transcript marker, existing parent job, and global lock.
- App-server fork: Task 4 covers `thread/fork` params and `turn/start` prompt marker.
- Receipt validation: Task 3 covers schema, boundaries, hashes, skill validation, invalid marker.
- Memory boundary: Task 3 limits paths to per-project `memory/USER.md` and `memory/MEMORY.md`.
- Skill boundary: Task 3 limits paths to `<skills_root>/csep-reflect-*/SKILL.md`.
- CLI debug commands: Task 5 adds `session-reflect --hook-payload`, `--job`, and `--status`.
- Diagnostics and docs: Task 6 surfaces status and documents debug paths.

Placeholder scan:

- No unresolved placeholder markers or cross-task shorthand steps.
- Every code-changing step names exact paths and includes code snippets or exact insertion text.
- Test commands use `uv run pytest`, matching this machine.

Known risk to verify during execution:

- `ProxyTransport` assumes `codex app-server proxy` accepts one JSON-RPC request on stdin and returns a matching response line. If real proxy framing differs, keep fake-transport tests and adjust only `ProxyTransport`; do not change runner, guard, or validation semantics.
- `ThreadForkParams` schema says `threadId` is required even when `path` is supplied. The MVP sends both, with `path` as fallback context. If live app-server rejects `path`, remove only the `path` param and record the live behavior in docs.
- `validate_receipt` writes `.csep-invalid.json` per the design, while existing skill synthesis writes `CSEP_INVALID.json`. Keep the reflection marker name from the spec unless the user asks to standardize.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-session-reflection-worker.md`. Two execution options:

1. Subagent-Driven (recommended): dispatch a fresh worker per task, review between tasks, faster iteration.
2. Inline Execution: execute tasks in this session using `superpowers:executing-plans`, with checkpoints after each task.

Which approach?
