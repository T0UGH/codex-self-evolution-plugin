# Skill Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a global periodic skill synthesis command that materializes historical CSEP evidence into a bounded workspace, runs a Pi synthesis agent, audits direct `csep-synth-*` skill writes, and stops the legacy compiler from producing skills.

**Architecture:** Add a new `codex_self_evolution.skill_synthesis` package with focused modules for paths, receipts, inventory, evidence materialization, validation, agent invocation, and orchestration. The existing compiler keeps memory/recall promotion only; generated skills become the sole responsibility of `skill-synthesize`. Scheduler, status, and config support are added as independent surfaces so the feature can run every 4 hours without coupling to `scan`.

**Tech Stack:** Python 3.11 stdlib, pytest, Bash launchd scripts, existing Pi CLI invocation pattern, existing CSEP storage JSON helpers.

---

## Scope And Existing Context

This plan implements the design in `docs/plans/2026-05-13-skill-synthesis-design.md`.

The work should be done in an isolated worktree at execution time. The current main worktree has unrelated dirty files:

```text
src/codex_self_evolution/compiler/backends.py
tests/test_agent_compiler_backend.py
claudecode-reviewer.md
docs/deepseek-review.md
```

Do not include those unrelated changes unless the user explicitly moves execution into this same dirty worktree.

The final behavior is:

- `skill-synthesize` is a new command.
- The new feature uses global state under `~/.codex-self-evolution/skill_synthesis/`.
- Agent output writes only `~/.codex/skills/csep-synth-*`.
- Dry-run writes to a temporary skills root and detects writes to the real root as `dry_run_leak`.
- Compiler no longer creates or publishes skills.
- Existing `csep-*` compiler skills remain legacy read-only artifacts.

## File Structure

Create these files:

- `src/codex_self_evolution/skill_synthesis/__init__.py`  
  Package marker and public exports.
- `src/codex_self_evolution/skill_synthesis/paths.py`  
  Resolve global synthesis home, run directories, real/dry-run skill roots.
- `src/codex_self_evolution/skill_synthesis/redaction.py`  
  Redact obvious secret-like strings before materializing evidence.
- `src/codex_self_evolution/skill_synthesis/inventory.py`  
  Read `csep-synth-*` and metadata-only non-synth skill inventory.
- `src/codex_self_evolution/skill_synthesis/validation.py`  
  Validate synthesized `SKILL.md`, retired format, and invalid markers.
- `src/codex_self_evolution/skill_synthesis/evidence.py`  
  Collect and materialize memory, recall, and done suggestions into `runs/<run_id>/input`.
- `src/codex_self_evolution/skill_synthesis/agent.py`  
  Build the Pi command, prompt, and parse `output/result.json`.
- `src/codex_self_evolution/skill_synthesis/runner.py`  
  Orchestrate config, lock, materialization, snapshots, agent invocation, receipt, and indices.
- `tests/test_skill_synthesis_config.py`
- `tests/test_skill_synthesis_inventory.py`
- `tests/test_skill_synthesis_validation.py`
- `tests/test_skill_synthesis_evidence.py`
- `tests/test_skill_synthesis_agent.py`
- `tests/test_skill_synthesis_runner.py`
- `tests/test_skill_synthesis_cli.py`
- `tests/test_skill_synthesis_scheduler.py`
- `tests/test_compiler_skill_disabled.py`
- `tests/test_diagnostics_skill_synthesis.py`
- `scripts/install-skill-synthesis-scheduler.sh`
- `scripts/uninstall-skill-synthesis-scheduler.sh`

Modify these files:

- `src/codex_self_evolution/config.py`  
  Add global synthesis constants.
- `src/codex_self_evolution/config_file.py`  
  Add independent `[skill_synthesis]` dataclasses, load logic, linting, and validation warnings.
- `src/codex_self_evolution/config_file_template.py`  
  Add default-enabled Minimax synthesis config.
- `src/codex_self_evolution/cli.py`  
  Add `skill-synthesize` subcommand and command observability.
- `src/codex_self_evolution/diagnostics.py`  
  Add synthesis scheduler/status and legacy/synth skill counts.
- `src/codex_self_evolution/compiler/backends.py`  
  Stop compiling skills in script and agent paths; emit deprecated discards for historical `skill_action`.
- `src/codex_self_evolution/compiler/engine.py`  
  Stop writing/publishing compiler-managed skills in the main path.
- `src/codex_self_evolution/review/prompt.md`  
  Remove guidance that encourages `skill_action`.
- `src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json`
- `plugins/codex-self-evolution/.codex-plugin/plugin.json`  
  Add a plugin command entry for manual `skill-synthesize`.
- `README.md`  
  Document the new command and compiler skill deprecation.
- `docs/getting-started.md`  
  Document scheduler installation and first dry-run smoke.

## Task 1: Config Model And Template

**Files:**
- Modify: `src/codex_self_evolution/config_file.py`
- Modify: `src/codex_self_evolution/config_file_template.py`
- Create: `tests/test_skill_synthesis_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_skill_synthesis_config.py`:

```python
from __future__ import annotations

from pathlib import Path

from codex_self_evolution.config_file import config_to_dict, load_config
from codex_self_evolution.config_file_template import CONFIG_TEMPLATE


def _write_config(home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def test_skill_synthesis_defaults_are_enabled_and_independent(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.skill_synthesis

    assert cfg.enabled is True
    assert cfg.default_mode == "incremental"
    assert cfg.lookback_hours == 24
    assert cfg.lookback_days == 30
    assert cfg.skills_prefix == "csep-synth-"
    assert cfg.agent.backend == "agent:pi"
    assert cfg.agent.provider == "minimax"
    assert cfg.agent.model == "MiniMax-M2.7"
    assert cfg.agent.timeout_seconds == 1800.0
    assert loaded.sources["skill_synthesis.agent.provider"] == "default"
    assert loaded.config.compile.pi.provider == "kimi"


def test_skill_synthesis_toml_values_apply_without_compile_fallback(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[compile.pi]
provider = "kimi"
model = "kimi-k2.6"

[skill_synthesis]
enabled = true
default_mode = "full"
lookback_hours = 12
lookback_days = 14
skills_prefix = "csep-synth-"

[skill_synthesis.agent]
backend = "agent:pi"
provider = "minimax"
model = "MiniMax-M2.7"
timeout_seconds = 2400
""")

    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.skill_synthesis

    assert cfg.default_mode == "full"
    assert cfg.lookback_hours == 12
    assert cfg.lookback_days == 14
    assert cfg.agent.provider == "minimax"
    assert cfg.agent.timeout_seconds == 2400.0
    assert loaded.sources["skill_synthesis.agent.provider"] == "config.toml"


def test_skill_synthesis_unsupported_backend_warns_when_enabled(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[skill_synthesis]
enabled = true

[skill_synthesis.agent]
backend = "agent:opencode"
provider = "minimax"
model = "MiniMax-M2.7"
""")

    loaded = load_config(home=tmp_path, env={})

    assert loaded.config.skill_synthesis.agent.backend == "agent:opencode"
    assert any("skill_synthesis.agent.backend" in warning for warning in loaded.warnings)


def test_skill_synthesis_template_contains_default_minimax_config() -> None:
    assert "[skill_synthesis]" in CONFIG_TEMPLATE
    assert "enabled = true" in CONFIG_TEMPLATE
    assert "[skill_synthesis.agent]" in CONFIG_TEMPLATE
    assert 'provider = "minimax"' in CONFIG_TEMPLATE
    assert 'model = "MiniMax-M2.7"' in CONFIG_TEMPLATE


def test_config_to_dict_includes_skill_synthesis(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    data = config_to_dict(loaded.config)

    assert data["skill_synthesis"]["enabled"] is True
    assert data["skill_synthesis"]["agent"]["backend"] == "agent:pi"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_skill_synthesis_config.py -q
```

Expected: FAIL with `AttributeError: 'PluginConfig' object has no attribute 'skill_synthesis'`.

- [ ] **Step 3: Add config dataclasses and loader wiring**

In `src/codex_self_evolution/config_file.py`, add dataclasses after `SchedulerConfig`:

```python
@dataclass
class SkillSynthesisAgentConfig:
    backend: str = "agent:pi"
    provider: str = "minimax"
    model: str = "MiniMax-M2.7"
    timeout_seconds: float = 1800.0


@dataclass
class SkillSynthesisConfig:
    enabled: bool = True
    default_mode: str = "incremental"
    lookback_hours: int = 24
    lookback_days: int = 30
    skills_prefix: str = "csep-synth-"
    agent: SkillSynthesisAgentConfig = field(default_factory=SkillSynthesisAgentConfig)
```

Add the field to `PluginConfig`:

```python
    skill_synthesis: SkillSynthesisConfig = field(default_factory=SkillSynthesisConfig)
```

Add constants near `ALLOWED_COMPILE_BACKENDS`:

```python
ALLOWED_SKILL_SYNTHESIS_BACKENDS = {"agent:pi"}
ALLOWED_SKILL_SYNTHESIS_MODES = {"incremental", "full"}
```

Add recognized paths to `_RECOGNIZED_PATHS`:

```python
    "skill_synthesis", "skill_synthesis.enabled",
    "skill_synthesis.default_mode", "skill_synthesis.lookback_hours",
    "skill_synthesis.lookback_days", "skill_synthesis.skills_prefix",
    "skill_synthesis.agent", "skill_synthesis.agent.backend",
    "skill_synthesis.agent.provider", "skill_synthesis.agent.model",
    "skill_synthesis.agent.timeout_seconds",
```

After the scheduler load block in `load_config()`, add:

```python
    # --- skill_synthesis ---
    synth_toml = raw_toml.get("skill_synthesis", {}) or {}
    synth_enabled = synth_toml.get("enabled")
    if isinstance(synth_enabled, bool):
        config.skill_synthesis.enabled = synth_enabled
        sources["skill_synthesis.enabled"] = "config.toml"
    else:
        sources["skill_synthesis.enabled"] = "default"

    config.skill_synthesis.default_mode, sources["skill_synthesis.default_mode"] = _resolve(
        field_path="skill_synthesis.default_mode",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("default_mode"),
        default=config.skill_synthesis.default_mode,
        validator=lambda v: v in ALLOWED_SKILL_SYNTHESIS_MODES,
    )
    config.skill_synthesis.lookback_hours, sources["skill_synthesis.lookback_hours"] = _resolve_number(
        "skill_synthesis.lookback_hours",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("lookback_hours"),
        default=config.skill_synthesis.lookback_hours,
        cast=int,
    )
    config.skill_synthesis.lookback_days, sources["skill_synthesis.lookback_days"] = _resolve_number(
        "skill_synthesis.lookback_days",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("lookback_days"),
        default=config.skill_synthesis.lookback_days,
        cast=int,
    )
    config.skill_synthesis.skills_prefix, sources["skill_synthesis.skills_prefix"] = _resolve(
        field_path="skill_synthesis.skills_prefix",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("skills_prefix"),
        default=config.skill_synthesis.skills_prefix,
    )

    synth_agent_toml = synth_toml.get("agent", {}) or {}
    config.skill_synthesis.agent.backend, sources["skill_synthesis.agent.backend"] = _resolve(
        field_path="skill_synthesis.agent.backend",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("backend"),
        default=config.skill_synthesis.agent.backend,
    )
    config.skill_synthesis.agent.provider, sources["skill_synthesis.agent.provider"] = _resolve(
        field_path="skill_synthesis.agent.provider",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("provider"),
        default=config.skill_synthesis.agent.provider,
    )
    config.skill_synthesis.agent.model, sources["skill_synthesis.agent.model"] = _resolve(
        field_path="skill_synthesis.agent.model",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("model"),
        default=config.skill_synthesis.agent.model,
    )
    config.skill_synthesis.agent.timeout_seconds, sources["skill_synthesis.agent.timeout_seconds"] = _resolve_number(
        "skill_synthesis.agent.timeout_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("timeout_seconds"),
        default=config.skill_synthesis.agent.timeout_seconds,
        cast=float,
    )

    if config.skill_synthesis.enabled:
        if config.skill_synthesis.agent.backend not in ALLOWED_SKILL_SYNTHESIS_BACKENDS:
            warnings.append(
                "skill_synthesis.agent.backend must be agent:pi in v1; "
                f"got {config.skill_synthesis.agent.backend!r}"
            )
        if not str(config.skill_synthesis.agent.provider).strip():
            warnings.append("skill_synthesis.agent.provider is required when skill synthesis is enabled")
        if not str(config.skill_synthesis.agent.model).strip():
            warnings.append("skill_synthesis.agent.model is required when skill synthesis is enabled")
        if config.skill_synthesis.agent.timeout_seconds <= 0:
            warnings.append("skill_synthesis.agent.timeout_seconds must be positive")
```

- [ ] **Step 4: Update config template**

In `src/codex_self_evolution/config_file_template.py`, insert before `[log]`:

```toml
# ===========================================================================
# [skill_synthesis] — periodic global synthesized skills
# ===========================================================================

[skill_synthesis]
enabled = true
default_mode = "incremental"
lookback_hours = 24
lookback_days = 30
skills_prefix = "csep-synth-"


[skill_synthesis.agent]
# v1 supports only agent:pi. This config is independent from [compile.pi].
backend = "agent:pi"
provider = "minimax"
model = "MiniMax-M2.7"
timeout_seconds = 1800

```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_skill_synthesis_config.py tests/test_config_file.py tests/test_cli_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_self_evolution/config_file.py src/codex_self_evolution/config_file_template.py tests/test_skill_synthesis_config.py
git commit -m "feat: add skill synthesis config"
```

## Task 2: Paths, Redaction, Inventory, Validation

**Files:**
- Create: `src/codex_self_evolution/skill_synthesis/__init__.py`
- Create: `src/codex_self_evolution/skill_synthesis/paths.py`
- Create: `src/codex_self_evolution/skill_synthesis/redaction.py`
- Create: `src/codex_self_evolution/skill_synthesis/inventory.py`
- Create: `src/codex_self_evolution/skill_synthesis/validation.py`
- Modify: `src/codex_self_evolution/config.py`
- Create: `tests/test_skill_synthesis_inventory.py`
- Create: `tests/test_skill_synthesis_validation.py`

- [ ] **Step 1: Write failing inventory and validation tests**

Create `tests/test_skill_synthesis_inventory.py`:

```python
from __future__ import annotations

from pathlib import Path

from codex_self_evolution.skill_synthesis.inventory import (
    read_skills_inventory,
    snapshot_synth_skills,
)
from codex_self_evolution.skill_synthesis.paths import build_skill_synthesis_paths


def test_paths_use_global_skill_synthesis_home(tmp_path: Path) -> None:
    paths = build_skill_synthesis_paths(home=tmp_path, run_id="run-1")

    assert paths.root == tmp_path / "skill_synthesis"
    assert paths.runs_dir == paths.root / "runs"
    assert paths.run_input_dir == paths.root / "runs" / "run-1" / "input"
    assert paths.run_output_dir == paths.root / "runs" / "run-1" / "output"
    assert paths.receipts_dir == paths.root / "receipts"


def test_inventory_reads_frontmatter_only_for_non_synth(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    user_skill = skills_root / "manual-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text(
        "---\nname: manual-skill\ndescription: Manual skill summary.\n---\n\nSECRET_BODY\n",
        encoding="utf-8",
    )
    synth_skill = skills_root / "csep-synth-alpha"
    synth_skill.mkdir()
    (synth_skill / "SKILL.md").write_text(
        "---\nname: csep-synth-alpha\ndescription: Use when alpha repeats.\n---\n\nWorkflow body\n",
        encoding="utf-8",
    )

    inventory = read_skills_inventory(skills_root)

    assert inventory["non_synth"][0]["name"] == "manual-skill"
    assert inventory["non_synth"][0]["description"] == "Manual skill summary."
    assert "SECRET_BODY" not in str(inventory)
    assert inventory["synth"][0]["skill_id"] == "csep-synth-alpha"


def test_snapshot_synth_skills_only_tracks_synth_prefix(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "csep-synth-alpha").mkdir(parents=True)
    (skills_root / "csep-synth-alpha" / "SKILL.md").write_text("alpha", encoding="utf-8")
    (skills_root / "csep-legacy").mkdir()
    (skills_root / "csep-legacy" / "SKILL.md").write_text("legacy", encoding="utf-8")

    snapshot = snapshot_synth_skills(skills_root)

    assert list(snapshot) == [str(skills_root / "csep-synth-alpha" / "SKILL.md")]
```

Create `tests/test_skill_synthesis_validation.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.skill_synthesis.redaction import redact_secrets
from codex_self_evolution.skill_synthesis.validation import (
    RETIRED_DESCRIPTION,
    RETIRED_BODY_LINE,
    mark_invalid,
    validate_synth_skill,
)


def test_redact_secrets_masks_obvious_tokens() -> None:
    text, count = redact_secrets("Authorization: Bearer abcdefghijklmnop\napi_key=sk-1234567890")

    assert count == 2
    assert "abcdefghijklmnop" not in text
    assert "sk-1234567890" not in text
    assert "[REDACTED]" in text


def test_validate_active_skill_accepts_trigger_and_workflow(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: csep-synth-alpha\n"
        "description: Use when repeated alpha debugging needs command evidence.\n"
        "---\n\n"
        "# Alpha\n\n"
        "## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Check the receipt.\n"
        "3. Verify the output before changing files.\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is True
    assert result["status"] == "active"


def test_validate_retired_skill_requires_low_trigger_format(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-old" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: csep-synth-old\n"
        f"description: \"{RETIRED_DESCRIPTION}\"\n"
        "csep_status: retired\n"
        "---\n\n"
        "# Retired: Old\n\n"
        "This generated skill has been retired by codex-self-evolution skill synthesis.\n\n"
        f"{RETIRED_BODY_LINE}\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is True
    assert result["status"] == "retired"


def test_validate_secret_like_output_is_invalid(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-leaky" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\nname: csep-synth-leaky\ndescription: Use when testing leaks.\n---\n\n"
        "Workflow\n\nRun with Authorization: Bearer abcdefghijklmnopqrstuvwxyz.\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "secret_like_content"


def test_mark_invalid_writes_marker_without_deleting_skill(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-bad" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("bad", encoding="utf-8")

    marker = mark_invalid(skill, run_id="run-1", reasons=["weak_description"], evidence_keys=["ev-1"])

    assert skill.exists()
    assert marker == skill.parent / "CSEP_INVALID.json"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-1"
    assert data["reasons"] == ["weak_description"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_skill_synthesis_inventory.py tests/test_skill_synthesis_validation.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_self_evolution.skill_synthesis'`.

- [ ] **Step 3: Add path constants**

In `src/codex_self_evolution/config.py`, add:

```python
SKILL_SYNTHESIS_SUBDIR = "skill_synthesis"
SYNTH_SKILL_PREFIX = "csep-synth-"
```

- [ ] **Step 4: Add package and paths module**

Create `src/codex_self_evolution/skill_synthesis/__init__.py`:

```python
"""Global synthesized skill generation for codex-self-evolution."""
```

Create `src/codex_self_evolution/skill_synthesis/paths.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import SKILL_SYNTHESIS_SUBDIR, get_home_dir
from ..managed_skills.publish import codex_skills_dir


@dataclass(frozen=True)
class SkillSynthesisPaths:
    home: Path
    root: Path
    lock_path: Path
    last_receipt_path: Path
    evidence_index_path: Path
    published_index_path: Path
    receipts_dir: Path
    runs_dir: Path
    discarded_dir: Path
    run_dir: Path
    run_input_dir: Path
    run_output_dir: Path


def build_skill_synthesis_paths(home: str | Path | None = None, run_id: str = "") -> SkillSynthesisPaths:
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    root = home_dir / SKILL_SYNTHESIS_SUBDIR
    run_dir = root / "runs" / run_id if run_id else root / "runs"
    return SkillSynthesisPaths(
        home=home_dir,
        root=root,
        lock_path=root / "lock",
        last_receipt_path=root / "last_receipt.json",
        evidence_index_path=root / "evidence_index.json",
        published_index_path=root / "published_index.json",
        receipts_dir=root / "receipts",
        runs_dir=root / "runs",
        discarded_dir=root / "discarded",
        run_dir=run_dir,
        run_input_dir=run_dir / "input",
        run_output_dir=run_dir / "output",
    )


def resolve_real_skills_root(override: str | Path | None = None) -> Path:
    return codex_skills_dir(override)
```

- [ ] **Step 5: Add redaction module**

Create `src/codex_self_evolution/skill_synthesis/redaction.py`:

```python
from __future__ import annotations

import re


SECRET_PATTERNS = [
    re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(api[_-]?key\s*=\s*)['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?"),
    re.compile(r"(?i)(token\s*=\s*)['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?"),
    re.compile(r"(?i)(password\s*=\s*)['\"]?[^'\"\\s]{6,}['\"]?"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{12,}\b"),
]


def redact_secrets(text: str) -> tuple[str, int]:
    redacted = text
    count = 0
    for pattern in SECRET_PATTERNS:
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if match.lastindex:
                return f"{match.group(1)}[REDACTED]"
            return "[REDACTED]"
        redacted = pattern.sub(replace, redacted)
    return redacted, count


def contains_secret_like_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)
```

- [ ] **Step 6: Add inventory module**

Create `src/codex_self_evolution/skill_synthesis/inventory.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..config import SYNTH_SKILL_PREFIX
from ..storage import atomic_write_json


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _skill_doc(path: Path) -> Path:
    return path / "SKILL.md"


def read_skill_metadata(skill_dir: Path) -> dict[str, Any] | None:
    skill_path = _skill_doc(skill_dir)
    if not skill_path.is_file():
        return None
    try:
        text = skill_path.read_text(encoding="utf-8")
        stat = skill_path.stat()
    except OSError:
        return None
    meta = _frontmatter(text)
    return {
        "skill_id": skill_dir.name,
        "path": str(skill_path),
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "csep_status": meta.get("csep_status", ""),
        "mtime": stat.st_mtime,
        "content_hash": _hash_text(text),
    }


def read_skills_inventory(skills_root: Path) -> dict[str, list[dict[str, Any]]]:
    synth: list[dict[str, Any]] = []
    non_synth: list[dict[str, Any]] = []
    if not skills_root.is_dir():
        return {"synth": synth, "non_synth": non_synth}
    for child in sorted(skills_root.iterdir()):
        if not child.is_dir():
            continue
        meta = read_skill_metadata(child)
        if meta is None:
            continue
        if child.name.startswith(SYNTH_SKILL_PREFIX):
            marker = child / "CSEP_INVALID.json"
            meta["invalid_marker"] = str(marker) if marker.exists() else ""
            synth.append(meta)
        else:
            non_synth.append({
                "skill_id": meta["skill_id"],
                "path": meta["path"],
                "name": meta["name"],
                "description": meta["description"],
                "mtime": meta["mtime"],
            })
    return {"synth": synth, "non_synth": non_synth}


def snapshot_synth_skills(skills_root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    if not skills_root.is_dir():
        return snapshot
    for child in sorted(skills_root.iterdir()):
        if not child.is_dir() or not child.name.startswith(SYNTH_SKILL_PREFIX):
            continue
        meta = read_skill_metadata(child)
        if meta is not None:
            snapshot[str(child / "SKILL.md")] = meta
    return snapshot


def changed_paths(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[str]:
    paths = set(before) | set(after)
    return sorted(
        path for path in paths
        if before.get(path, {}).get("content_hash") != after.get(path, {}).get("content_hash")
    )


def write_published_index(path: Path, skills_root: Path, inventory: dict[str, list[dict[str, Any]]]) -> None:
    atomic_write_json(path, {
        "schema_version": 1,
        "skills_root": str(skills_root),
        "skills": inventory.get("synth", []),
    })
```

- [ ] **Step 7: Add validation module**

Create `src/codex_self_evolution/skill_synthesis/validation.py`:

```python
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import SYNTH_SKILL_PREFIX
from ..storage import atomic_write_json
from .inventory import _frontmatter
from .redaction import contains_secret_like_text

RETIRED_DESCRIPTION = "Retired generated skill. Do not use."
RETIRED_BODY_LINE = "Do not use this skill. It is kept only for audit history."


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_synth_skill(skill_path: Path) -> dict[str, Any]:
    if skill_path.name != "SKILL.md":
        return {"path": str(skill_path), "valid": False, "reason": "not_skill_md"}
    if not skill_path.parent.name.startswith(SYNTH_SKILL_PREFIX):
        return {"path": str(skill_path), "valid": False, "reason": "outside_synth_namespace"}
    if not re.fullmatch(r"csep-synth-[a-z0-9-]+", skill_path.parent.name):
        return {"path": str(skill_path), "valid": False, "reason": "invalid_skill_id"}
    if not skill_path.is_file():
        return {"path": str(skill_path), "valid": False, "reason": "missing_skill_md"}
    text = skill_path.read_text(encoding="utf-8")
    meta = _frontmatter(text)
    if not meta.get("name") or not meta.get("description"):
        return {"path": str(skill_path), "valid": False, "reason": "missing_frontmatter"}
    if contains_secret_like_text(text):
        return {"path": str(skill_path), "valid": False, "reason": "secret_like_content"}
    if meta.get("csep_status") == "retired":
        if meta.get("description") != RETIRED_DESCRIPTION:
            return {"path": str(skill_path), "valid": False, "reason": "invalid_retired_description"}
        if RETIRED_BODY_LINE not in text:
            return {"path": str(skill_path), "valid": False, "reason": "invalid_retired_body"}
        return {"path": str(skill_path), "valid": True, "status": "retired", "reason": None}
    description = meta.get("description", "").lower()
    if "use" not in description or "when" not in description:
        return {"path": str(skill_path), "valid": False, "reason": "weak_description"}
    words = [word for word in re.split(r"\s+", text) if word]
    if len(words) < 24:
        return {"path": str(skill_path), "valid": False, "reason": "low_signal"}
    if not any(marker in text.lower() for marker in ("workflow", "steps", "run ", "verify", "check")):
        return {"path": str(skill_path), "valid": False, "reason": "low_signal"}
    return {"path": str(skill_path), "valid": True, "status": "active", "reason": None}


def mark_invalid(skill_path: Path, *, run_id: str, reasons: list[str], evidence_keys: list[str]) -> Path:
    marker = skill_path.parent / "CSEP_INVALID.json"
    atomic_write_json(marker, {
        "schema_version": 1,
        "checked_at": _now(),
        "run_id": run_id,
        "skill_path": str(skill_path),
        "reasons": reasons,
        "evidence_keys": evidence_keys,
    })
    return marker


def clear_invalid_marker(skill_path: Path) -> bool:
    marker = skill_path.parent / "CSEP_INVALID.json"
    if marker.exists():
        marker.unlink()
        return True
    return False
```

- [ ] **Step 8: Run focused tests**

Run:

```bash
uv run pytest tests/test_skill_synthesis_inventory.py tests/test_skill_synthesis_validation.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/codex_self_evolution/config.py src/codex_self_evolution/skill_synthesis tests/test_skill_synthesis_inventory.py tests/test_skill_synthesis_validation.py
git commit -m "feat: add skill synthesis file guards"
```

## Task 3: Evidence Materialization And Indices

**Files:**
- Create: `src/codex_self_evolution/skill_synthesis/evidence.py`
- Modify: `src/codex_self_evolution/skill_synthesis/paths.py`
- Create: `tests/test_skill_synthesis_evidence.py`

- [ ] **Step 1: Write failing evidence tests**

Create `tests/test_skill_synthesis_evidence.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codex_self_evolution.skill_synthesis.evidence import (
    collect_evidence,
    load_evidence_index,
    materialize_evidence_workspace,
    update_evidence_index,
)
from codex_self_evolution.skill_synthesis.paths import build_skill_synthesis_paths


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_collect_evidence_uses_reviewer_timestamp_for_done_suggestions(tmp_path: Path) -> None:
    now = datetime(2026, 5, 13, tzinfo=UTC)
    bucket = tmp_path / "projects" / "-repo"
    _write_json(bucket / "suggestions" / "done" / "s1.json", {
        "schema_version": 1,
        "suggestion_id": "s1",
        "idempotency_key": "id1",
        "thread_id": "t1",
        "cwd": "/repo",
        "repo_fingerprint": "repo",
        "reviewer_timestamp": "2026-05-13T00:00:00Z",
        "suggestions": [
            {"family": "memory_updates", "summary": "Run status", "details": {"content": "Use status before changing scheduler."}},
        ],
        "source_authority": [],
        "state": "done",
    })

    evidence = collect_evidence(tmp_path, now=now, mode="incremental", lookback_hours=24, lookback_days=30)

    assert len(evidence) == 1
    assert evidence[0]["event_time_source"] == "reviewer_timestamp"
    assert evidence[0]["family"] == "memory_updates"


def test_collect_evidence_filters_old_items_by_lookback(tmp_path: Path) -> None:
    now = datetime(2026, 5, 13, tzinfo=UTC)
    old = now - timedelta(days=40)
    memory_path = tmp_path / "projects" / "-repo" / "memory" / "memory.json"
    _write_json(memory_path, {
        "global": [
            {"summary": "Old", "content": "Old workflow", "updated_at": old.isoformat().replace("+00:00", "Z")},
        ],
        "user": [],
    })

    evidence = collect_evidence(tmp_path, now=now, mode="full", lookback_hours=24, lookback_days=30)

    assert evidence == []


def test_materialize_workspace_redacts_and_records_original_path(tmp_path: Path) -> None:
    paths = build_skill_synthesis_paths(home=tmp_path, run_id="run-1")
    source = tmp_path / "projects" / "-repo" / "memory" / "memory.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    evidence = [{
        "evidence_key": "ev1",
        "bucket": "-repo",
        "source_path": str(source),
        "original_path": str(source),
        "source_type": "memory",
        "family": "memory_updates",
        "event_time": "2026-05-13T00:00:00Z",
        "event_time_source": "record_updated_at",
        "summary": "Secret note",
        "content": "Run with api_key=sk-1234567890 during tests.",
        "content_hash": "hash",
        "seen_before": False,
    }]

    manifest = materialize_evidence_workspace(paths, evidence, skills_inventory={"synth": [], "non_synth": []}, synth_inventory=[])

    manifest_path = paths.run_input_dir / "MANIFEST.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["evidence"][0]["original_path"] == str(source)
    assert data["evidence"][0]["redaction_count"] == 1
    excerpt = Path(data["evidence"][0]["excerpt_path"]).read_text(encoding="utf-8")
    assert "sk-1234567890" not in excerpt
    assert manifest["evidence_count"] == 1


def test_evidence_index_updates_seen_counts(tmp_path: Path) -> None:
    path = tmp_path / "skill_synthesis" / "evidence_index.json"
    index = load_evidence_index(path)
    update_evidence_index(path, index, [{"evidence_key": "ev1"}], run_id="run-1", outcome="used")
    updated = load_evidence_index(path)

    assert updated["evidence"]["ev1"]["seen_count"] == 1
    assert updated["evidence"]["ev1"]["last_run_id"] == "run-1"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_skill_synthesis_evidence.py -q
```

Expected: FAIL with `ModuleNotFoundError` or missing `collect_evidence`.

- [ ] **Step 3: Add evidence collection and materialization**

Create `src/codex_self_evolution/skill_synthesis/evidence.py`:

```python
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import PROJECTS_SUBDIR, is_archived_bucket
from ..schemas import SchemaError, SuggestionEnvelope
from ..storage import atomic_write_json, atomic_write_text, load_json
from .redaction import redact_secrets

MAX_MEMORY_BYTES = 8 * 1024
MAX_SUGGESTION_BYTES = 16 * 1024


def _now_string() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _file_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _cutoff(now: datetime, mode: str, lookback_hours: int, lookback_days: int) -> datetime:
    if mode == "full":
        return now - timedelta(days=max(1, min(30, lookback_days)))
    return now - timedelta(hours=max(1, lookback_hours))


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _truncate(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def collect_evidence(
    home: str | Path,
    *,
    now: datetime | None = None,
    mode: str,
    lookback_hours: int,
    lookback_days: int,
    evidence_index: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    home_dir = Path(home).expanduser().resolve()
    now_dt = now or datetime.now(UTC)
    cutoff = _cutoff(now_dt, mode, lookback_hours, lookback_days)
    projects_dir = home_dir / PROJECTS_SUBDIR
    if not projects_dir.is_dir():
        return []
    seen = (evidence_index or {}).get("evidence", {})
    output: list[dict[str, Any]] = []
    for bucket in sorted(projects_dir.iterdir()):
        if not bucket.is_dir() or is_archived_bucket(bucket.name):
            continue
        output.extend(_memory_evidence(bucket, cutoff, seen))
        output.extend(_recall_evidence(bucket, cutoff, seen))
        output.extend(_suggestion_done_evidence(bucket, cutoff, seen))
    return sorted(output, key=lambda item: item["event_time"], reverse=True)


def _memory_evidence(bucket: Path, cutoff: datetime, seen: dict[str, Any]) -> list[dict[str, Any]]:
    path = bucket / "memory" / "memory.json"
    if not path.is_file():
        return []
    try:
        raw = load_json(path)
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    out: list[dict[str, Any]] = []
    for scope in ("user", "global"):
        for item in raw.get(scope, []) if isinstance(raw.get(scope), list) else []:
            if not isinstance(item, dict):
                continue
            event_time = _parse_time(item.get("updated_at")) or _parse_time(item.get("created_at")) or _file_time(path)
            if event_time < cutoff:
                continue
            summary = str(item.get("summary") or "").strip()
            content = _truncate(str(item.get("content") or "").strip(), MAX_MEMORY_BYTES)
            key = f"memory:{bucket.name}:{scope}:{_hash(summary)}:{_hash(content)}"
            out.append(_evidence(
                key=key,
                bucket=bucket,
                path=path,
                source_type="memory",
                family="memory_updates",
                event_time=event_time,
                event_time_source="record_updated_at" if item.get("updated_at") or item.get("created_at") else "file_mtime",
                summary=summary,
                content=content,
                seen=seen,
            ))
    return out


def _recall_evidence(bucket: Path, cutoff: datetime, seen: dict[str, Any]) -> list[dict[str, Any]]:
    path = bucket / "recall" / "index.json"
    if not path.is_file():
        return []
    try:
        raw = load_json(path)
    except (OSError, ValueError):
        return []
    records = raw.get("records") if isinstance(raw, dict) else []
    if not isinstance(records, list):
        return []
    out: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        event_time = _parse_time(item.get("source_updated_at")) or _file_time(path)
        if event_time < cutoff:
            continue
        summary = str(item.get("summary") or "").strip()
        content = _truncate(str(item.get("content") or "").strip(), MAX_MEMORY_BYTES)
        raw_id = str(item.get("id") or "").strip()
        key = f"recall:{bucket.name}:{raw_id or _hash(summary + content)}"
        out.append(_evidence(
            key=key,
            bucket=bucket,
            path=path,
            source_type="recall",
            family="recall_candidate",
            event_time=event_time,
            event_time_source="source_updated_at" if item.get("source_updated_at") else "file_mtime",
            summary=summary,
            content=content,
            seen=seen,
        ))
    return out


def _suggestion_done_evidence(bucket: Path, cutoff: datetime, seen: dict[str, Any]) -> list[dict[str, Any]]:
    done_dir = bucket / "suggestions" / "done"
    if not done_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(done_dir.glob("*.json")):
        try:
            envelope = SuggestionEnvelope.from_dict(load_json(path))
        except (OSError, ValueError, SchemaError):
            continue
        event_time = _parse_time(envelope.reviewer_timestamp) or _file_time(path)
        if event_time < cutoff:
            continue
        for suggestion in envelope.suggestions:
            content = _truncate(json.dumps(suggestion.details, ensure_ascii=False, sort_keys=True), MAX_SUGGESTION_BYTES)
            key = f"suggestion:{bucket.name}:{envelope.suggestion_id}:{suggestion.family}:{_hash(suggestion.summary)}"
            out.append(_evidence(
                key=key,
                bucket=bucket,
                path=path,
                source_type="suggestions_done",
                family=suggestion.family,
                event_time=event_time,
                event_time_source="reviewer_timestamp",
                summary=suggestion.summary,
                content=content,
                seen=seen,
            ))
    return out


def _evidence(
    *,
    key: str,
    bucket: Path,
    path: Path,
    source_type: str,
    family: str,
    event_time: datetime,
    event_time_source: str,
    summary: str,
    content: str,
    seen: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evidence_key": key,
        "bucket": bucket.name,
        "source_path": str(path),
        "original_path": str(path),
        "source_type": source_type,
        "family": family,
        "event_time": event_time.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "event_time_source": event_time_source,
        "summary": summary,
        "content": content,
        "content_hash": _hash(content),
        "seen_before": key in seen,
    }


def materialize_evidence_workspace(
    paths,
    evidence: list[dict[str, Any]],
    *,
    skills_inventory: dict[str, Any],
    synth_inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    if paths.run_dir.exists():
        shutil.rmtree(paths.run_dir)
    paths.run_input_dir.mkdir(parents=True, exist_ok=True)
    paths.run_output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths.run_input_dir / "skills_inventory.json", skills_inventory)
    atomic_write_json(paths.run_input_dir / "synth_inventory.json", synth_inventory)
    manifest_items: list[dict[str, Any]] = []
    for index, item in enumerate(evidence):
        redacted, redaction_count = redact_secrets(item.get("content", ""))
        excerpt_path = paths.run_input_dir / "buckets" / item["bucket"] / item["source_type"] / f"{index:04d}.md"
        atomic_write_text(excerpt_path, f"# {item.get('summary', '')}\n\n{redacted}\n")
        manifest_item = {k: v for k, v in item.items() if k != "content"}
        manifest_item["excerpt_path"] = str(excerpt_path)
        manifest_item["redaction_count"] = redaction_count
        manifest_items.append(manifest_item)
    manifest = {
        "schema_version": 1,
        "evidence_count": len(evidence),
        "evidence": manifest_items,
    }
    atomic_write_json(paths.run_input_dir / "MANIFEST.json", manifest)
    atomic_write_text(paths.run_input_dir / "README.md", _render_readme(paths, len(evidence)))
    return manifest


def _render_readme(paths, count: int) -> str:
    return (
        "# Skill Synthesis Input Workspace\n\n"
        f"Evidence items: {count}\n\n"
        "Read only this input directory. Write result.json under ../output/.\n"
    )


def load_evidence_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "updated_at": "", "evidence": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "updated_at": "", "evidence": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("evidence"), dict):
        return {"schema_version": 1, "updated_at": "", "evidence": {}}
    return raw


def update_evidence_index(path: Path, index: dict[str, Any], evidence: list[dict[str, Any]], *, run_id: str, outcome: str) -> None:
    now = _now_string()
    records = dict(index.get("evidence") or {})
    for item in evidence:
        key = str(item["evidence_key"])
        current = dict(records.get(key) or {})
        records[key] = {
            "first_seen_at": current.get("first_seen_at") or now,
            "last_seen_at": now,
            "seen_count": int(current.get("seen_count", 0) or 0) + 1,
            "last_run_id": run_id,
            "last_outcome": outcome,
        }
    atomic_write_json(path, {"schema_version": 1, "updated_at": now, "evidence": records})
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_skill_synthesis_evidence.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_self_evolution/skill_synthesis/evidence.py src/codex_self_evolution/skill_synthesis/paths.py tests/test_skill_synthesis_evidence.py
git commit -m "feat: materialize skill synthesis evidence"
```

## Task 4: Agent Runner Contract

**Files:**
- Create: `src/codex_self_evolution/skill_synthesis/agent.py`
- Create: `tests/test_skill_synthesis_agent.py`

- [ ] **Step 1: Write failing agent tests**

Create `tests/test_skill_synthesis_agent.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_self_evolution.skill_synthesis.agent import (
    SkillSynthesisAgentError,
    build_pi_skill_synthesis_command,
    build_skill_synthesis_prompt,
    parse_agent_result,
)


def test_prompt_names_workspace_and_allowed_skills_root(tmp_path: Path) -> None:
    prompt = build_skill_synthesis_prompt(
        run_input_dir=tmp_path / "run" / "input",
        run_output_dir=tmp_path / "run" / "output",
        skills_root=tmp_path / "skills",
    )

    assert str(tmp_path / "run" / "input") in prompt
    assert str(tmp_path / "run" / "output" / "result.json") in prompt
    assert str(tmp_path / "skills" / "csep-synth-*") in prompt
    assert "Do not read files outside the input directory" in prompt


def test_build_pi_command_uses_independent_provider_model(tmp_path: Path) -> None:
    cmd = build_pi_skill_synthesis_command(
        prompt="hello",
        provider="minimax",
        model="MiniMax-M2.7",
    )

    assert cmd[:9] == [
        "pi", "-p", "--mode", "json", "--no-session", "--no-context-files",
        "--no-extensions", "--no-skills", "--no-prompt-templates",
    ]
    assert "--provider" in cmd
    assert cmd[cmd.index("--provider") + 1] == "minimax"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "MiniMax-M2.7"
    assert cmd[-1] == "hello"


def test_parse_agent_result_accepts_actions(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-1",
        "actions": [
            {
                "action": "create",
                "skill_id": "csep-synth-alpha",
                "path": "/tmp/skills/csep-synth-alpha/SKILL.md",
                "evidence_keys": ["ev1"],
                "reason": "repeated workflow",
            }
        ],
    }), encoding="utf-8")

    parsed = parse_agent_result(result_path)

    assert parsed["valid"] is True
    assert parsed["actions"][0]["skill_id"] == "csep-synth-alpha"


def test_parse_agent_result_marks_missing_or_invalid_partial(tmp_path: Path) -> None:
    missing = parse_agent_result(tmp_path / "missing.json")
    assert missing["valid"] is False
    assert missing["result_missing"] is True

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{bad", encoding="utf-8")
    invalid = parse_agent_result(invalid_path)
    assert invalid["valid"] is False
    assert invalid["result_invalid"] is True


def test_parse_agent_result_rejects_non_synth_skill_id(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-1",
        "actions": [{"action": "edit", "skill_id": "csep-legacy", "path": "", "evidence_keys": [], "reason": ""}],
    }), encoding="utf-8")

    parsed = parse_agent_result(result_path)

    assert parsed["valid"] is False
    assert parsed["result_invalid"] is True
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_skill_synthesis_agent.py -q
```

Expected: FAIL with missing module or functions.

- [ ] **Step 3: Add agent module**

Create `src/codex_self_evolution/skill_synthesis/agent.py`:

```python
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


class SkillSynthesisAgentError(RuntimeError):
    pass


AgentInvoker = Callable[[list[str], float], subprocess.CompletedProcess[str]]


def build_skill_synthesis_prompt(*, run_input_dir: Path, run_output_dir: Path, skills_root: Path) -> str:
    result_path = run_output_dir / "result.json"
    return (
        "You are the codex-self-evolution skill synthesis agent.\n\n"
        f"Input directory: {run_input_dir}\n"
        f"Output result file: {result_path}\n"
        f"Writable skills namespace: {skills_root}/csep-synth-*\n\n"
        "Rules:\n"
        "1. Do not read files outside the input directory.\n"
        "2. Write result.json only to the output result file.\n"
        "3. Write SKILL.md only under the writable skills namespace.\n"
        "4. Create, edit, or retire skills only for repeated reusable workflows.\n"
        "5. Do not create skills for one-off facts, temporary state, secrets, or account identifiers.\n"
        "6. Retired skills must use description \"Retired generated skill. Do not use.\" and csep_status: retired.\n\n"
        "After writing files, return exactly DONE."
    )


def build_pi_skill_synthesis_command(*, prompt: str, provider: str, model: str) -> list[str]:
    cmd = [
        "pi",
        "-p",
        "--mode",
        "json",
        "--no-session",
        "--no-context-files",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--tools",
        "read,write,edit,ls",
    ]
    if provider:
        cmd.extend(["--provider", provider])
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def run_pi_skill_synthesis_agent(
    *,
    run_input_dir: Path,
    run_output_dir: Path,
    skills_root: Path,
    provider: str,
    model: str,
    timeout_seconds: float,
    invoker: AgentInvoker | None = None,
) -> dict[str, Any]:
    if invoker is None and shutil.which("pi") is None:
        raise SkillSynthesisAgentError("pi_unavailable")
    prompt = build_skill_synthesis_prompt(
        run_input_dir=run_input_dir,
        run_output_dir=run_output_dir,
        skills_root=skills_root,
    )
    cmd = build_pi_skill_synthesis_command(prompt=prompt, provider=provider, model=model)
    runner = invoker or _subprocess_run
    proc = runner(cmd, timeout_seconds)
    if proc.returncode != 0:
        raise SkillSynthesisAgentError(f"pi exit={proc.returncode}; stderr={proc.stderr.strip()[:400]}")
    return parse_agent_result(run_output_dir / "result.json")


def _subprocess_run(cmd: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_seconds, check=False)


def parse_agent_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"valid": False, "actions": [], "result_missing": True, "result_invalid": False}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": str(exc)}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "schema_version"}
    actions = raw.get("actions")
    if not isinstance(actions, list):
        return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "actions"}
    parsed: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "action_not_object"}
        action = str(item.get("action") or "")
        skill_id = str(item.get("skill_id") or "")
        if action not in {"create", "edit", "retire", "skip"}:
            return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "bad_action"}
        if skill_id and not re.fullmatch(r"csep-synth-[a-z0-9-]+", skill_id):
            return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "bad_skill_id"}
        parsed.append({
            "action": action,
            "skill_id": skill_id,
            "path": str(item.get("path") or ""),
            "evidence_keys": [str(v) for v in item.get("evidence_keys", []) if isinstance(v, str)],
            "reason": str(item.get("reason") or ""),
        })
    return {
        "valid": True,
        "actions": parsed,
        "result_missing": False,
        "result_invalid": False,
        "notes": str(raw.get("notes") or ""),
    }
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_skill_synthesis_agent.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_self_evolution/skill_synthesis/agent.py tests/test_skill_synthesis_agent.py
git commit -m "feat: add skill synthesis agent contract"
```

## Task 5: Orchestrator And Receipts

**Files:**
- Create: `src/codex_self_evolution/skill_synthesis/runner.py`
- Create: `tests/test_skill_synthesis_runner.py`

- [ ] **Step 1: Write failing runner tests**

Create `tests/test_skill_synthesis_runner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.config_file import load_config
from codex_self_evolution.skill_synthesis.runner import run_skill_synthesis


def _write_config(home: Path, enabled: bool = True) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f"""
[skill_synthesis]
enabled = {str(enabled).lower()}
default_mode = "incremental"
lookback_hours = 24
lookback_days = 30
skills_prefix = "csep-synth-"

[skill_synthesis.agent]
backend = "agent:pi"
provider = "minimax"
model = "MiniMax-M2.7"
timeout_seconds = 1800
""", encoding="utf-8")


def test_run_skill_synthesis_skips_when_disabled(tmp_path: Path) -> None:
    _write_config(tmp_path, enabled=False)

    result = run_skill_synthesis(home=tmp_path, mode=None, lookback_hours=None, lookback_days=None, dry_run=False, agent_invoker=None)

    assert result["status"] == "skip_unconfigured"


def test_run_skill_synthesis_dry_run_uses_temp_root_and_writes_receipt(tmp_path: Path) -> None:
    _write_config(tmp_path)
    calls = []

    def fake_agent(**kwargs):
        calls.append(kwargs)
        skill = Path(kwargs["skills_root"]) / "csep-synth-alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: csep-synth-alpha\ndescription: Use when repeated alpha debugging needs command evidence.\n---\n\n"
            "# Alpha\n\n## Workflow\n\n1. Run `codex-self-evolution status`.\n2. Verify the receipt.\n3. Check output.\n",
            encoding="utf-8",
        )
        out = Path(kwargs["run_output_dir"]) / "result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "schema_version": 1,
            "run_id": kwargs["run_id"],
            "actions": [{"action": "create", "skill_id": "csep-synth-alpha", "path": str(skill), "evidence_keys": [], "reason": "test"}],
        }), encoding="utf-8")

    result = run_skill_synthesis(home=tmp_path, mode="incremental", lookback_hours=24, lookback_days=None, dry_run=True, agent_invoker=fake_agent)

    assert result["status"] == "success"
    assert result["dry_run"] is True
    assert result["detected_changed"]
    assert result["dry_run_leak"] is False
    assert Path(result["receipt_path"]).exists()
    assert calls[0]["skills_root"] != Path.home() / ".codex" / "skills"


def test_run_skill_synthesis_marks_partial_when_result_missing(tmp_path: Path) -> None:
    _write_config(tmp_path)

    def fake_agent(**kwargs):
        skill = Path(kwargs["skills_root"]) / "csep-synth-alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: csep-synth-alpha\ndescription: Use when repeated alpha debugging needs command evidence.\n---\n\n"
            "# Alpha\n\n## Workflow\n\n1. Run `codex-self-evolution status`.\n2. Verify receipt.\n3. Check output.\n",
            encoding="utf-8",
        )

    result = run_skill_synthesis(home=tmp_path, mode="incremental", lookback_hours=24, lookback_days=None, dry_run=True, agent_invoker=fake_agent)

    assert result["status"] == "partial"
    assert result["result_missing"] is True


def test_run_skill_synthesis_detects_dry_run_leak(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path)
    real_root = tmp_path / "real-skills"
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(real_root))

    def fake_agent(**kwargs):
        leaked = real_root / "csep-synth-leak" / "SKILL.md"
        leaked.parent.mkdir(parents=True)
        leaked.write_text("leak", encoding="utf-8")
        out = Path(kwargs["run_output_dir"]) / "result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"schema_version": 1, "run_id": kwargs["run_id"], "actions": []}), encoding="utf-8")

    result = run_skill_synthesis(home=tmp_path, mode="incremental", lookback_hours=24, lookback_days=None, dry_run=True, agent_invoker=fake_agent)

    assert result["status"] == "error"
    assert result["dry_run_leak"] is True
    assert result["changed_real_paths"] == [str(real_root / "csep-synth-leak" / "SKILL.md")]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_skill_synthesis_runner.py -q
```

Expected: FAIL with missing `runner`.

- [ ] **Step 3: Add runner module**

Create `src/codex_self_evolution/skill_synthesis/runner.py`:

```python
from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ..config_file import load_config
from ..storage import atomic_write_json
from .agent import run_pi_skill_synthesis_agent
from .evidence import (
    collect_evidence,
    load_evidence_index,
    materialize_evidence_workspace,
    update_evidence_index,
)
from .inventory import changed_paths, read_skills_inventory, snapshot_synth_skills, write_published_index
from .paths import build_skill_synthesis_paths, resolve_real_skills_root
from .validation import clear_invalid_marker, mark_invalid, validate_synth_skill


AgentCallable = Callable[..., None]


class SkillSynthesisLockError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_id() -> str:
    return uuid.uuid4().hex[:12]


def run_skill_synthesis(
    *,
    home: str | Path | None,
    mode: str | None,
    lookback_hours: int | None,
    lookback_days: int | None,
    dry_run: bool,
    agent_invoker: AgentCallable | None = None,
) -> dict[str, Any]:
    loaded = load_config(home=Path(home).expanduser().resolve() if home else None)
    cfg = loaded.config.skill_synthesis
    if not cfg.enabled:
        return {"status": "skip_unconfigured", "reason": "disabled"}
    if cfg.agent.backend != "agent:pi":
        return {"status": "error", "error": "unsupported_backend", "backend": cfg.agent.backend}

    run_mode = mode or cfg.default_mode
    hours = int(lookback_hours if lookback_hours is not None else cfg.lookback_hours)
    days = int(lookback_days if lookback_days is not None else cfg.lookback_days)
    run_id = _run_id()
    paths = build_skill_synthesis_paths(home=home, run_id=run_id)
    real_skills_root = resolve_real_skills_root()
    paths.root.mkdir(parents=True, exist_ok=True)

    try:
        with _skill_synthesis_lock(paths.lock_path):
            return _run_locked(
                home=paths.home,
                paths=paths,
                run_id=run_id,
                mode=run_mode,
                lookback_hours=hours,
                lookback_days=days,
                dry_run=dry_run,
                real_skills_root=real_skills_root,
                provider=cfg.agent.provider,
                model=cfg.agent.model,
                timeout_seconds=cfg.agent.timeout_seconds,
                agent_invoker=agent_invoker,
            )
    except SkillSynthesisLockError:
        return {"status": "skip_locked", "run_id": run_id}


def _run_locked(
    *,
    home: Path,
    paths,
    run_id: str,
    mode: str,
    lookback_hours: int,
    lookback_days: int,
    dry_run: bool,
    real_skills_root: Path,
    provider: str,
    model: str,
    timeout_seconds: float,
    agent_invoker: AgentCallable | None,
) -> dict[str, Any]:
    started_at = _now()
    evidence_index = load_evidence_index(paths.evidence_index_path)
    evidence = collect_evidence(
        home,
        mode=mode,
        lookback_hours=lookback_hours,
        lookback_days=lookback_days,
        evidence_index=evidence_index,
    )
    real_before = snapshot_synth_skills(real_skills_root)
    skills_root = Path(tempfile.mkdtemp(prefix=f"csep-skill-synthesis-{run_id}-")) / "skills" if dry_run else real_skills_root
    before = snapshot_synth_skills(skills_root)
    inventory = read_skills_inventory(real_skills_root)
    materialize_evidence_workspace(
        paths,
        evidence,
        skills_inventory=inventory,
        synth_inventory=inventory.get("synth", []),
    )

    agent_result = {"valid": False, "actions": [], "result_missing": True, "result_invalid": False}
    error = None
    try:
        if agent_invoker is not None:
            agent_invoker(
                run_id=run_id,
                run_input_dir=paths.run_input_dir,
                run_output_dir=paths.run_output_dir,
                skills_root=skills_root,
            )
            from .agent import parse_agent_result
            agent_result = parse_agent_result(paths.run_output_dir / "result.json")
        else:
            agent_result = run_pi_skill_synthesis_agent(
                run_input_dir=paths.run_input_dir,
                run_output_dir=paths.run_output_dir,
                skills_root=skills_root,
                provider=provider,
                model=model,
                timeout_seconds=timeout_seconds,
            )
    except Exception as exc:  # noqa: BLE001 - receipt should capture agent failures
        error = f"{type(exc).__name__}: {exc}"

    after = snapshot_synth_skills(skills_root)
    real_after = snapshot_synth_skills(real_skills_root)
    detected = changed_paths(before, after)
    changed_real = changed_paths(real_before, real_after) if dry_run else []
    valid, invalid, retired = _validate_changed(run_id, detected)
    result_missing = bool(agent_result.get("result_missing"))
    result_invalid = bool(agent_result.get("result_invalid"))
    mismatch = _reported_written(agent_result) != detected
    dry_run_leak = dry_run and bool(changed_real)
    status = "success"
    if error or dry_run_leak:
        status = "error"
    elif invalid or mismatch or result_missing or result_invalid:
        status = "partial"
    skipped = [item for item in agent_result.get("actions", []) if item.get("action") == "skip"]
    receipt = {
        "run_id": run_id,
        "status": status,
        "mode": mode,
        "lookback_hours": lookback_hours if mode == "incremental" else None,
        "lookback_days": lookback_days if mode == "full" else None,
        "dry_run": dry_run,
        "evidence_count": len(evidence),
        "new_evidence_count": sum(1 for item in evidence if not item.get("seen_before")),
        "seen_before_count": sum(1 for item in evidence if item.get("seen_before")),
        "reported_written": _reported_written(agent_result),
        "detected_changed": detected,
        "changed_real_paths": changed_real,
        "valid": valid,
        "invalid": invalid,
        "retired": retired,
        "skipped": skipped,
        "mismatch": mismatch,
        "dry_run_leak": dry_run_leak,
        "result_missing": result_missing,
        "result_invalid": result_invalid,
        "error": error,
        "started_at": started_at,
        "finished_at": _now(),
    }
    _write_receipts(paths, receipt)
    update_evidence_index(paths.evidence_index_path, evidence_index, evidence, run_id=run_id, outcome=status)
    write_published_index(paths.published_index_path, real_skills_root, read_skills_inventory(real_skills_root))
    return {**receipt, "receipt_path": str(paths.last_receipt_path)}


def _reported_written(agent_result: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for action in agent_result.get("actions", []):
        if action.get("action") in {"create", "edit", "retire"} and action.get("path"):
            out.append(str(action["path"]))
    return sorted(out)


def _validate_changed(run_id: str, changed: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    retired: list[dict[str, Any]] = []
    for raw in changed:
        skill_path = Path(raw)
        result = validate_synth_skill(skill_path)
        if result.get("valid"):
            clear_invalid_marker(skill_path)
            if result.get("status") == "retired":
                retired.append(result)
            else:
                valid.append(result)
        else:
            mark_invalid(skill_path, run_id=run_id, reasons=[str(result.get("reason") or "invalid")], evidence_keys=[])
            invalid.append(result)
    return valid, invalid, retired


def _write_receipts(paths, receipt: dict[str, Any]) -> None:
    safe_ts = receipt["finished_at"].replace(":", "-")
    receipt_path = paths.receipts_dir / f"{safe_ts}-{receipt['run_id']}.json"
    atomic_write_json(receipt_path, receipt)
    atomic_write_json(paths.last_receipt_path, receipt)


@contextmanager
def _skill_synthesis_lock(lock_path: Path):
    if lock_path.exists():
        raise SkillSynthesisLockError(f"skill synthesis already locked: {lock_path}")
    atomic_write_json(lock_path, {"created_at": _now(), "pid": os.getpid()})
    try:
        yield lock_path
    finally:
        if lock_path.exists():
            lock_path.unlink()
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_skill_synthesis_runner.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_self_evolution/skill_synthesis/runner.py tests/test_skill_synthesis_runner.py
git commit -m "feat: orchestrate skill synthesis runs"
```

## Task 6: CLI Subcommand

**Files:**
- Modify: `src/codex_self_evolution/cli.py`
- Create: `tests/test_skill_synthesis_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_skill_synthesis_cli.py`:

```python
from __future__ import annotations

import json

from codex_self_evolution import cli


def test_skill_synthesize_cli_prints_result(monkeypatch, capsys, tmp_path):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "dry_run": kwargs["dry_run"]}

    monkeypatch.setattr(cli, "run_skill_synthesis", fake_run)

    exit_code = cli.main([
        "skill-synthesize",
        "--home",
        str(tmp_path),
        "--mode",
        "full",
        "--lookback-days",
        "30",
        "--dry-run",
    ])

    assert exit_code == 0
    assert captured["home"] == str(tmp_path)
    assert captured["mode"] == "full"
    assert captured["lookback_days"] == 30
    assert captured["dry_run"] is True
    assert json.loads(capsys.readouterr().out)["status"] == "success"


def test_skill_synthesize_cli_defaults_to_config_mode(monkeypatch, capsys):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "skip_unconfigured"}

    monkeypatch.setattr(cli, "run_skill_synthesis", fake_run)

    exit_code = cli.main(["skill-synthesize"])

    assert exit_code == 0
    assert captured["mode"] is None
    assert captured["lookback_hours"] is None
    assert captured["lookback_days"] is None
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_skill_synthesis_cli.py -q
```

Expected: FAIL with parser rejecting `skill-synthesize`.

- [ ] **Step 3: Wire CLI parser and dispatcher**

In `src/codex_self_evolution/cli.py`, add import:

```python
from .skill_synthesis.runner import run_skill_synthesis
```

In `build_parser()`, add before config parser:

```python
    synth_parser = subparsers.add_parser(
        "skill-synthesize",
        help="Run global synthesized skill generation from memory/recall/done suggestions.",
    )
    synth_parser.add_argument("--home")
    synth_parser.add_argument("--mode", choices=("incremental", "full"))
    synth_parser.add_argument("--lookback-hours", type=int)
    synth_parser.add_argument("--lookback-days", type=int)
    synth_parser.add_argument("--dry-run", action="store_true")
```

In `main()`, add before `status`:

```python
        elif args.command == "skill-synthesize":
            result = run_skill_synthesis(
                home=args.home,
                mode=args.mode,
                lookback_hours=args.lookback_hours,
                lookback_days=args.lookback_days,
                dry_run=args.dry_run,
            )
```

In `_observability_extras()`, add a branch if the function exists in the file:

```python
    if command == "skill-synthesize":
        return {
            "status": result.get("status"),
            "mode": result.get("mode"),
            "dry_run": result.get("dry_run"),
            "evidence_count": result.get("evidence_count", 0),
            "valid_count": len(result.get("valid") or []),
            "invalid_count": len(result.get("invalid") or []),
            "retired_count": len(result.get("retired") or []),
            "mismatch": result.get("mismatch"),
            "dry_run_leak": result.get("dry_run_leak"),
        }
```

- [ ] **Step 4: Run focused CLI tests**

Run:

```bash
uv run pytest tests/test_skill_synthesis_cli.py tests/test_csep_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_self_evolution/cli.py tests/test_skill_synthesis_cli.py
git commit -m "feat: add skill synthesis cli"
```

## Task 7: Disable Compiler Skill Production

**Files:**
- Modify: `src/codex_self_evolution/compiler/backends.py`
- Modify: `src/codex_self_evolution/compiler/engine.py`
- Modify: `src/codex_self_evolution/review/prompt.md`
- Modify existing tests as needed:
  - `tests/test_compiler_skills.py`
  - `tests/test_agent_compile_io.py`
  - `tests/test_agent_compiler_backend.py`
  - `tests/test_end_to_end.py`
- Create: `tests/test_compiler_skill_disabled.py`

- [ ] **Step 1: Write failing compiler-disabled tests**

Create `tests/test_compiler_skill_disabled.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.compiler.backends import ScriptCompilerBackend, build_compile_context
from codex_self_evolution.compiler.engine import run_compile
from codex_self_evolution.config import build_paths
from codex_self_evolution.schemas import Suggestion, SuggestionEnvelope
from codex_self_evolution.storage import append_pending_suggestion


def _envelope(suggestions: list[Suggestion]) -> SuggestionEnvelope:
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="s1",
        idempotency_key="i1",
        thread_id="t1",
        cwd="/repo",
        repo_fingerprint="repo",
        reviewer_timestamp="2026-05-13T00:00:00Z",
        suggestions=suggestions,
        source_authority=[],
    )


def test_script_compiler_discards_skill_action_without_outputs(tmp_path: Path) -> None:
    paths = build_paths(repo_root=tmp_path, state_dir=tmp_path / "state")
    envelope = _envelope([
        Suggestion(
            family="skill_action",
            summary="create skill",
            details={
                "action": "create",
                "skill_id": "old",
                "title": "Old",
                "description": "Use when old flow repeats.",
                "content": "Workflow steps are old.",
            },
        )
    ])
    context = build_compile_context(paths, [envelope])

    artifacts = ScriptCompilerBackend().compile([envelope], context, {})

    assert artifacts.compiled_skills == []
    assert artifacts.manifest_entries == []
    assert artifacts.discarded_items[0]["reason"] == "skill_action_disabled"


def test_run_compile_does_not_write_managed_skill_or_global_projection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "codex-skills"))
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    paths = build_paths(repo_root=repo, state_dir=state)
    append_pending_suggestion(paths, _envelope([
        Suggestion(
            family="skill_action",
            summary="create skill",
            details={
                "action": "create",
                "skill_id": "old",
                "title": "Old",
                "description": "Use when old flow repeats.",
                "content": "Workflow steps are old.",
            },
        )
    ]))

    result = run_compile(repo_root=repo, state_dir=state, backend="script")

    assert result["status"] == "success"
    assert not (state / "skills" / "managed" / "old.md").exists()
    assert not (tmp_path / "codex-skills" / "csep-old").exists()
    receipt = json.loads((state / "compiler" / "last_receipt.json").read_text(encoding="utf-8"))
    assert receipt["managed_skills"] == 0
    assert any(item.get("reason") == "skill_action_disabled" for item in receipt["item_receipts"])
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_compiler_skill_disabled.py -q
```

Expected: FAIL because compiler still compiles and publishes skills.

- [ ] **Step 3: Stop skill compilation in backends**

In `src/codex_self_evolution/compiler/backends.py`, add helper near `ScriptCompilerBackend`:

```python
def _discard_disabled_skill_actions(batch: list[SuggestionEnvelope]) -> list[dict[str, Any]]:
    discarded: list[dict[str, Any]] = []
    for envelope in batch:
        for suggestion in envelope.suggestions:
            if suggestion.family == "skill_action":
                discarded.append({
                    "summary": suggestion.summary,
                    "reason": "skill_action_disabled",
                    "detail": "compiler no longer produces skills; use skill-synthesize",
                })
    return discarded
```

In `ScriptCompilerBackend.compile()`, replace:

```python
        compiled_skills, discarded_items = compile_skills(all_suggestions, existing_entries=existing_manifest)
        discarded_items = [*recall_discarded, *discarded_items]
        manifest_entries = build_manifest_entries(compiled_skills, context["skills_dir"], existing_entries=existing_manifest)
```

with:

```python
        compiled_skills: list[dict[str, Any]] = []
        discarded_items = [*recall_discarded, *_discard_disabled_skill_actions(batch)]
        manifest_entries: list[SkillManifestEntry] = list(existing_manifest)
```

For agent backends, after parsing `parsed`, override:

```python
            parsed["compiled_skills"] = []
            parsed["manifest_entries"] = list(context.get("existing_manifest") or [])
            parsed["discarded_items"] = [
                *parsed.get("discarded_items", []),
                *_discard_disabled_skill_actions(batch),
            ]
```

Apply that override in both JSON mode and Pi edit workspace mode before returning `CompileArtifacts`.

- [ ] **Step 4: Stop writer from publishing compiler skills**

In `src/codex_self_evolution/compiler/engine.py`, change both calls to `apply_compiler_outputs(... publish_global_skills_enabled=True)` to:

```python
                publish_global_skills_enabled=False,
```

and:

```python
            publish_global_skills_enabled=False,
```

Keep `_write_skills()` in place for legacy tests and future cleanup, but the main compiler path must not publish global skills.

- [ ] **Step 5: Remove reviewer prompt skill encouragement**

In `src/codex_self_evolution/review/prompt.md`, remove instructions that ask for new `skill_action` output. Replace the skill section with:

```markdown
`skill_action` is deprecated. Return an empty array for this field if the
schema still asks for it. Reusable workflow synthesis is handled later by the
periodic `skill-synthesize` command.
```

Keep top-level JSON compatible if existing parser still expects the key.

- [ ] **Step 6: Update tests that expected compiler skill publishing**

Adjust tests that currently assert `csep-*` global projection from compiler. Replace those assertions with:

```python
assert receipt["managed_skills"] == 0
assert not (tmp_path / "codex-skills" / "csep-test-skill" / "SKILL.md").exists()
```

Keep `tests/test_compiler_skills.py` if it remains a unit test for disconnected legacy helpers. Rename comments to "legacy helper" so future readers do not infer the main compiler still uses it.

- [ ] **Step 7: Run focused compiler tests**

Run:

```bash
uv run pytest tests/test_compiler_skill_disabled.py tests/test_compiler_memory.py tests/test_compiler_recall.py tests/test_end_to_end.py tests/test_agent_compiler_backend.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/codex_self_evolution/compiler/backends.py src/codex_self_evolution/compiler/engine.py src/codex_self_evolution/review/prompt.md tests/test_compiler_skill_disabled.py tests/test_compiler_skills.py tests/test_agent_compile_io.py tests/test_agent_compiler_backend.py tests/test_end_to_end.py
git commit -m "feat: disable compiler skill publishing"
```

## Task 8: Diagnostics And Status

**Files:**
- Modify: `src/codex_self_evolution/diagnostics.py`
- Create: `tests/test_diagnostics_skill_synthesis.py`

- [ ] **Step 1: Write failing diagnostics tests**

Create `tests/test_diagnostics_skill_synthesis.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.diagnostics import collect_status


def test_status_reports_skill_synthesis_receipt_and_counts(tmp_path: Path, monkeypatch) -> None:
    skills_root = tmp_path / "skills"
    active = skills_root / "csep-synth-active"
    active.mkdir(parents=True)
    (active / "SKILL.md").write_text(
        "---\nname: csep-synth-active\ndescription: Use when active repeats.\n---\n\nWorkflow steps check verify run output.\n",
        encoding="utf-8",
    )
    invalid = skills_root / "csep-synth-invalid"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("bad", encoding="utf-8")
    (invalid / "CSEP_INVALID.json").write_text("{}", encoding="utf-8")
    legacy = skills_root / "csep-legacy"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text("---\nname: csep-legacy\ndescription: legacy\n---\n", encoding="utf-8")

    synth_dir = tmp_path / "skill_synthesis"
    synth_dir.mkdir()
    (synth_dir / "last_receipt.json").write_text(json.dumps({"status": "partial", "run_id": "run-1"}), encoding="utf-8")
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(skills_root))

    status = collect_status(home=tmp_path)

    assert status["skill_synthesis"]["last_receipt"]["status"] == "partial"
    assert status["skills"]["synthesized"]["active"] == 1
    assert status["skills"]["synthesized"]["invalid"] == 1
    assert status["skills"]["compiler_legacy"]["active"] == 1
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/test_diagnostics_skill_synthesis.py -q
```

Expected: FAIL because `skill_synthesis` and `skills` keys are missing from status.

- [ ] **Step 3: Add status helpers**

In `src/codex_self_evolution/diagnostics.py`, add constants near `LAUNCHD_LABEL`:

```python
SKILL_SYNTHESIS_LAUNCHD_LABEL = "com.codex-self-evolution.skill-synthesis"
```

Add imports:

```python
from .managed_skills.publish import codex_skills_dir
from .skill_synthesis.inventory import read_skills_inventory
```

Add to `collect_status()` return dict:

```python
        "skill_synthesis": _check_skill_synthesis(home_dir),
        "skills": _check_skill_counts(),
```

Add helper functions:

```python
def _check_skill_synthesis(home_dir: Path) -> dict[str, Any]:
    root = home_dir / "skill_synthesis"
    return {
        "root": str(root),
        "exists": root.exists(),
        "scheduler": _check_launchd_label(SKILL_SYNTHESIS_LAUNCHD_LABEL),
        "last_receipt": _read_last_receipt(root / "last_receipt.json"),
    }


def _check_skill_counts() -> dict[str, Any]:
    root = codex_skills_dir()
    inventory = read_skills_inventory(root)
    synth = inventory["synth"]
    legacy = [
        item for item in inventory["non_synth"]
        if Path(item["path"]).parent.name.startswith("csep-")
    ]
    return {
        "skills_root": str(root),
        "synthesized": {
            "active": sum(1 for item in synth if not item.get("invalid_marker") and item.get("csep_status") != "retired"),
            "invalid": sum(1 for item in synth if item.get("invalid_marker")),
            "retired": sum(1 for item in synth if item.get("csep_status") == "retired"),
        },
        "compiler_legacy": {
            "active": len(legacy),
            "note": "legacy; compiler no longer creates or updates skills",
        },
    }


def _check_launchd_label(label: str) -> dict[str, Any]:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    result = {"label": label, "plist_path": str(plist_path), "plist_exists": plist_path.exists(), "loaded": False, "error": None}
    if shutil.which("launchctl") is None:
        result["error"] = "launchctl not available on this host"
        return result
    try:
        proc = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = f"launchctl list failed: {exc}"
        return result
    result["loaded"] = any(line.strip().endswith(label) for line in proc.stdout.splitlines())
    return result
```

Then simplify `_check_scheduler()` to call `_check_launchd_label(LAUNCHD_LABEL)` and preserve the existing shape.

- [ ] **Step 4: Run diagnostics tests**

Run:

```bash
uv run pytest tests/test_diagnostics_skill_synthesis.py tests/test_diagnostics.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_self_evolution/diagnostics.py tests/test_diagnostics_skill_synthesis.py
git commit -m "feat: report skill synthesis status"
```

## Task 9: Scheduler Scripts

**Files:**
- Create: `scripts/install-skill-synthesis-scheduler.sh`
- Create: `scripts/uninstall-skill-synthesis-scheduler.sh`
- Create: `tests/test_skill_synthesis_scheduler.py`

- [ ] **Step 1: Write failing scheduler tests**

Create `tests/test_skill_synthesis_scheduler.py`:

```python
from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path


def _write_executable(path: Path, text: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_skill_synthesis_scheduler_plist(tmp_path: Path, monkeypatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_cli = fake_bin / "codex-self-evolution"
    _write_executable(local_cli)
    _write_executable(fake_bin / "pi")
    _write_executable(fake_bin / "launchctl")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    subprocess.run(["bash", "scripts/install-skill-synthesis-scheduler.sh"], check=True)

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.codex-self-evolution.skill-synthesis.plist"
    plist = plistlib.loads(plist_path.read_bytes())

    assert plist["Label"] == "com.codex-self-evolution.skill-synthesis"
    assert plist["ProgramArguments"] == [
        str(local_cli),
        "skill-synthesize",
        "--mode",
        "incremental",
        "--lookback-hours",
        "24",
    ]
    assert plist["StartInterval"] == 14400
    assert plist["RunAtLoad"] is False
    assert "skill-synthesis.launchd.stdout.log" in plist["StandardOutPath"]
    assert "uvx" not in json.dumps(plist)
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/test_skill_synthesis_scheduler.py -q
```

Expected: FAIL because scripts do not exist.

- [ ] **Step 3: Add install script**

Create `scripts/install-skill-synthesis-scheduler.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_HOME="$HOME/.codex-self-evolution"
LOG_DIR="$PLUGIN_HOME/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL="com.codex-self-evolution.skill-synthesis"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
ENTRY_POINT="${CSEP_ENTRY_POINT:-codex-self-evolution}"
INTERVAL_SECONDS="${CSEP_SKILL_SYNTHESIS_INTERVAL:-14400}"
LOOKBACK_HOURS="${CSEP_SKILL_SYNTHESIS_LOOKBACK_HOURS:-24}"
ARGS=("skill-synthesize" "--mode" "incremental" "--lookback-hours" "$LOOKBACK_HOURS")

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

info "preflight checks"
command -v "$ENTRY_POINT" >/dev/null 2>&1 || fail "$ENTRY_POINT not found on PATH. Run scripts/install.sh first."
ENTRY_POINT_BIN="$(command -v "$ENTRY_POINT")"
ENTRY_POINT_DIR="$(dirname "$ENTRY_POINT_BIN")"
echo "  $ENTRY_POINT OK at $ENTRY_POINT_BIN"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

PI_BIN="$(command -v pi 2>/dev/null || true)"
if [ -n "$PI_BIN" ]; then
    PI_DIR="$(dirname "$PI_BIN")"
    echo "  pi found at $PI_BIN"
else
    PI_DIR=""
    warn "pi not on PATH — skill synthesis agent:pi will not run."
fi

PLIST_PATH_ENV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
for dir in "$ENTRY_POINT_DIR" "$PI_DIR"; do
    [ -z "$dir" ] && continue
    case ":$PLIST_PATH_ENV:" in
        *":$dir:"*) ;;
        *) PLIST_PATH_ENV="$dir:$PLIST_PATH_ENV" ;;
    esac
done

if [ -f "$PLIST_PATH" ]; then
    info "removing previous $LABEL install"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

info "writing $PLIST_PATH"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ENTRY_POINT_BIN</string>
        <string>${ARGS[0]}</string>
        <string>${ARGS[1]}</string>
        <string>${ARGS[2]}</string>
        <string>${ARGS[3]}</string>
        <string>${ARGS[4]}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$PLIST_PATH_ENV</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
    <key>StartInterval</key>
    <integer>$INTERVAL_SECONDS</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/skill-synthesis.launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/skill-synthesis.launchd.stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>60</integer>
</dict>
</plist>
PLIST

info "loading $LABEL into launchd"
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

info "done."
echo ""
echo "Skill synthesis scheduler installed:"
echo "  label:    $LABEL"
echo "  interval: ${INTERVAL_SECONDS}s (override via CSEP_SKILL_SYNTHESIS_INTERVAL)"
echo "  command:  $ENTRY_POINT_BIN ${ARGS[*]}"
echo "  plist:    $PLIST_PATH"
echo "  logs:     $LOG_DIR/skill-synthesis.launchd.{stdout,stderr}.log"
echo ""
echo "Recommended first smoke:"
echo "  codex-self-evolution skill-synthesize --mode full --lookback-days 30 --dry-run"
echo ""
echo "Trigger real run manually:"
echo "  launchctl kickstart gui/\$(id -u)/$LABEL"
echo ""
echo "Remove later: $REPO/scripts/uninstall-skill-synthesis-scheduler.sh"
```

- [ ] **Step 4: Add uninstall script**

Create `scripts/uninstall-skill-synthesis-scheduler.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

LABEL="com.codex-self-evolution.skill-synthesis"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST_PATH"

echo "Removed $LABEL"
```

Run:

```bash
chmod +x scripts/install-skill-synthesis-scheduler.sh scripts/uninstall-skill-synthesis-scheduler.sh
```

Expected: no output.

- [ ] **Step 5: Run scheduler tests**

Run:

```bash
uv run pytest tests/test_skill_synthesis_scheduler.py tests/test_scheduler_integration.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/install-skill-synthesis-scheduler.sh scripts/uninstall-skill-synthesis-scheduler.sh tests/test_skill_synthesis_scheduler.py
git commit -m "feat: add skill synthesis scheduler"
```

## Task 10: Plugin Manifest And Docs

**Files:**
- Modify: `src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json`
- Modify: `plugins/codex-self-evolution/.codex-plugin/plugin.json`
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `tests/test_plugin_bundle_hooks.py`

- [ ] **Step 1: Write failing plugin manifest test**

In `tests/test_plugin_bundle_hooks.py`, add:

```python
def test_plugin_manifest_exposes_skill_synthesize_command():
    manifest = _load_json(ROOT / "src" / "codex_self_evolution" / "plugin_bundle" / ".codex-plugin" / "plugin.json")
    commands = {entry["name"]: entry["command"] for entry in manifest["commands"]}

    assert commands["skill-synthesize"] == "codex-self-evolution skill-synthesize"
    assert manifest["scheduler"]["skill_synthesis_command"] == (
        "codex-self-evolution skill-synthesize --mode incremental --lookback-hours 24"
    )
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/test_plugin_bundle_hooks.py::test_plugin_manifest_exposes_skill_synthesize_command -q
```

Expected: FAIL with missing command.

- [ ] **Step 3: Update both plugin manifests**

In both plugin manifests, add command:

```json
{
  "name": "skill-synthesize",
  "command": "codex-self-evolution skill-synthesize"
}
```

Add scheduler field:

```json
"skill_synthesis_command": "codex-self-evolution skill-synthesize --mode incremental --lookback-hours 24"
```

Keep the two manifest files byte-for-byte aligned.

- [ ] **Step 4: Update README and getting started docs**

In `README.md`, add a short section near compile/scheduler commands:

````markdown
### Periodic Skill Synthesis

Compiler no longer creates generated skills. It promotes memory and recall only.
Reusable workflow skills are synthesized by a separate global command:

```bash
codex-self-evolution skill-synthesize --mode full --lookback-days 30 --dry-run
codex-self-evolution skill-synthesize --mode incremental --lookback-hours 24
```

Install the independent 4-hour launchd job with:

```bash
scripts/install-skill-synthesis-scheduler.sh
```

Synthesized skills are written only under `~/.codex/skills/csep-synth-*`.
Existing `csep-*` compiler skills are legacy read-only artifacts.
````

In `docs/getting-started.md`, add the same command list under the scheduler setup area.

- [ ] **Step 5: Run docs/manifest tests**

Run:

```bash
uv run pytest tests/test_plugin_bundle_hooks.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json plugins/codex-self-evolution/.codex-plugin/plugin.json README.md docs/getting-started.md tests/test_plugin_bundle_hooks.py
git commit -m "docs: document skill synthesis command"
```

## Task 11: Full Verification

**Files:**
- No new files

- [ ] **Step 1: Run focused skill synthesis suite**

Run:

```bash
uv run pytest tests/test_skill_synthesis_config.py tests/test_skill_synthesis_inventory.py tests/test_skill_synthesis_validation.py tests/test_skill_synthesis_evidence.py tests/test_skill_synthesis_agent.py tests/test_skill_synthesis_runner.py tests/test_skill_synthesis_cli.py tests/test_skill_synthesis_scheduler.py tests/test_diagnostics_skill_synthesis.py tests/test_compiler_skill_disabled.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run shell syntax checks**

Run:

```bash
bash -n scripts/install-scheduler.sh scripts/uninstall-scheduler.sh scripts/install-skill-synthesis-scheduler.sh scripts/uninstall-skill-synthesis-scheduler.sh
```

Expected: no output.

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Run dry-run CLI smoke with temp home**

Run:

```bash
tmp_home="$(mktemp -d)"
CODEX_SELF_EVOLUTION_HOME="$tmp_home" CSEP_CODEX_SKILLS_DIR="$tmp_home/skills" \
  codex-self-evolution skill-synthesize --mode full --lookback-days 30 --dry-run
```

Expected: JSON output with `status` equal to `success`, `partial`, or `error`. If Pi is unavailable in the execution environment, expected status is `error` with a clear Pi availability message in the receipt. The command must not write outside `$tmp_home/skills`.

- [ ] **Step 6: Commit verification-only docs if changed**

If verification required updating docs or tests, commit those changes:

```bash
git add README.md docs/getting-started.md tests
git commit -m "test: verify skill synthesis integration"
```

If no files changed, skip this commit.

## Self-Review

**Spec coverage:** Covered independent config, default-enabled Minimax, global state layout, run workspaces, evidence collection, redaction, agent SOP/result schema, direct `csep-synth-*` writes, dry-run leak detection, receipts, indices, invalid markers, retired format, scheduler, status, and compiler skill deprecation.

**Placeholder scan:** This plan avoids vague implementation instructions. Every code-changing task names exact files, functions, tests, commands, and expected outcomes.

**Type consistency:** `run_skill_synthesis()`, `SkillSynthesisPaths`, evidence dict fields, `result.json` fields, receipt fields, and status keys are named consistently across tests and implementation snippets.
