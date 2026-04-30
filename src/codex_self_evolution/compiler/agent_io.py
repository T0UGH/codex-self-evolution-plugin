from __future__ import annotations

import json
from typing import Any

from ..schemas import (
    ALLOWED_SKILL_ACTIONS,
    RecallRecord,
    SchemaError,
    SkillManifestEntry,
    SuggestionEnvelope,
)


AGENT_COMPILE_SCHEMA_VERSION = 1


COMPILE_CONTRACT = {
    "schema_version": AGENT_COMPILE_SCHEMA_VERSION,
    "goals": [
        "Merge the new suggestion batch with existing memory / recall without discarding stable entries.",
        "Dedupe memory and recall entries by content; preserve provenance when possible.",
        "Only propose skill actions consistent with the existing manifest ownership.",
        "Emit ONLY the declared response schema; do not write files directly, the writer handles final I/O.",
    ],
    "response_schema": {
        "memory_records": {
            "user": "list[MemoryRecord]",
            "global": "list[MemoryRecord]",
        },
        "recall_records": "list[RecallRecord]",
        "compiled_skills": (
            "list[{skill_id: str, title: str, description: str, "
            "content: str, action: create|patch|edit|retire}]"
        ),
        "manifest_entries": "list[SkillManifestEntry]",
        "discarded_items": "list[{reason: str, ...}]",
    },
}


class AgentResponseError(ValueError):
    """Raised when an agent compile response fails structural validation."""


def build_agent_compile_payload(
    batch: list[SuggestionEnvelope],
    context: dict[str, Any],
) -> dict[str, Any]:
    existing_manifest = context.get("existing_manifest") or []
    manifest_payload = [
        entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        for entry in existing_manifest
    ]
    memory_index = context.get("existing_memory_index") or {"user": [], "global": []}
    return {
        "schema_version": AGENT_COMPILE_SCHEMA_VERSION,
        "repo": {
            "cwd": context.get("cwd", ""),
            "repo_fingerprint": context.get("repo_fingerprint", ""),
            "skills_dir": context.get("skills_dir", ""),
            "memory_dir": context.get("memory_dir", ""),
            "recall_dir": context.get("recall_dir", ""),
        },
        "existing_assets": {
            "manifest": manifest_payload,
            "memory": {
                "user_markdown": context.get("existing_user_memory", ""),
                "global_markdown": context.get("existing_global_memory", ""),
                "index": {
                    "user": list(memory_index.get("user", [])),
                    "global": list(memory_index.get("global", [])),
                },
                "paths": dict(context.get("memory_paths", {})),
            },
            "recall": {
                "records": list(context.get("existing_recall_records", [])),
                "compiled_markdown": context.get("existing_recall_markdown", ""),
                "paths": dict(context.get("recall_paths", {})),
            },
        },
        "batch": [envelope.to_dict() for envelope in batch],
        "contract": COMPILE_CONTRACT,
    }


def parse_agent_compile_response(raw: Any) -> dict[str, Any]:
    """Normalize an agent response into compile artifact inputs.

    Returns a dict with keys ``memory_records``, ``recall_records``,
    ``compiled_skills``, ``manifest_entries``, ``discarded_items``.
    Raises :class:`AgentResponseError` on any structural problem so the caller
    can fall back deterministically.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            raise AgentResponseError("agent output is empty")
        try:
            data = json.loads(stripped)
        except ValueError as exc:
            raise AgentResponseError(f"agent output is not valid JSON: {exc}") from exc
    else:
        data = raw

    if not isinstance(data, dict):
        raise AgentResponseError("agent output must be a JSON object")

    return {
        "memory_records": _parse_memory_records(data.get("memory_records")),
        "recall_records": _parse_recall_records(data.get("recall_records")),
        "compiled_skills": _parse_compiled_skills(data.get("compiled_skills")),
        "manifest_entries": _parse_manifest_entries(data.get("manifest_entries")),
        "discarded_items": _parse_discarded_items(data.get("discarded_items", [])),
    }


def _parse_memory_records(value: Any) -> dict[str, list[dict[str, Any]]]:
    if value is None:
        return {"user": [], "global": []}
    if not isinstance(value, dict):
        raise AgentResponseError("memory_records must be an object")
    out: dict[str, list[dict[str, Any]]] = {"user": [], "global": []}
    for scope in ("user", "global"):
        items = value.get(scope, [])
        if not isinstance(items, list):
            raise AgentResponseError(f"memory_records.{scope} must be a list")
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise AgentResponseError(f"memory_records.{scope} entries must be objects")
            summary = str(item.get("summary", "")).strip()
            content = str(item.get("content", "")).strip()
            if not summary or not content:
                raise AgentResponseError(
                    f"memory_records.{scope} entries require non-empty summary and content"
                )
            normalized.append(
                {
                    "scope": scope,
                    "summary": summary,
                    "content": content,
                    "source_paths": [str(path) for path in item.get("source_paths", [])],
                    "confidence": float(item.get("confidence", 1.0)),
                    "provenance": list(item.get("provenance", [])),
                }
            )
        out[scope] = normalized
    return out


def _parse_recall_records(value: Any) -> list[RecallRecord]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentResponseError("recall_records must be a list")
    records: list[RecallRecord] = []
    for item in value:
        if not isinstance(item, dict):
            raise AgentResponseError("recall_records entries must be objects")
        try:
            records.append(RecallRecord.from_dict(item))
        except SchemaError as exc:
            raise AgentResponseError(f"invalid recall_record: {exc}") from exc
    return records


def _parse_compiled_skills(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentResponseError("compiled_skills must be a list")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise AgentResponseError("compiled_skills entries must be objects")
        action = str(item.get("action", "")).strip()
        if action not in ALLOWED_SKILL_ACTIONS:
            raise AgentResponseError(f"invalid skill action: {action!r}")
        skill_id = str(item.get("skill_id", "")).strip()
        title = str(item.get("title", "")).strip()
        if not skill_id or not title:
            raise AgentResponseError("compiled_skills entries require skill_id and title")
        description = str(item.get("description", "")).strip()
        if action != "retire" and not description:
            raise AgentResponseError(
                "compiled_skills create/patch/edit entries require description"
            )
        out.append(
            {
                "skill_id": skill_id,
                "title": title,
                "description": description,
                "content": str(item.get("content", "")),
                "action": action,
            }
        )
    return out


def _parse_manifest_entries(value: Any) -> list[SkillManifestEntry]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentResponseError("manifest_entries must be a list")
    entries: list[SkillManifestEntry] = []
    for item in value:
        if not isinstance(item, dict):
            raise AgentResponseError("manifest_entries entries must be objects")
        try:
            entries.append(SkillManifestEntry.from_dict(item))
        except SchemaError as exc:
            raise AgentResponseError(f"invalid manifest_entry: {exc}") from exc
    return entries


def _parse_discarded_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AgentResponseError("discarded_items must be a list")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise AgentResponseError("discarded_items entries must be objects")
        out.append(dict(item))
    return out
