"""Worktree consolidation: collapse bucket directories that belong to the same
logical repo into a single canonical bucket.

Context: The plugin used to key state dirs by ``mangle(cwd)``. For users who
work in multiple git worktrees of the same repo (``repo/``, ``repo_feature/``,
``repo_feature_v2/``) that produced one bucket per worktree — so the same
architecture decision got learned three times without any of the buckets
seeing what the others had already recorded.

The live pipeline now keys buckets by the canonical repo root (common
``.git`` dir parent) via :func:`codex_self_evolution.config.resolve_bucket_key`,
so new writes automatically land in the right place. This module provides the
one-shot migration for pre-existing buckets so users aren't stuck with a
split view forever.

Design choices (matching what was agreed during design discussion):

- **Archive, don't delete** — consolidated buckets are renamed to
  ``<name>.archived.<ts>``. The scheduler's :func:`scan_all_projects`
  explicitly skips ``.archived`` directories so the stale buckets stop
  producing receipts, but the data is still on disk for inspection/rollback.
- **Minimal merge surface** — we consolidate ``memory.json`` (re-rendered to
  MEMORY.md/USER.md via the existing writer) and move ``suggestions/pending/``
  into the target so queued-but-not-yet-compiled work continues. Everything
  else (done/failed history, recall snapshots, skills, compiler receipts)
  stays in the archived bucket; it's historical and not exercised by the
  live pipeline.
- **Dry-run first** — the caller can preview the plan before anything gets
  renamed. Guarded by an ``apply`` flag on :func:`plan_and_run`.

Non-goals for this pass: cross-clone consolidation (two independent clones
of the same origin URL), deduping *within* already-consolidated buckets, or
fixing stale ``repo_fingerprint`` fields in historical suggestion files.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compiler.engine import _render_memory_markdown
from .compiler.memory import _normalize_existing_entry
from .config import (
    ARCHIVED_BUCKET_SUFFIX,
    CANONICAL_CWD_MARKER,
    PROJECTS_SUBDIR,
    detect_repo_identity,
    get_home_dir,
    is_archived_bucket,
    mangle_project_path,
    unmangle_bucket_name,
)
from .storage import atomic_write_json, atomic_write_text, load_json


@dataclass
class BucketPlan:
    """Planned consolidation for a single source bucket."""

    source_bucket: str
    source_path: Path
    source_cwd: Path
    target_bucket: str
    target_path: Path
    target_cwd: Path
    source_memory_entries: int
    pending_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_bucket": self.source_bucket,
            "source_path": str(self.source_path),
            "source_cwd": str(self.source_cwd),
            "target_bucket": self.target_bucket,
            "target_path": str(self.target_path),
            "target_cwd": str(self.target_cwd),
            "source_memory_entries": self.source_memory_entries,
            "pending_count": self.pending_count,
        }


@dataclass
class BucketSkip:
    """A bucket we looked at but aren't migrating, with a reason."""

    bucket: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"bucket": self.bucket, "reason": self.reason}


@dataclass
class MigrationPlan:
    home: Path
    plans: list[BucketPlan] = field(default_factory=list)
    skipped: list[BucketSkip] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": str(self.home),
            "plans": [plan.to_dict() for plan in self.plans],
            "skipped": [skip.to_dict() for skip in self.skipped],
            "counts": {
                "to_migrate": len(self.plans),
                "skipped": len(self.skipped),
            },
        }


def _resolve_bucket_cwd(bucket_path: Path, bucket_name: str) -> Path:
    """Figure out which cwd a bucket was built for.

    Prefers the ``.canonical_cwd`` sidecar file (written by ``build_paths``)
    because the name-based :func:`unmangle_bucket_name` is lossy — any ``-``
    in the original path collides with the ``/`` → ``-`` substitution. Falls
    back to unmangling for legacy buckets that predate the marker.
    """
    marker = bucket_path / CANONICAL_CWD_MARKER
    if marker.is_file():
        try:
            raw = marker.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        if raw:
            return Path(raw)
    return unmangle_bucket_name(bucket_name)


def _count_memory_entries(bucket_path: Path) -> int:
    path = bucket_path / "memory" / "memory.json"
    if not path.exists():
        return 0
    try:
        data = load_json(path)
    except Exception:  # noqa: BLE001 — corrupt memory.json should not abort planning
        return 0
    if not isinstance(data, dict):
        return 0
    count = 0
    for scope in ("user", "global"):
        items = data.get(scope, []) or []
        if isinstance(items, list):
            count += sum(1 for item in items if isinstance(item, dict))
    return count


def _count_pending(bucket_path: Path) -> int:
    pending_dir = bucket_path / "suggestions" / "pending"
    if not pending_dir.is_dir():
        return 0
    return sum(1 for item in pending_dir.iterdir() if item.is_file() and item.suffix == ".json")


def plan_migration(home: Path | None = None) -> MigrationPlan:
    """Inspect every bucket under ``<home>/projects/`` and decide which ones
    belong to a worktree that should be folded into a canonical sibling.

    A bucket becomes a migration candidate when:

    1. Its ``unmangle`` back to a path still exists on disk (we have the file
       system available to probe git state).
    2. That path is inside a git worktree.
    3. The worktree's canonical identity differs from its own path — i.e. the
       bucket is not the main repo worktree.

    Buckets that skip any of the above show up in ``plan.skipped`` with a
    human-readable reason, so dry-run output stays transparent.
    """
    home = home or get_home_dir()
    projects_dir = home / PROJECTS_SUBDIR
    plan = MigrationPlan(home=home)
    if not projects_dir.is_dir():
        return plan

    for bucket_path in sorted(projects_dir.iterdir()):
        if not bucket_path.is_dir():
            continue
        name = bucket_path.name
        if is_archived_bucket(name):
            plan.skipped.append(BucketSkip(bucket=name, reason="already archived"))
            continue

        candidate_cwd = _resolve_bucket_cwd(bucket_path, name)
        if not candidate_cwd.exists():
            plan.skipped.append(
                BucketSkip(bucket=name, reason="original cwd no longer exists on disk")
            )
            continue

        identity = detect_repo_identity(candidate_cwd)
        if identity is None:
            plan.skipped.append(
                BucketSkip(bucket=name, reason="not a git worktree (or git unavailable)")
            )
            continue

        if identity == candidate_cwd:
            plan.skipped.append(
                BucketSkip(bucket=name, reason="already the canonical worktree")
            )
            continue

        target_bucket_name = mangle_project_path(identity)
        target_path = projects_dir / target_bucket_name
        plan.plans.append(
            BucketPlan(
                source_bucket=name,
                source_path=bucket_path,
                source_cwd=candidate_cwd,
                target_bucket=target_bucket_name,
                target_path=target_path,
                target_cwd=identity,
                source_memory_entries=_count_memory_entries(bucket_path),
                pending_count=_count_pending(bucket_path),
            )
        )

    return plan


def _merge_memory(source_path: Path, target_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Merge source memory.json INTO target memory.json, dedup by (scope, content).

    Target may not exist yet (first consolidation into a fresh canonical
    bucket); in that case we materialise it from source. Returns the merged
    dict so callers can also re-render MEMORY.md / USER.md.
    """
    def _load(path: Path) -> dict[str, list[dict[str, Any]]]:
        if not path.exists():
            return {"user": [], "global": []}
        try:
            data = load_json(path)
        except Exception:  # noqa: BLE001 — corrupt files contribute nothing
            return {"user": [], "global": []}
        return {
            "user": data.get("user", []) or [] if isinstance(data, dict) else [],
            "global": data.get("global", []) or [] if isinstance(data, dict) else [],
        }

    source_memory = source_path / "memory" / "memory.json"
    target_memory = target_path / "memory" / "memory.json"

    # Target wins on identical (scope, content) — target is the canonical
    # bucket so its confidence/provenance take precedence.
    merged: dict[str, list[dict[str, Any]]] = {"user": [], "global": []}
    seen_keys: set[tuple[str, str]] = set()
    for store in (_load(target_memory), _load(source_memory)):
        for scope in ("user", "global"):
            for raw in store.get(scope, []) or []:
                if not isinstance(raw, dict):
                    continue
                normalized = _normalize_existing_entry(scope, raw)
                if normalized is None:
                    continue
                key = (scope, normalized["content"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged[scope].append(normalized)
    return merged


def _apply_one(bucket_plan: BucketPlan) -> dict[str, Any]:
    """Execute a single consolidation: merge memory, move pending, archive source."""
    source = bucket_plan.source_path
    target = bucket_plan.target_path
    target_memory_dir = target / "memory"
    target_suggestions_pending = target / "suggestions" / "pending"
    target_memory_dir.mkdir(parents=True, exist_ok=True)
    target_suggestions_pending.mkdir(parents=True, exist_ok=True)

    merged = _merge_memory(source, target)
    atomic_write_json(target_memory_dir / "memory.json", merged)
    atomic_write_text(
        target_memory_dir / "USER.md",
        _render_memory_markdown("USER", merged["user"]),
    )
    atomic_write_text(
        target_memory_dir / "MEMORY.md",
        _render_memory_markdown("MEMORY", merged["global"]),
    )

    # Move pending suggestions so queued-but-uncompiled work keeps flowing
    # after archive. Done / failed / discarded / processing stay put: they're
    # historical receipts tied to snapshots in this same bucket, moving them
    # would dangle the snapshot paths.
    moved_pending = 0
    source_pending = source / "suggestions" / "pending"
    if source_pending.is_dir():
        for item in sorted(source_pending.iterdir()):
            if not (item.is_file() and item.suffix == ".json"):
                continue
            dest = target_suggestions_pending / item.name
            if dest.exists():
                # Same content-hash filename means the same suggestion was
                # already queued in the target bucket — skip without error.
                item.unlink()
            else:
                shutil.move(str(item), str(dest))
            moved_pending += 1

    # Rename the source bucket so the scheduler stops picking it up. The
    # suffix is timestamped so repeated migrations never collide.
    ts = time.strftime("%Y%m%dT%H%M%S")
    archived_name = f"{source.name}{ARCHIVED_BUCKET_SUFFIX}.{ts}"
    archived_path = source.parent / archived_name
    source.rename(archived_path)

    return {
        "source_bucket": bucket_plan.source_bucket,
        "target_bucket": bucket_plan.target_bucket,
        "archived_as": archived_path.name,
        "merged_memory_entries": len(merged["user"]) + len(merged["global"]),
        "moved_pending": moved_pending,
    }


def run_migration(home: Path | None = None, apply: bool = False) -> dict[str, Any]:
    """Plan, and optionally apply, worktree bucket consolidation.

    Returns a summary dict shaped for JSON printing by the CLI.
    """
    plan = plan_migration(home=home)
    result: dict[str, Any] = plan.to_dict()
    result["applied"] = False
    result["apply_results"] = []
    if apply and plan.plans:
        for bucket_plan in plan.plans:
            result["apply_results"].append(_apply_one(bucket_plan))
        result["applied"] = True
    return result
