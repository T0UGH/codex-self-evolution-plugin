# Phase 5B Usage Context Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record low-sensitive stable-memory injection usage and attach deterministic context labels to archive/reflection metadata.

**Architecture:** Add a small `memory_usage` module that atomically updates `memory/usage.json` from `SessionStart`, then add a deterministic `context_labels` classifier in the session recall layer. Archive stores labels in existing `sessions.metadata_json`; reflection job creation copies labels into job metadata and reflection prompts so child workers can treat external or injected context conservatively. No SQLite schema migration, automatic deletion, summary generation, candidate layer, or skill staging is included.

**Tech Stack:** Python 3.11+ stdlib, pytest, existing `Paths` runtime model, existing SessionStart/Stop hook and session_recall metadata flow.

---

## File Structure

- Create `src/codex_self_evolution/memory_usage.py`
  - Owns `memory/usage.json` schema v1.
  - Exposes `record_memory_injection(memory_dir: Path, source: str, source_path: Path, injected_at: str | None = None) -> dict[str, object]`.
  - Uses existing `atomic_write_json()` / `load_json()` helpers.
- Modify `src/codex_self_evolution/hooks/session_start.py`
  - Calls `record_memory_injection()` after stable memory selection succeeds.
  - Adds `memory_usage_path` and `memory_usage` to `stable_background` for debugging.
  - Keeps hook failure-safe: usage write failures must not block SessionStart.
- Create `src/codex_self_evolution/session_recall/context_labels.py`
  - Owns deterministic, low-sensitive label derivation.
  - Exposes `derive_context_labels(parsed: ParsedSession) -> list[str]`.
- Modify `src/codex_self_evolution/session_recall/archive.py`
  - Derives labels before store archive and writes them into `ParsedSession.metadata["context_labels"]`.
- Modify `src/codex_self_evolution/session_reflection/state.py`
  - Copies `context_labels` from Stop payload into queued job metadata.
- Modify `src/codex_self_evolution/session_reflection/runner.py`
  - Passes `context_labels` from job to prompt builder.
- Modify `src/codex_self_evolution/session_reflection/prompt.py`
  - Adds prompt text that lists context labels and tells the child not to promote external or third-party context as durable user preference by default.
- Modify tests:
  - `tests/test_session_start.py`
  - `tests/test_session_recall_store.py`
  - `tests/test_session_recall_cli.py`
  - `tests/test_session_reflection_runner.py`

## Scope Boundaries

- Do not store transcript text in `usage.json`.
- Do not record secrets, cookies, tokens, user IDs, or source excerpts in usage metadata.
- Do not change session_recall SQLite schema. Use existing `metadata_json`.
- Do not make labels block archive, recall, or reflection.
- Do not add automatic memory deletion, summary generation, candidates, consolidation, or skill staging.
- Do not make SessionStart fail when `usage.json` is malformed or unwritable.

## Task 1: Add Memory Usage Writer

**Files:**
- Create: `src/codex_self_evolution/memory_usage.py`
- Modify: `tests/test_session_start.py`
- Test: `tests/test_session_start.py`

- [ ] **Step 1: Add failing usage assertions for MEMORY.md fallback injection**

In `tests/test_session_start.py`, extend `test_session_start_injects_memory_and_short_recall_pointer` after the existing `memory_summary_meta_path` assertion:

```python
    usage_path = state / "memory" / "usage.json"
    assert result["stable_background"]["memory_usage_path"] == str(usage_path)
    assert result["stable_background"]["memory_usage"]["status"] == "recorded"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    assert usage["schema_version"] == 1
    assert usage["items"]["MEMORY.md"]["kind"] == "memory"
    assert usage["items"]["MEMORY.md"]["injected_count"] == 1
    assert usage["items"]["MEMORY.md"]["citation_count"] == 0
    assert usage["items"]["MEMORY.md"]["last_cited_at"] == ""
    assert "Run focused tests first." not in json.dumps(usage, ensure_ascii=False)
```

- [ ] **Step 2: Run the fallback usage test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_start.py::test_session_start_injects_memory_and_short_recall_pointer
```

Expected: FAIL with `KeyError: 'memory_usage_path'`.

- [ ] **Step 3: Create the memory usage module**

Create `src/codex_self_evolution/memory_usage.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from .storage import atomic_write_json, load_json, utc_now


USAGE_SCHEMA_VERSION = 1


def record_memory_injection(
    *,
    memory_dir: Path,
    source: str,
    source_path: Path,
    injected_at: str | None = None,
) -> dict[str, Any]:
    """Record that SessionStart injected one stable-memory artifact."""
    usage_path = memory_dir / "usage.json"
    usage = _load_usage(usage_path)
    items = usage.setdefault("items", {})
    if not isinstance(items, dict):
        items = {}
        usage["items"] = items

    item_key = _usage_item_key(memory_dir=memory_dir, source=source, source_path=source_path)
    item = items.get(item_key)
    if not isinstance(item, dict):
        item = _empty_item(source)
        items[item_key] = item

    now = injected_at or utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    item["kind"] = _kind_for_source(source)
    item["injected_count"] = int(item.get("injected_count") or 0) + 1
    item["last_injected_at"] = now
    item.setdefault("citation_count", 0)
    item.setdefault("last_cited_at", "")
    atomic_write_json(usage_path, usage)
    return {"status": "recorded", "path": str(usage_path), "item_key": item_key, "item": item}


def _load_usage(path: Path) -> dict[str, Any]:
    """Load usage metadata, resetting malformed files to an empty schema."""
    try:
        raw = load_json(path)
    except (OSError, ValueError):
        return {"schema_version": USAGE_SCHEMA_VERSION, "items": {}}
    if not isinstance(raw, dict) or raw.get("schema_version") != USAGE_SCHEMA_VERSION:
        return {"schema_version": USAGE_SCHEMA_VERSION, "items": {}}
    if not isinstance(raw.get("items"), dict):
        raw["items"] = {}
    return raw


def _empty_item(source: str) -> dict[str, Any]:
    """Return a schema-v1 usage item without sensitive content."""
    return {
        "kind": _kind_for_source(source),
        "injected_count": 0,
        "last_injected_at": "",
        "citation_count": 0,
        "last_cited_at": "",
    }


def _kind_for_source(source: str) -> str:
    """Map stable-memory source names to usage item kinds."""
    return "memory_summary" if source == "memory_summary.md" else "memory"


def _usage_item_key(*, memory_dir: Path, source: str, source_path: Path) -> str:
    """Return the stable usage key for a memory artifact."""
    try:
        return str(source_path.relative_to(memory_dir))
    except ValueError:
        return source
```

- [ ] **Step 4: Wire SessionStart to record injection usage**

In `src/codex_self_evolution/hooks/session_start.py`, add the import:

```python
from ..memory_usage import record_memory_injection
```

After `memory_heading` is computed, add:

```python
    memory_usage = _safe_record_memory_usage(paths, memory_selection)
```

Add these keys inside `stable_background`:

```python
            "memory_usage_path": str(paths.memory_dir / "usage.json"),
            "memory_usage": memory_usage,
```

At the end of the file, add:

```python
def _safe_record_memory_usage(paths, memory_selection) -> dict[str, Any]:
    """Record stable-memory injection without making SessionStart fragile."""
    try:
        return record_memory_injection(
            memory_dir=paths.memory_dir,
            source=memory_selection.source,
            source_path=memory_selection.source_path,
        )
    except Exception as exc:  # noqa: BLE001 - SessionStart must never fail on telemetry.
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
```

- [ ] **Step 5: Run the fallback usage test and verify it passes**

Run:

```bash
uv run pytest -q tests/test_session_start.py::test_session_start_injects_memory_and_short_recall_pointer
```

Expected: `1 passed`.

- [ ] **Step 6: Add summary usage assertions**

In `test_session_start_prefers_valid_memory_summary`, after existing prefix assertions, add:

```python
    usage = json.loads((memory_dir / "usage.json").read_text(encoding="utf-8"))
    assert usage["items"]["memory_summary.md"]["kind"] == "memory_summary"
    assert usage["items"]["memory_summary.md"]["injected_count"] == 1
    assert "Hot summary only." not in json.dumps(usage, ensure_ascii=False)
    assert "Full detail should stay cold." not in json.dumps(usage, ensure_ascii=False)
```

- [ ] **Step 7: Add usage count increment test**

Add this test to `tests/test_session_start.py`:

```python
def test_session_start_increments_memory_usage_without_storing_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# MEMORY\n\nSensitive detail should not enter usage.\n", encoding="utf-8")

    session_start(cwd=repo, state_dir=state)
    session_start(cwd=repo, state_dir=state)

    usage = json.loads((memory_dir / "usage.json").read_text(encoding="utf-8"))
    item = usage["items"]["MEMORY.md"]
    assert item["injected_count"] == 2
    assert item["last_injected_at"]
    assert "Sensitive detail should not enter usage." not in json.dumps(usage, ensure_ascii=False)
```

- [ ] **Step 8: Run SessionStart tests**

Run:

```bash
uv run pytest -q tests/test_session_start.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add src/codex_self_evolution/memory_usage.py src/codex_self_evolution/hooks/session_start.py tests/test_session_start.py
git commit -m "feat: record stable memory injection usage"
```

## Task 2: Derive Context Labels During Archive

**Files:**
- Create: `src/codex_self_evolution/session_recall/context_labels.py`
- Modify: `src/codex_self_evolution/session_recall/archive.py`
- Modify: `tests/test_session_recall_store.py`
- Modify: `tests/test_session_recall_cli.py`
- Test: `tests/test_session_recall_store.py`
- Test: `tests/test_session_recall_cli.py`

- [ ] **Step 1: Add store metadata preservation test**

In `tests/test_session_recall_store.py`, add:

```python
def test_store_preserves_context_labels_in_session_metadata(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    parsed = _parsed_session(tmp_path)
    parsed.metadata["context_labels"] = ["agent_injected_context", "local_repo_code"]

    store.archive(parsed)

    row = store._conn.execute(
        "SELECT metadata_json FROM sessions WHERE session_id = ?",
        (parsed.session_id,),
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata["context_labels"] == ["agent_injected_context", "local_repo_code"]
```

Also add `import json` at the top of `tests/test_session_recall_store.py`.

- [ ] **Step 2: Run the metadata preservation test**

Run:

```bash
uv run pytest -q tests/test_session_recall_store.py::test_store_preserves_context_labels_in_session_metadata
```

Expected: PASS. This confirms Task 2 can use existing `metadata_json` and does not need a schema migration.

- [ ] **Step 3: Add CLI archive label test**

In `tests/test_session_recall_cli.py`, add this test:

```python
def test_csep_session_archive_records_context_labels(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "s1", "cwd": str(repo)}}),
                json.dumps({"role": "developer", "content": "# AGENTS.md instructions"}),
                json.dumps({"role": "user", "content": "以后这里优先跑 uv run pytest -q"}),
                json.dumps({"role": "assistant", "content": "reading /tmp/example.py"}),
                json.dumps({"role": "tool", "name": "web.run", "content": "external article body"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert csep.main(["session-archive", "--transcript-path", str(transcript), "--cwd", str(repo), "--session-id", "s1"]) == 0
    out = json.loads(capsys.readouterr().out)

    assert out["status"] == "archived"
    assert out["context_labels"] == [
        "agent_injected_context",
        "external_web",
        "local_repo_code",
        "user_instruction",
    ]
```

- [ ] **Step 4: Run the CLI label test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_recall_cli.py::test_csep_session_archive_records_context_labels
```

Expected: FAIL with `KeyError: 'context_labels'`.

- [ ] **Step 5: Create deterministic context label classifier**

Create `src/codex_self_evolution/session_recall/context_labels.py`:

```python
from __future__ import annotations

from .models import ParsedSession


LABEL_ORDER = (
    "agent_injected_context",
    "external_web",
    "local_repo_code",
    "third_party_document",
    "user_instruction",
)

_LOCAL_PATH_MARKERS = ("/Users/", "/tmp/", "/private/tmp/", "src/", "tests/", ".py", ".md", ".toml")
_USER_INSTRUCTION_MARKERS = ("以后", "记住", "偏好", "规则", "优先", "不要", "总是", "每次", "prefer", "always", "never")
_AGENT_CONTEXT_MARKERS = ("AGENTS.md", "DeveloperInstructions", "Stable Background", "Recall Policy", "system", "developer")
_EXTERNAL_TOOL_MARKERS = ("web.run", "search_query", "browser", "chrome", "open_url")
_THIRD_PARTY_MARKERS = ("third_party_document", "pdf", "docx", "Google Docs", "external document")


def derive_context_labels(parsed: ParsedSession) -> list[str]:
    """Derive low-sensitive source labels from parsed message metadata and text."""
    labels: set[str] = set()
    repo_root = str(parsed.metadata.get("repo_root") or parsed.cwd or "")
    for message in parsed.messages:
        role = message.role.lower()
        raw_event = message.raw_event_type.lower()
        tool_name = message.tool_name.lower()
        text = message.content
        lowered = text.lower()

        if role in {"system", "developer"} or any(marker.lower() in lowered for marker in _AGENT_CONTEXT_MARKERS):
            labels.add("agent_injected_context")
        if tool_name in {"web.run", "browser", "chrome"} or any(marker in tool_name for marker in ("web", "browser", "chrome")):
            labels.add("external_web")
        if any(marker.lower() in lowered for marker in _EXTERNAL_TOOL_MARKERS) or "http://" in lowered or "https://" in lowered:
            labels.add("external_web")
        if repo_root and repo_root in text:
            labels.add("local_repo_code")
        if any(marker in text for marker in _LOCAL_PATH_MARKERS):
            labels.add("local_repo_code")
        if role == "user" and any(marker.lower() in lowered for marker in _USER_INSTRUCTION_MARKERS):
            labels.add("user_instruction")
        if any(marker.lower() in lowered for marker in _THIRD_PARTY_MARKERS) or raw_event in {"document", "attachment"}:
            labels.add("third_party_document")
    return [label for label in LABEL_ORDER if label in labels]
```

- [ ] **Step 6: Attach labels in archive flow**

In `src/codex_self_evolution/session_recall/archive.py`, add:

```python
from .context_labels import derive_context_labels
```

In `archive_transcript()`, after parsing and before `store.archive(parsed)`, add:

```python
        labels = derive_context_labels(parsed)
        parsed.metadata["context_labels"] = labels
        result = store.archive(parsed)
        result["context_labels"] = labels
        return result
```

In `archive_claude_transcript()`, apply the same pattern:

```python
        labels = derive_context_labels(parsed)
        parsed.metadata["context_labels"] = labels
        result = store.archive(parsed)
        result["context_labels"] = labels
        return result
```

- [ ] **Step 7: Run archive label tests**

Run:

```bash
uv run pytest -q tests/test_session_recall_store.py::test_store_preserves_context_labels_in_session_metadata tests/test_session_recall_cli.py::test_csep_session_archive_records_context_labels
```

Expected: both tests pass.

- [ ] **Step 8: Run recall CLI tests**

Run:

```bash
uv run pytest -q tests/test_session_recall_cli.py tests/test_session_recall_store.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 2**

```bash
git add src/codex_self_evolution/session_recall/context_labels.py src/codex_self_evolution/session_recall/archive.py tests/test_session_recall_store.py tests/test_session_recall_cli.py
git commit -m "feat: label archived session context"
```

## Task 3: Propagate Context Labels To Reflection Jobs

**Files:**
- Modify: `src/codex_self_evolution/session_reflection/state.py`
- Modify: `src/codex_self_evolution/session_reflection/runner.py`
- Modify: `src/codex_self_evolution/session_reflection/prompt.py`
- Modify: `tests/test_session_reflection_runner.py`
- Test: `tests/test_session_reflection_runner.py`

- [ ] **Step 1: Add job metadata assertion**

In `tests/test_session_reflection_runner.py`, update `test_enqueue_reflection_from_payload_queues_when_trigger_hits` after the `trigger_decision` assertion:

```python
    assert job["context_labels"] == ["external_web", "user_instruction"]
```

Change the payload setup in that test to include labels:

```python
    payload = _payload(repo, context_labels=["external_web", "user_instruction"])
```

- [ ] **Step 2: Run the enqueue test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_enqueue_reflection_from_payload_queues_when_trigger_hits
```

Expected: FAIL with `KeyError: 'context_labels'`.

- [ ] **Step 3: Persist context labels in reflection jobs**

In `src/codex_self_evolution/session_reflection/state.py`, add:

```python
def _payload_context_labels(payload: dict[str, Any]) -> list[str]:
    """Return deterministic string context labels from a Stop payload."""
    raw = payload.get("context_labels")
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for value in raw:
        if isinstance(value, str) and value and value not in labels:
            labels.append(value)
    return labels
```

In `create_job_from_payload()`, add to the base `job` object:

```python
        "context_labels": _payload_context_labels(payload),
```

- [ ] **Step 4: Add prompt propagation assertion**

In `test_run_reflection_job_forks_starts_registers_validates_and_cleans_lock`, create the job with labels:

```python
    job = create_job_from_payload(
        _payload(repo, context_labels=["external_web", "third_party_document"]),
        home=home,
    )
```

Add prompt assertions:

```python
    assert "Context labels: external_web, third_party_document" in client.start_calls[0]["prompt"]
    assert "Do not promote external_web or third_party_document content as durable user preference by default." in client.start_calls[0]["prompt"]
```

- [ ] **Step 5: Run the prompt test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_run_reflection_job_forks_starts_registers_validates_and_cleans_lock
```

Expected: FAIL because prompt does not include context labels yet.

- [ ] **Step 6: Add context labels to prompt builder**

In `src/codex_self_evolution/session_reflection/prompt.py`, extend the signature:

```python
    context_labels: list[str] | None = None,
```

Before the final `return`, add:

```python
    labels = list(context_labels or [])
    context_label_text = ", ".join(labels) if labels else "none"
    contamination_instruction = (
        "Do not promote external_web or third_party_document content as durable user preference by default.\n"
        if any(label in {"external_web", "third_party_document"} for label in labels)
        else ""
    )
```

In the returned prompt, after `Skill generation mode`, add:

```python
        f"Context labels: {context_label_text}\n"
        f"{contamination_instruction}\n"
```

- [ ] **Step 7: Pass labels from runner to prompt**

In `src/codex_self_evolution/session_reflection/runner.py`, add the keyword when calling `build_reflection_prompt()`:

```python
            context_labels=[str(label) for label in job.get("context_labels") or []],
```

- [ ] **Step 8: Run reflection runner tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_enqueue_reflection_from_payload_queues_when_trigger_hits tests/test_session_reflection_runner.py::test_run_reflection_job_forks_starts_registers_validates_and_cleans_lock
```

Expected: both tests pass.

- [ ] **Step 9: Run full reflection runner file**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py
```

Expected: all tests pass.

- [ ] **Step 10: Commit Task 3**

```bash
git add src/codex_self_evolution/session_reflection/state.py src/codex_self_evolution/session_reflection/runner.py src/codex_self_evolution/session_reflection/prompt.py tests/test_session_reflection_runner.py
git commit -m "feat: pass context labels to reflection"
```

## Task 4: Derive Reflection Labels From Transcript

**Files:**
- Modify: `src/codex_self_evolution/session_reflection/state.py`
- Modify: `tests/test_session_reflection_runner.py`
- Test: `tests/test_session_reflection_runner.py`

- [ ] **Step 1: Add queued reflection transcript-label test**

In `tests/test_session_reflection_runner.py`, add:

```python
def test_enqueue_reflection_derives_context_labels_from_transcript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Queued reflection jobs get labels even when raw Stop payload has none."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    Path(str(payload["transcript_path"])).write_text(
        "\n".join(
            [
                json.dumps({"role": "developer", "content": "# AGENTS.md instructions"}),
                json.dumps({"role": "user", "content": "以后把这个 workflow 沉淀成 skill"}),
                json.dumps({"role": "assistant", "content": f"reading {repo / 'src/example.py'}"}),
                json.dumps({"role": "tool", "name": "web.run", "content": "external article body"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setattr(
        "codex_self_evolution.session_reflection.runner.app_server_proxy_status",
        lambda: {"available": True, "reason": None, "socket_path": str(tmp_path / "app-server.sock")},
    )

    result = enqueue_reflection_from_payload(payload, home=home)

    assert result["status"] == "queued"
    assert result["job"]["context_labels"] == [
        "agent_injected_context",
        "external_web",
        "local_repo_code",
        "user_instruction",
    ]
```

- [ ] **Step 2: Run the queued reflection transcript-label test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_enqueue_reflection_derives_context_labels_from_transcript
```

Expected: FAIL because `context_labels` is still empty when the Stop payload does not include labels.

- [ ] **Step 3: Derive labels in queued reflection job creation**

In `src/codex_self_evolution/session_reflection/state.py`, add imports:

```python
from ..session_recall.context_labels import derive_context_labels
from ..session_recall.parser import parse_codex_jsonl
```

Replace `_payload_context_labels()` with:

```python
def _payload_context_labels(payload: dict[str, Any]) -> list[str]:
    """Return context labels from payload or derive them from the parent transcript."""
    raw = payload.get("context_labels")
    if isinstance(raw, list):
        labels: list[str] = []
        for value in raw:
            if isinstance(value, str) and value and value not in labels:
                labels.append(value)
        return labels

    transcript_path = _payload_text(payload, "transcript_path", "codex_transcript_path")
    if not transcript_path:
        return []
    try:
        parsed = parse_codex_jsonl(
            transcript_path,
            session_id=_payload_text(payload, "session_id", "thread_id"),
            cwd=_payload_text(payload, "cwd"),
        )
    except Exception:  # noqa: BLE001 - labels are best-effort metadata.
        return []
    return derive_context_labels(parsed)
```

This derives labels only once a reflection job is being created. It does not add synchronous work to archive-only Stop hook paths.

- [ ] **Step 4: Run reflection label tests**

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_enqueue_reflection_from_payload_queues_when_trigger_hits tests/test_session_reflection_runner.py::test_enqueue_reflection_derives_context_labels_from_transcript
```

Expected: both tests pass.

- [ ] **Step 5: Run full reflection runner file**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/codex_self_evolution/session_reflection/state.py tests/test_session_reflection_runner.py
git commit -m "feat: derive reflection context labels"
```

## Task 5: Document 5B Runtime Metadata

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/specs/2026-06-12-memory-governance-design.md`
- Test: focused and full suite

- [ ] **Step 1: Update architecture directory tree**

In `docs/architecture.md`, update the `memory/` tree from:

```text
            ├── memory_summary.meta.json
            └── refs/
```

to:

```text
            ├── memory_summary.meta.json
            ├── usage.json
            └── refs/
```

- [ ] **Step 2: Update architecture runtime description**

In `docs/architecture.md`, append this sentence to the paragraph below the tree:

```markdown
`usage.json` 只记录注入次数和时间等低敏元数据，不保存 memory 正文、transcript 正文或用户隐私标识。
```

- [ ] **Step 3: Update Phase 5B spec with first-implementation boundary**

In `docs/superpowers/specs/2026-06-12-memory-governance-design.md`, under `### 使用反馈`, add:

```markdown
Phase 5B 首版只在 `SessionStart` 记录 `MEMORY.md` / `memory_summary.md` 的注入次数和最近注入时间。`citation_count`、`open_count` 和 `trigger_count` 保留为 schema 字段或后续扩展，不在首版从 transcript 正文反推。
```

Under `### 污染标签`, add:

```markdown
标签首版以确定性规则写入 archive metadata；reflection 只在确定要创建 queued job 时从 Stop payload 或 parent transcript 派生同一组标签。Stop hook 不为了标签同步等待异步 archive 子进程。
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_session_start.py tests/test_session_recall_cli.py tests/test_session_recall_store.py tests/test_session_reflection_runner.py tests/test_csep_cli.py
```

Expected: all tests pass.

- [ ] **Step 5: Run full regression**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 7: Run plugin mirror check**

Run:

```bash
python3 scripts/sync-plugin-bundle.py --check
```

Expected:

```text
plugin bundle mirrors match canonical source: src/codex_self_evolution/plugin_bundle
```

- [ ] **Step 8: Run runtime smoke through current source shim**

Run:

```bash
tmpdir="$(mktemp -d)"
py="$(uv run python -c 'import sys; print(sys.executable)')"
cat > "$tmpdir/csep" <<SH
#!/usr/bin/env bash
export PYTHONPATH="$PWD/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$py" -m codex_self_evolution.csep "\$@"
SH
chmod +x "$tmpdir/csep"
PATH="$tmpdir:$PATH" CSEP_BIN=csep PYTHON="$py" CSEP_SMOKE_SKIP_CONFIG=1 CSEP_SMOKE_SKIP_STATUS_VERSION_ASSERT=1 scripts/smoke-runtime.sh
```

Expected output includes:

```text
==> runtime smoke passed
```

- [ ] **Step 9: Commit Task 5**

```bash
git add docs/architecture.md docs/superpowers/specs/2026-06-12-memory-governance-design.md
git commit -m "docs: document memory usage metadata"
```

## Self-Review Notes

- Spec coverage:
  - SessionStart injection usage is covered by Task 1.
  - Usage metadata avoids transcript/memory content by assertion in Task 1.
  - Archive `context_labels` are covered by Task 2.
  - Reflection prompt can read labels from payload or queued-job transcript derivation via Tasks 3-4.
  - Labels missing do not block archive/recall/reflection because all code paths default to empty lists.
- Scope boundaries:
  - No memory deletion or ranking is introduced.
  - No summary generation is introduced.
  - No candidate/consolidation job is introduced.
  - No skill staging or promotion change is introduced.
  - No session_recall schema migration is needed.
- Type consistency:
  - `record_memory_injection()` returns a JSON-serializable dict used directly in `stable_background["memory_usage"]`.
  - `derive_context_labels()` accepts a `ParsedSession` and returns ordered `list[str]`.
  - Reflection jobs store `context_labels: list[str]`.
  - `build_reflection_prompt()` accepts `context_labels: list[str] | None = None`.
- Execution note:
  - Stop hook still spawns archive asynchronously. Reflection derives labels only after trigger policy has already decided to queue a job, so archive-only Stop paths stay lightweight.
