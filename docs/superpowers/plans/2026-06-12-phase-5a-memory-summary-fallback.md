# Phase 5A Memory Summary Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `SessionStart` prefer a verified `memory_summary.md` while safely falling back to the existing `MEMORY.md` behavior.

**Architecture:** Add a structured stable-memory selector in `storage.py` that validates `memory_summary.meta.json` against both `MEMORY.md` and `memory_summary.md` hashes. `hooks/session_start.py` consumes that selector to build the injected prefix and expose source/fallback metadata in the existing `stable_background` payload. No reflection write path or summary generation changes are included.

**Tech Stack:** Python 3.11+ stdlib, pytest, existing `Paths` runtime state model, existing Codex `SessionStart` hook protocol.

---

## File Structure

- Modify `src/codex_self_evolution/storage.py`
  - Add `StableMemorySelection` dataclass.
  - Add SHA-256 helpers and summary meta validation.
  - Change `load_stable_memory(paths)` to return `StableMemorySelection`.
- Modify `src/codex_self_evolution/hooks/session_start.py`
  - Use the structured selection to choose the heading and content.
  - Preserve existing `current_memory_md`, `memory_path`, `memory_refs_dir`, and `combined_prefix` fields.
  - Add `memory_source`, `memory_source_path`, `memory_summary_path`, `memory_summary_meta_path`, `memory_fallback_used`, and `memory_fallback_reason`.
- Modify `tests/test_session_start.py`
  - Cover valid summary selection and fallback reasons.
- Modify `tests/test_session_start_codex_hook.py`
  - Cover Codex hook `additionalContext` when summary is selected.
- Modify `docs/architecture.md`
  - Update Stable Memory read path from “inject MEMORY.md” to “prefer verified memory_summary.md, fallback to MEMORY.md”.

## Task 1: Lock Existing Fallback Behavior

**Files:**
- Modify: `tests/test_session_start.py`
- Test: `tests/test_session_start.py`

- [ ] **Step 1: Extend the existing SessionStart fallback test**

In `tests/test_session_start.py`, add the following assertions to `test_session_start_injects_memory_and_short_recall_pointer` after the existing `stable_background` assertions:

```python
    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "summary_missing"
    assert result["stable_background"]["memory_source_path"] == str(state / "memory" / "MEMORY.md")
    assert result["stable_background"]["memory_summary_path"] == str(state / "memory" / "memory_summary.md")
    assert result["stable_background"]["memory_summary_meta_path"] == str(state / "memory" / "memory_summary.meta.json")
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_start.py::test_session_start_injects_memory_and_short_recall_pointer
```

Expected: FAIL with `KeyError: 'memory_source'`.

- [ ] **Step 3: Add the minimal structured loader shell**

In `src/codex_self_evolution/storage.py`, add the import and dataclass:

```python
from dataclasses import dataclass
```

```python
@dataclass(frozen=True)
class StableMemorySelection:
    """Selected stable memory content plus source and fallback metadata."""

    content: str
    source: str
    source_path: Path
    memory_path: Path
    summary_path: Path
    summary_meta_path: Path
    fallback_used: bool
    fallback_reason: str
```

Replace `load_stable_memory()` with:

```python
def load_stable_memory(paths: Paths) -> StableMemorySelection:
    """Load the default-injected stable memory with safe summary fallback."""
    memory_path = paths.memory_dir / "MEMORY.md"
    summary_path = paths.memory_dir / "memory_summary.md"
    summary_meta_path = paths.memory_dir / "memory_summary.meta.json"
    memory_text = read_text_if_exists(memory_path)
    return StableMemorySelection(
        content=memory_text,
        source="MEMORY.md",
        source_path=memory_path,
        memory_path=memory_path,
        summary_path=summary_path,
        summary_meta_path=summary_meta_path,
        fallback_used=True,
        fallback_reason="summary_missing",
    )
```

In `src/codex_self_evolution/hooks/session_start.py`, update the loader usage:

```python
    memory_selection = load_stable_memory(paths)
    memory_text = memory_selection.content
    memory_heading = "## memory_summary.md" if memory_selection.source == "memory_summary.md" else "## MEMORY.md"
    combined_prefix = "\n\n".join(
        section
        for section in [
            "# Stable Background",
            memory_heading + "\n" + (memory_text or "_No entries yet._\n"),
        ]
        if section
    )
```

Add these keys inside `stable_background`:

```python
            "memory_source": memory_selection.source,
            "memory_source_path": str(memory_selection.source_path),
            "memory_summary_path": str(memory_selection.summary_path),
            "memory_summary_meta_path": str(memory_selection.summary_meta_path),
            "memory_fallback_used": memory_selection.fallback_used,
            "memory_fallback_reason": memory_selection.fallback_reason,
```

- [ ] **Step 4: Run the fallback test and verify it passes**

Run:

```bash
uv run pytest -q tests/test_session_start.py::test_session_start_injects_memory_and_short_recall_pointer
```

Expected: `1 passed`.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/codex_self_evolution/storage.py src/codex_self_evolution/hooks/session_start.py tests/test_session_start.py
git commit -m "feat: expose stable memory fallback metadata"
```

## Task 2: Implement Verified Summary Selection

**Files:**
- Modify: `src/codex_self_evolution/storage.py`
- Modify: `tests/test_session_start.py`
- Test: `tests/test_session_start.py`

- [ ] **Step 1: Add test helpers and valid summary test**

In `tests/test_session_start.py`, add imports:

```python
import hashlib
```

Add helper:

```python
def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

Add this test:

```python
def test_session_start_prefers_valid_memory_summary(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nFull detail should stay cold.\n"
    summary_text = "# Memory Summary\n\nHot summary only.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text(summary_text, encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "MEMORY.md",
                "source_memory_sha256": _sha256_text(memory_text),
                "summary_sha256": _sha256_text(summary_text),
                "generated_at": "2026-06-12T00:00:00Z",
                "generator": "session_reflection_consolidation",
            }
        ),
        encoding="utf-8",
    )

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "memory_summary.md"
    assert result["stable_background"]["memory_fallback_used"] is False
    assert result["stable_background"]["memory_fallback_reason"] == ""
    assert result["stable_background"]["current_memory_md"] == summary_text
    assert "## memory_summary.md" in result["stable_background"]["combined_prefix"]
    assert "Hot summary only." in result["stable_background"]["combined_prefix"]
    assert "Full detail should stay cold." not in result["stable_background"]["combined_prefix"]
```

- [ ] **Step 2: Run the valid summary test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_start.py::test_session_start_prefers_valid_memory_summary
```

Expected: FAIL because `memory_source` is still `MEMORY.md`.

- [ ] **Step 3: Implement summary meta validation**

In `src/codex_self_evolution/storage.py`, add:

```python
def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest for UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

Add:

```python
def _load_summary_meta(path: Path) -> dict[str, object] | None:
    """Load memory summary metadata when it is a JSON object."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
```

Replace `load_stable_memory()` with:

```python
def load_stable_memory(paths: Paths) -> StableMemorySelection:
    """Load the default-injected stable memory with safe summary fallback."""
    memory_path = paths.memory_dir / "MEMORY.md"
    summary_path = paths.memory_dir / "memory_summary.md"
    summary_meta_path = paths.memory_dir / "memory_summary.meta.json"
    memory_text = read_text_if_exists(memory_path)
    summary_text = read_text_if_exists(summary_path)

    fallback_reason = _summary_fallback_reason(
        memory_text=memory_text,
        summary_text=summary_text,
        summary_meta_path=summary_meta_path,
    )
    if not fallback_reason:
        return StableMemorySelection(
            content=summary_text,
            source="memory_summary.md",
            source_path=summary_path,
            memory_path=memory_path,
            summary_path=summary_path,
            summary_meta_path=summary_meta_path,
            fallback_used=False,
            fallback_reason="",
        )
    return StableMemorySelection(
        content=memory_text,
        source="MEMORY.md",
        source_path=memory_path,
        memory_path=memory_path,
        summary_path=summary_path,
        summary_meta_path=summary_meta_path,
        fallback_used=True,
        fallback_reason=fallback_reason,
    )
```

Add:

```python
def _summary_fallback_reason(
    *,
    memory_text: str,
    summary_text: str,
    summary_meta_path: Path,
) -> str:
    """Return an empty string only when memory_summary.md is safe to inject."""
    if not summary_text.strip():
        return "summary_missing" if not summary_meta_path.with_name("memory_summary.md").exists() else "summary_empty"
    if not summary_meta_path.exists():
        return "summary_meta_missing"
    meta = _load_summary_meta(summary_meta_path)
    if meta is None:
        return "summary_meta_invalid"
    if meta.get("schema_version") != 1 or meta.get("source") != "MEMORY.md":
        return "summary_meta_invalid"
    if meta.get("source_memory_sha256") != sha256_text(memory_text):
        return "source_hash_mismatch"
    if meta.get("summary_sha256") != sha256_text(summary_text):
        return "summary_hash_mismatch"
    return ""
```

- [ ] **Step 4: Run valid summary test**

Run:

```bash
uv run pytest -q tests/test_session_start.py::test_session_start_prefers_valid_memory_summary
```

Expected: `1 passed`.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/codex_self_evolution/storage.py tests/test_session_start.py
git commit -m "feat: prefer verified memory summary"
```

## Task 3: Cover Fallback Reasons and Hook Output

**Files:**
- Modify: `tests/test_session_start.py`
- Modify: `tests/test_session_start_codex_hook.py`
- Test: `tests/test_session_start.py`
- Test: `tests/test_session_start_codex_hook.py`

- [ ] **Step 1: Add stale summary fallback tests**

In `tests/test_session_start.py`, add:

```python
def test_session_start_falls_back_when_summary_source_hash_is_stale(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nFresh full memory.\n"
    summary_text = "# Memory Summary\n\nStale summary.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text(summary_text, encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "MEMORY.md",
                "source_memory_sha256": _sha256_text("# MEMORY\n\nOld full memory.\n"),
                "summary_sha256": _sha256_text(summary_text),
                "generated_at": "2026-06-12T00:00:00Z",
                "generator": "session_reflection_consolidation",
            }
        ),
        encoding="utf-8",
    )

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "source_hash_mismatch"
    assert "Fresh full memory." in result["stable_background"]["combined_prefix"]
    assert "Stale summary." not in result["stable_background"]["combined_prefix"]
```

Add:

```python
def test_session_start_falls_back_when_summary_hash_mismatches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nFull memory.\n"
    summary_text = "# Memory Summary\n\nEdited without meta update.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text(summary_text, encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "MEMORY.md",
                "source_memory_sha256": _sha256_text(memory_text),
                "summary_sha256": _sha256_text("# Memory Summary\n\nPrevious summary.\n"),
                "generated_at": "2026-06-12T00:00:00Z",
                "generator": "session_reflection_consolidation",
            }
        ),
        encoding="utf-8",
    )

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_reason"] == "summary_hash_mismatch"
    assert "Full memory." in result["stable_background"]["combined_prefix"]
    assert "Edited without meta update." not in result["stable_background"]["combined_prefix"]
```

- [ ] **Step 2: Add corrupt meta fallback test**

In `tests/test_session_start.py`, add:

```python
def test_session_start_falls_back_when_summary_meta_is_invalid_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# MEMORY\n\nSafe full memory.\n", encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text("# Memory Summary\n\nBroken meta summary.\n", encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text("{not-json", encoding="utf-8")

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_reason"] == "summary_meta_invalid"
    assert "Safe full memory." in result["stable_background"]["combined_prefix"]
    json.dumps(result)
```

- [ ] **Step 3: Add Codex hook additionalContext summary test**

In `tests/test_session_start_codex_hook.py`, add imports:

```python
import hashlib
```

Add helper:

```python
def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

Add test:

```python
def test_format_includes_verified_memory_summary_instead_of_full_memory(tmp_path):
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nCold full memory.\n"
    summary_text = "# Memory Summary\n\nHot startup summary.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text(summary_text, encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "MEMORY.md",
                "source_memory_sha256": _sha256_text(memory_text),
                "summary_sha256": _sha256_text(summary_text),
                "generated_at": "2026-06-12T00:00:00Z",
                "generator": "session_reflection_consolidation",
            }
        ),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    result = session_start(cwd=repo, state_dir=state)
    ac = format_session_start_for_codex(result)["hookSpecificOutput"]["additionalContext"]

    assert "## memory_summary.md" in ac
    assert "Hot startup summary." in ac
    assert "Cold full memory." not in ac
    assert "Recall Policy" in ac
```

- [ ] **Step 4: Run the focused tests**

Run:

```bash
uv run pytest -q tests/test_session_start.py tests/test_session_start_codex_hook.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add tests/test_session_start.py tests/test_session_start_codex_hook.py
git commit -m "test: cover memory summary fallback reasons"
```

## Task 4: Update Architecture Docs and Run Full Verification

**Files:**
- Modify: `docs/architecture.md`
- Test: full suite

- [ ] **Step 1: Update architecture read path wording**

In `docs/architecture.md`, update the Stable Memory row from:

```markdown
| Stable Memory | session reflection 写 `MEMORY.md` / `memory/refs/` | `SessionStart` 注入 `MEMORY.md` |
```

to:

```markdown
| Stable Memory | session reflection 写 `MEMORY.md` / `memory/refs/`，后续 consolidation 可生成 `memory_summary.md` | `SessionStart` 优先注入已验证的 `memory_summary.md`，否则回退 `MEMORY.md` |
```

Update the lifecycle text from:

```text
  -> 读取当前 repo bucket 的 MEMORY.md
```

to:

```text
  -> 读取当前 repo bucket 的已验证 memory_summary.md，缺失或过期时回退 MEMORY.md
```

Update the runtime directory tree under `memory/` so it includes:

```text
            ├── MEMORY.md
            ├── memory_summary.md
            ├── memory_summary.meta.json
            └── refs/
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_session_start.py tests/test_session_start_codex_hook.py
```

Expected: all tests pass.

- [ ] **Step 3: Run full regression**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Run runtime smoke through current source shim**

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

- [ ] **Step 6: Commit Task 4**

```bash
git add docs/architecture.md src/codex_self_evolution/storage.py src/codex_self_evolution/hooks/session_start.py tests/test_session_start.py tests/test_session_start_codex_hook.py
git commit -m "feat: add memory summary fallback"
```

## Self-Review Notes

- Spec coverage:
  - Phase 5A safe summary read path is covered by Tasks 1-3.
  - Fresh machine fallback is covered by Task 1.
  - Stale summary fallback is covered by Task 3.
  - SessionStart payload metadata is covered by Tasks 1-3.
  - Architecture documentation is covered by Task 4.
- Scope boundaries:
  - No summary generation is included.
  - No reflection child write-path change is included.
  - No usage feedback, pollution labels, candidate layer, consolidation, or skill staging is included.
- Type consistency:
  - `load_stable_memory(paths)` returns `StableMemorySelection`.
  - `session_start()` consumes `.content`, `.source`, `.source_path`, `.summary_path`, `.summary_meta_path`, `.fallback_used`, and `.fallback_reason`.
  - Existing `stable_background["current_memory_md"]` remains present and contains the selected injected memory text.
