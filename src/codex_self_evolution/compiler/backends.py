from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..managed_skills.manifest import load_manifest
from ..schemas import SkillManifestEntry, SuggestionEnvelope
from ..storage import read_text_if_exists
from .agent_io import (
    AgentResponseError,
    build_agent_compile_payload,
    parse_agent_compile_response,
)
from .memory import compile_memory
from .recall import compile_recall
from .skills import build_manifest_entries, compile_skills


AgentInvoker = Callable[[dict[str, Any], dict[str, Any]], Any]


def _load_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_existing_memory(memory_dir: Path) -> dict[str, Any]:
    index_path = memory_dir / "memory.json"
    user_path = memory_dir / "USER.md"
    global_path = memory_dir / "MEMORY.md"
    raw_index = _load_json_if_exists(index_path)
    if isinstance(raw_index, dict):
        user_records = raw_index.get("user") if isinstance(raw_index.get("user"), list) else []
        global_records = raw_index.get("global") if isinstance(raw_index.get("global"), list) else []
    else:
        user_records = []
        global_records = []
    return {
        "user_markdown": read_text_if_exists(user_path),
        "global_markdown": read_text_if_exists(global_path),
        "index": {"user": list(user_records), "global": list(global_records)},
        "paths": {
            "user": str(user_path),
            "global": str(global_path),
            "index": str(index_path),
        },
    }


def _load_existing_recall(recall_dir: Path) -> dict[str, Any]:
    index_path = recall_dir / "index.json"
    compiled_path = recall_dir / "compiled.md"
    raw_index = _load_json_if_exists(index_path)
    records: list[dict[str, Any]] = []
    if isinstance(raw_index, dict):
        maybe_records = raw_index.get("records")
        if isinstance(maybe_records, list):
            records = [item for item in maybe_records if isinstance(item, dict)]
    return {
        "records": records,
        "compiled_markdown": read_text_if_exists(compiled_path),
        "paths": {
            "index": str(index_path),
            "compiled": str(compiled_path),
        },
    }


@dataclass(frozen=True)
class CompileArtifacts:
    memory_records: dict[str, list[dict]]
    recall_records: list[Any]
    compiled_skills: list[dict]
    manifest_entries: list[SkillManifestEntry]
    discarded_items: list[dict[str, Any]]
    backend_name: str
    fallback_backend: str | None = None


class CompilerBackend(Protocol):
    name: str

    def compile(self, batch: list[SuggestionEnvelope], context: dict[str, Any], options: dict[str, Any]) -> CompileArtifacts: ...


class ScriptCompilerBackend:
    name = "script"

    def compile(self, batch: list[SuggestionEnvelope], context: dict[str, Any], options: dict[str, Any]) -> CompileArtifacts:
        all_suggestions = [item for envelope in batch for item in envelope.suggestions]
        existing_manifest = context["existing_manifest"]
        memory_records = compile_memory(
            all_suggestions,
            existing_index=context.get("existing_memory_index"),
        )
        recall_records = compile_recall(
            all_suggestions,
            repo_fingerprint=context["repo_fingerprint"],
            cwd=context["cwd"],
            thread_id=batch[0].thread_id if batch else "",
            existing_records=context.get("existing_recall_records"),
        )
        compiled_skills, discarded_items = compile_skills(all_suggestions, existing_entries=existing_manifest)
        manifest_entries = build_manifest_entries(compiled_skills, context["skills_dir"], existing_entries=existing_manifest)
        return CompileArtifacts(
            memory_records=memory_records,
            recall_records=recall_records,
            compiled_skills=compiled_skills,
            manifest_entries=manifest_entries,
            discarded_items=discarded_items,
            backend_name=self.name,
        )


class AgentCompilerBackend:
    name = "agent:opencode"

    DEFAULT_TIMEOUT_SECONDS = 120

    def __init__(self, invoker: AgentInvoker | None = None) -> None:
        self._invoker = invoker

    def compile(self, batch: list[SuggestionEnvelope], context: dict[str, Any], options: dict[str, Any]) -> CompileArtifacts:
        payload = build_agent_compile_payload(batch, context)
        if self._invoker is None and shutil.which("opencode") is None:
            return self._fallback(batch, context, options, reason="opencode_unavailable")
        invoker = self._invoker or self._subprocess_invoker
        try:
            raw = invoker(payload, options)
        except Exception as exc:
            return self._fallback(
                batch,
                context,
                options,
                reason="agent_invoke_failed",
                detail=_truncate(str(exc)),
            )
        try:
            parsed = parse_agent_compile_response(raw)
        except AgentResponseError as exc:
            return self._fallback(
                batch,
                context,
                options,
                reason="agent_output_invalid",
                detail=_truncate(str(exc)),
            )
        return CompileArtifacts(
            memory_records=parsed["memory_records"],
            recall_records=parsed["recall_records"],
            compiled_skills=parsed["compiled_skills"],
            manifest_entries=parsed["manifest_entries"],
            discarded_items=parsed["discarded_items"],
            backend_name=self.name,
        )

    def _fallback(
        self,
        batch: list[SuggestionEnvelope],
        context: dict[str, Any],
        options: dict[str, Any],
        *,
        reason: str,
        detail: str | None = None,
    ) -> CompileArtifacts:
        if not options.get("allow_fallback", True):
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"{self.name} failed ({reason}){suffix}")
        fallback = ScriptCompilerBackend().compile(batch, context, options)
        entry: dict[str, Any] = {"reason": reason, "backend": self.name}
        if detail:
            entry["detail"] = detail
        return CompileArtifacts(
            memory_records=fallback.memory_records,
            recall_records=fallback.recall_records,
            compiled_skills=fallback.compiled_skills,
            manifest_entries=fallback.manifest_entries,
            discarded_items=[*fallback.discarded_items, entry],
            backend_name=self.name,
            fallback_backend="script",
        )

    def _subprocess_invoker(self, payload: dict[str, Any], options: dict[str, Any]) -> str:
        command = (
            options.get("opencode_command")
            or _command_from_env()
            or ["opencode", "run", "--stdin-json", "--stdout-json"]
        )
        timeout = float(options.get("opencode_timeout_seconds", self.DEFAULT_TIMEOUT_SECONDS))
        proc = subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode exit={proc.returncode}; stderr={proc.stderr.strip()[:400]}"
            )
        return proc.stdout


def _command_from_env() -> list[str] | None:
    raw = os.environ.get("CODEX_SELF_EVOLUTION_OPENCODE_COMMAND")
    if not raw:
        return None
    parts = [part for part in raw.split() if part]
    return parts or None


def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def get_backend(name: str) -> CompilerBackend:
    if name == "script":
        return ScriptCompilerBackend()
    if name == "agent:opencode":
        return AgentCompilerBackend()
    raise ValueError(f"unknown compiler backend: {name}")


def build_compile_context(paths, batch: list[SuggestionEnvelope]) -> dict[str, Any]:
    existing_manifest = load_manifest(paths.skills_dir / "manifest.json")
    existing_memory = _load_existing_memory(paths.memory_dir)
    existing_recall = _load_existing_recall(paths.recall_dir)
    first = batch[0] if batch else None
    return {
        "cwd": first.cwd if first else str(paths.repo_root),
        "repo_fingerprint": first.repo_fingerprint if first else "",
        "skills_dir": str(paths.skills_dir),
        "memory_dir": str(paths.memory_dir),
        "recall_dir": str(paths.recall_dir),
        "existing_manifest": existing_manifest,
        "existing_user_memory": existing_memory["user_markdown"],
        "existing_global_memory": existing_memory["global_markdown"],
        "existing_memory_index": existing_memory["index"],
        "existing_recall_records": existing_recall["records"],
        "existing_recall_markdown": existing_recall["compiled_markdown"],
        "memory_paths": existing_memory["paths"],
        "recall_paths": existing_recall["paths"],
    }
