from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any, Protocol

from ..managed_skills.manifest import load_manifest
from ..schemas import SkillManifestEntry, SuggestionEnvelope
from .memory import compile_memory
from .recall import compile_recall
from .skills import build_manifest_entries, compile_skills


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
        memory_records = compile_memory(all_suggestions)
        recall_records = compile_recall(
            all_suggestions,
            repo_fingerprint=context["repo_fingerprint"],
            cwd=context["cwd"],
            thread_id=batch[0].thread_id if batch else "",
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

    def compile(self, batch: list[SuggestionEnvelope], context: dict[str, Any], options: dict[str, Any]) -> CompileArtifacts:
        if shutil.which("opencode") is None:
            if options.get("allow_fallback", True):
                fallback = ScriptCompilerBackend().compile(batch, context, options)
                return CompileArtifacts(
                    memory_records=fallback.memory_records,
                    recall_records=fallback.recall_records,
                    compiled_skills=fallback.compiled_skills,
                    manifest_entries=fallback.manifest_entries,
                    discarded_items=[*fallback.discarded_items, {"reason": "opencode_unavailable", "backend": self.name}],
                    backend_name=self.name,
                    fallback_backend="script",
                )
            raise RuntimeError("opencode backend unavailable")
        fallback = ScriptCompilerBackend().compile(batch, context, options)
        return CompileArtifacts(
            memory_records=fallback.memory_records,
            recall_records=fallback.recall_records,
            compiled_skills=fallback.compiled_skills,
            manifest_entries=fallback.manifest_entries,
            discarded_items=[*fallback.discarded_items, {"reason": "agent_backend_scaffold_only", "backend": self.name}],
            backend_name=self.name,
            fallback_backend="script",
        )


def get_backend(name: str) -> CompilerBackend:
    if name == "script":
        return ScriptCompilerBackend()
    if name == "agent:opencode":
        return AgentCompilerBackend()
    raise ValueError(f"unknown compiler backend: {name}")


def build_compile_context(paths, batch: list[SuggestionEnvelope]) -> dict[str, Any]:
    existing_manifest = load_manifest(paths.skills_dir / "manifest.json")
    first = batch[0] if batch else None
    return {
        "cwd": first.cwd if first else str(paths.repo_root),
        "repo_fingerprint": first.repo_fingerprint if first else "",
        "skills_dir": str(paths.skills_dir),
        "existing_manifest": existing_manifest,
    }
