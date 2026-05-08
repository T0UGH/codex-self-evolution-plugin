from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..schemas import SkillManifestEntry, SuggestionEnvelope
from .backends import get_backend


def evaluate_compiler_fixture(
    fixture_path: str | Path,
    *,
    backend: str = "script",
    compile_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay a saved compiler-quality fixture without mutating runtime state."""
    path = Path(fixture_path)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    batch = [SuggestionEnvelope.from_dict(item) for item in fixture.get("batch", [])]
    context = _normalize_context(fixture.get("context") or {}, batch)
    options = {"allow_fallback": False, **(compile_options or {})}
    try:
        artifacts = get_backend(backend).compile(batch, context, options)
    except Exception as exc:  # noqa: BLE001 - eval reports failures as data
        return {
            "name": fixture.get("name") or path.stem,
            "fixture_path": str(path),
            "backend": backend,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "metrics": {},
            "failures": ["compiler_error"],
        }

    metrics = _artifact_metrics(artifacts)
    failures = _check_expectations(metrics, fixture.get("expect") or {})
    return {
        "name": fixture.get("name") or path.stem,
        "fixture_path": str(path),
        "backend": artifacts.backend_name,
        "status": "pass" if not failures else "fail",
        "metrics": metrics,
        "failures": failures,
        "compiler_observability": artifacts.compiler_observability,
    }


def _normalize_context(raw: dict[str, Any], batch: list[SuggestionEnvelope]) -> dict[str, Any]:
    first = batch[0] if batch else None
    manifest = []
    for item in raw.get("existing_manifest") or []:
        if isinstance(item, SkillManifestEntry):
            manifest.append(item)
        elif isinstance(item, dict):
            manifest.append(SkillManifestEntry.from_dict(item))
    return {
        "cwd": raw.get("cwd") or (first.cwd if first else ""),
        "repo_fingerprint": raw.get("repo_fingerprint") or (first.repo_fingerprint if first else ""),
        "skills_dir": raw.get("skills_dir") or "",
        "memory_dir": raw.get("memory_dir") or "",
        "recall_dir": raw.get("recall_dir") or "",
        "existing_manifest": manifest,
        "existing_user_memory": raw.get("existing_user_memory") or "",
        "existing_global_memory": raw.get("existing_global_memory") or "",
        "existing_memory_index": raw.get("existing_memory_index") or {"user": [], "global": []},
        "existing_recall_records": raw.get("existing_recall_records") or [],
        "existing_recall_markdown": raw.get("existing_recall_markdown") or "",
        "memory_paths": raw.get("memory_paths") or {},
        "recall_paths": raw.get("recall_paths") or {},
    }


def _artifact_metrics(artifacts) -> dict[str, Any]:
    memory_user = artifacts.memory_records.get("user", [])
    memory_global = artifacts.memory_records.get("global", [])
    discard_reasons = Counter(
        str(item.get("reason") or "")
        for item in artifacts.discarded_items
        if str(item.get("reason") or "").strip()
    )
    return {
        "memory_records": len(memory_user) + len(memory_global),
        "memory_summaries": [item.get("summary", "") for item in [*memory_user, *memory_global]],
        "recall_records": len(artifacts.recall_records),
        "recall_summaries": [item.summary for item in artifacts.recall_records],
        "compiled_skills": len(artifacts.compiled_skills),
        "discarded_items": len(artifacts.discarded_items),
        "discard_reasons": dict(discard_reasons),
    }


def _check_expectations(metrics: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for field, metric in (
        ("min_memory_records", "memory_records"),
        ("min_recall_records", "recall_records"),
        ("min_discarded_items", "discarded_items"),
    ):
        if field in expect and metrics.get(metric, 0) < int(expect[field]):
            failures.append(f"{metric}_below_{expect[field]}")
    if "max_recall_records" in expect and metrics.get("recall_records", 0) > int(expect["max_recall_records"]):
        failures.append(f"recall_records_above_{expect['max_recall_records']}")
    for summary in expect.get("required_memory_summaries") or []:
        if summary not in metrics.get("memory_summaries", []):
            failures.append(f"missing_memory:{summary}")
    for summary in expect.get("required_recall_summaries") or []:
        if summary not in metrics.get("recall_summaries", []):
            failures.append(f"missing_recall:{summary}")
    reasons = metrics.get("discard_reasons") or {}
    for reason, minimum in (expect.get("required_discard_reasons") or {}).items():
        if int(reasons.get(reason, 0)) < int(minimum):
            failures.append(f"discard_reason:{reason}_below_{minimum}")
    return failures
