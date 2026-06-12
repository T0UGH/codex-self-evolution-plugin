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
    item = _canonical_item(source, items.get(item_key))
    items[item_key] = item

    now = injected_at or utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    item["kind"] = _kind_for_source(source)
    item["injected_count"] = _safe_count(item.get("injected_count")) + 1
    item["last_injected_at"] = now
    atomic_write_json(usage_path, usage)
    return {"status": "recorded", "path": str(usage_path), "item_key": item_key, "item": item}


def _load_usage(usage_path: Path) -> dict[str, Any]:
    """Load usage state, resetting malformed or missing files."""
    try:
        usage = load_json(usage_path)
    except (OSError, ValueError):
        return {"schema_version": USAGE_SCHEMA_VERSION, "items": {}}
    if not isinstance(usage, dict) or usage.get("schema_version") != USAGE_SCHEMA_VERSION:
        return {"schema_version": USAGE_SCHEMA_VERSION, "items": {}}
    if not isinstance(usage.get("items"), dict):
        usage["items"] = {}
    return usage


def _empty_item(source: str) -> dict[str, Any]:
    """Return a blank per-source usage record without source content."""
    return {
        "kind": _kind_for_source(source),
        "injected_count": 0,
        "last_injected_at": "",
        "citation_count": 0,
        "last_cited_at": "",
    }


def _canonical_item(source: str, raw: object) -> dict[str, Any]:
    """Return an allowlisted usage item, dropping any stored content fields."""
    if not isinstance(raw, dict):
        return _empty_item(source)
    last_injected_at = raw.get("last_injected_at") if isinstance(raw.get("last_injected_at"), str) else ""
    last_cited_at = raw.get("last_cited_at") if isinstance(raw.get("last_cited_at"), str) else ""
    return {
        "kind": _kind_for_source(source),
        "injected_count": _safe_count(raw.get("injected_count")),
        "last_injected_at": last_injected_at,
        "citation_count": _safe_count(raw.get("citation_count")),
        "last_cited_at": last_cited_at,
    }


def _safe_count(value: object) -> int:
    """Parse non-negative integer counters while rejecting bools and malformed values."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _kind_for_source(source: str) -> str:
    """Classify stable-memory source files for usage reporting."""
    return "memory_summary" if source == "memory_summary.md" else "memory"


def _usage_item_key(*, memory_dir: Path, source: str, source_path: Path) -> str:
    """Return the usage key relative to the memory directory when possible."""
    try:
        return source_path.relative_to(memory_dir).as_posix()
    except ValueError:
        return source
