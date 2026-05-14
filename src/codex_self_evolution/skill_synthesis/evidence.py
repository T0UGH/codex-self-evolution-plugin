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
        records = raw.get(scope, [])
        if not isinstance(records, list):
            continue
        for item in records:
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
    atomic_write_text(paths.run_input_dir / "README.md", _render_readme(len(evidence)))
    return manifest


def _render_readme(count: int) -> str:
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


def update_evidence_index(
    path: Path,
    index: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    run_id: str,
    outcome: str,
) -> None:
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
