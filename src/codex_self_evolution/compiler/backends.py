from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..managed_skills.manifest import dump_manifest, load_manifest
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


class AgentCompileError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail or ""
        suffix = f": {self.detail}" if self.detail else ""
        super().__init__(f"{reason}{suffix}")


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
    executable = "opencode"
    timeout_option = "opencode_timeout_seconds"

    # Upper bound for the opencode subprocess. Kept strictly below the 30-minute
    # compile-lock hard limit so a hung agent times out, yields in finally, and
    # releases the lock before the next preflight evicts it.
    DEFAULT_TIMEOUT_SECONDS = 15 * 60
    DEFAULT_QUALITY_RETRIES = 1

    def __init__(self, invoker: AgentInvoker | None = None) -> None:
        self._invoker = invoker

    def compile(self, batch: list[SuggestionEnvelope], context: dict[str, Any], options: dict[str, Any]) -> CompileArtifacts:
        payload = build_agent_compile_payload(batch, context)
        if self._invoker is None and shutil.which(self.executable) is None:
            raise AgentCompileError(f"{self.executable}_unavailable")
        invoker = self._invoker or self._subprocess_invoker
        retries = int(options.get("agent_quality_retries", self.DEFAULT_QUALITY_RETRIES))
        last_reason = "agent_failed"
        last_detail = ""
        for attempt in range(max(0, retries) + 1):
            attempt_payload = dict(payload)
            if attempt > 0:
                attempt_payload["retry_feedback"] = {
                    "reason": last_reason,
                    "detail": last_detail,
                    "instruction": (
                        "Your previous compiler response was rejected. "
                        "Re-read the batch and existing_assets, preserve stable existing assets, "
                        "emit useful memory/recall/skills, or explain every discarded suggestion "
                        "in discarded_items. Do not return a silent empty artifact set."
                    ),
                }
            try:
                raw = invoker(attempt_payload, options)
            except Exception as exc:
                last_reason = "agent_invoke_failed"
                last_detail = _truncate(str(exc))
                continue
            try:
                parsed = parse_agent_compile_response(raw)
            except AgentResponseError as exc:
                last_reason = "agent_output_invalid"
                last_detail = _truncate(str(exc))
                continue
            empty_output_reason = _agent_empty_output_reason(batch, context, parsed)
            if empty_output_reason:
                last_reason = empty_output_reason
                last_detail = _agent_quality_detail(batch, context, parsed)
                continue
            return CompileArtifacts(
                memory_records=parsed["memory_records"],
                recall_records=parsed["recall_records"],
                compiled_skills=parsed["compiled_skills"],
                manifest_entries=parsed["manifest_entries"],
                discarded_items=parsed["discarded_items"],
                backend_name=self.name,
            )
        raise AgentCompileError(last_reason, last_detail)

    def _subprocess_invoker(self, payload: dict[str, Any], options: dict[str, Any]) -> str:
        # opencode 1.4.0's `run` takes the message as a positional argument and
        # attaches files via `--file`, so we cannot pipe payload on stdin.
        # Writing the JSON payload to a temp file and attaching it keeps us
        # clear of argv size limits (a full batch plus existing_assets can
        # easily blow past typical MAX_ARG_STRLEN).
        payload_path = _write_payload_tempfile(payload)
        try:
            command = (
                options.get(f"{self.executable}_command")
                or self._command_from_env()
                or self._build_default_command(payload_path, options)
            )
            timeout = float(options.get(self.timeout_option, self.DEFAULT_TIMEOUT_SECONDS))
            proc = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"{self.executable} exit={proc.returncode}; stderr={proc.stderr.strip()[:400]}"
                )
            # `--format json` emits one JSON event per line plus a trailing
            # "Shell cwd was reset to ..." noise line. We concatenate the
            # assistant's `text` parts and strip any code fence / prose the
            # model might still wrap around the JSON payload.
            assistant_text = self._extract_assistant_text(proc.stdout)
            if not assistant_text:
                raise RuntimeError(
                    f"{self.executable} produced no assistant text; "
                    f"stderr={proc.stderr.strip()[:400]}"
                )
            return _cleanup_agent_text(assistant_text)
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass

    def _command_from_env(self) -> list[str] | None:
        return _command_from_env("CODEX_SELF_EVOLUTION_OPENCODE_COMMAND")

    def _build_default_command(self, payload_path: str, options: dict[str, Any]) -> list[str]:
        return _build_default_opencode_command(payload_path, options)

    def _extract_assistant_text(self, stdout: str) -> str:
        return _extract_assistant_text(stdout)


class PiAgentCompilerBackend(AgentCompilerBackend):
    name = "agent:pi"
    executable = "pi"
    timeout_option = "pi_timeout_seconds"

    def compile(self, batch: list[SuggestionEnvelope], context: dict[str, Any], options: dict[str, Any]) -> CompileArtifacts:
        if _pi_compile_mode(options) == "edit":
            return self._compile_with_edit_workspace(batch, context, options)
        return super().compile(batch, context, options)

    def _command_from_env(self) -> list[str] | None:
        return _command_from_env("CODEX_SELF_EVOLUTION_PI_COMMAND")

    def _build_default_command(self, payload_path: str, options: dict[str, Any]) -> list[str]:
        return _build_default_pi_command(payload_path, options)

    def _extract_assistant_text(self, stdout: str) -> str:
        return _extract_pi_assistant_text(stdout)

    def _compile_with_edit_workspace(
        self,
        batch: list[SuggestionEnvelope],
        context: dict[str, Any],
        options: dict[str, Any],
    ) -> CompileArtifacts:
        if self._invoker is None and shutil.which(self.executable) is None:
            raise AgentCompileError(f"{self.executable}_unavailable")
        invoker = self._invoker or self._subprocess_edit_invoker
        base_payload = build_agent_compile_payload(batch, context)
        retries = int(options.get("agent_quality_retries", self.DEFAULT_QUALITY_RETRIES))
        last_reason = "agent_failed"
        last_detail = ""
        for attempt in range(max(0, retries) + 1):
            payload = dict(base_payload)
            if attempt > 0:
                payload["retry_feedback"] = {
                    "reason": last_reason,
                    "detail": last_detail,
                    "instruction": (
                        "Your previous direct-edit compiler run was rejected. "
                        "Start from the fresh workspace, edit only the provided asset files, "
                        "preserve existing stable assets, and write valid JSON indexes."
                    ),
                }
            with tempfile.TemporaryDirectory(prefix="csep-pi-edit-") as workspace_raw:
                workspace = Path(workspace_raw)
                payload_path = _prepare_pi_edit_workspace(workspace, payload, context)
                edit_payload = {
                    "mode": "edit",
                    "workspace_dir": str(workspace),
                    "payload_path": str(payload_path),
                    "assets_dir": str(workspace / "assets"),
                    "result_path": str(workspace / "compiler" / "result.json"),
                }
                try:
                    invoker(edit_payload, options)
                except Exception as exc:
                    last_reason = "agent_invoke_failed"
                    last_detail = _truncate(str(exc))
                    continue
                try:
                    parsed = _load_pi_edit_workspace_artifacts(workspace)
                except AgentResponseError as exc:
                    last_reason = "agent_output_invalid"
                    last_detail = _truncate(str(exc))
                    continue
                empty_output_reason = _agent_empty_output_reason(batch, context, parsed)
                if empty_output_reason:
                    last_reason = empty_output_reason
                    last_detail = _agent_quality_detail(batch, context, parsed)
                    continue
                return CompileArtifacts(
                    memory_records=parsed["memory_records"],
                    recall_records=parsed["recall_records"],
                    compiled_skills=parsed["compiled_skills"],
                    manifest_entries=parsed["manifest_entries"],
                    discarded_items=parsed["discarded_items"],
                    backend_name=self.name,
                )
        raise AgentCompileError(last_reason, last_detail)

    def _subprocess_edit_invoker(self, payload: dict[str, Any], options: dict[str, Any]) -> str:
        command = (
            options.get("pi_edit_command")
            or _command_from_env("CODEX_SELF_EVOLUTION_PI_EDIT_COMMAND")
            or _build_default_pi_edit_command(payload["workspace_dir"], payload["payload_path"], options)
        )
        timeout = float(options.get(self.timeout_option, self.DEFAULT_TIMEOUT_SECONDS))
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pi exit={proc.returncode}; stderr={proc.stderr.strip()[:400]}")
        if not proc.stdout.strip():
            return ""
        return _extract_pi_assistant_text(proc.stdout)


def _command_from_env(env_var: str) -> list[str] | None:
    raw = os.environ.get(env_var)
    if not raw:
        return None
    parts = [part for part in raw.split() if part]
    return parts or None


def _write_payload_tempfile(payload: dict[str, Any]) -> str:
    # `delete=False` because we unlink in a `finally` after opencode finishes;
    # leaving open+unlink would give opencode a dangling filename.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="csep-compile-",
        delete=False,
        encoding="utf-8",
    ) as fh:
        json.dump(payload, fh, ensure_ascii=False)
        return fh.name


def _build_default_opencode_command(payload_path: str, options: dict[str, Any]) -> list[str]:
    cmd: list[str] = ["opencode", "run", "--format", "json", "--file", payload_path]
    # The compile agent needs file-read tools to inspect the payload. Without
    # skip-permissions the TUI prompts on every tool use, which is fatal for
    # a headless subprocess invocation.
    if options.get("opencode_skip_permissions", True):
        cmd.append("--dangerously-skip-permissions")
    model = options.get("opencode_model") or os.environ.get("CODEX_SELF_EVOLUTION_OPENCODE_MODEL")
    if model:
        cmd.extend(["--model", model])
    agent = options.get("opencode_agent") or os.environ.get("CODEX_SELF_EVOLUTION_OPENCODE_AGENT")
    if agent:
        cmd.extend(["--agent", agent])
    # `--` ends opencode's flag parsing so the prompt (which may contain
    # leading dashes, quotes, or braces) is passed through unchanged.
    cmd.append("--")
    cmd.append(_build_compile_prompt(payload_path))
    return cmd


def _build_default_pi_command(payload_path: str, options: dict[str, Any]) -> list[str]:
    cmd: list[str] = [
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
        "read",
    ]
    provider = options.get("pi_provider") or os.environ.get("CODEX_SELF_EVOLUTION_PI_PROVIDER") or "kimi"
    if provider:
        cmd.extend(["--provider", str(provider)])
    model = options.get("pi_model") or os.environ.get("CODEX_SELF_EVOLUTION_PI_MODEL") or "kimi-k2.6"
    if model:
        cmd.extend(["--model", str(model)])
    cmd.append(_build_compile_prompt(payload_path))
    return cmd


def _build_default_pi_edit_command(workspace_dir: str, payload_path: str, options: dict[str, Any]) -> list[str]:
    cmd: list[str] = [
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
    provider = options.get("pi_provider") or os.environ.get("CODEX_SELF_EVOLUTION_PI_PROVIDER") or "kimi"
    if provider:
        cmd.extend(["--provider", str(provider)])
    model = options.get("pi_model") or os.environ.get("CODEX_SELF_EVOLUTION_PI_MODEL") or "kimi-k2.6"
    if model:
        cmd.extend(["--model", str(model)])
    cmd.append(_build_edit_compile_prompt(workspace_dir, payload_path))
    return cmd


def _build_compile_prompt(payload_path: str) -> str:
    # Kept inline (not a separate file) so the contract travels with the code
    # that depends on it. If you update this prompt, also update
    # parse_agent_compile_response in agent_io.py — they are two halves of
    # the same wire protocol.
    return (
        f"The JSON file at {payload_path} is a compile payload from "
        "the codex-self-evolution-plugin reviewer pipeline. Your job is to "
        "merge its `batch` into its `existing_assets` and emit the merged "
        "artifacts. Read the full file from disk with your file-reading tools "
        "before deciding; any attachment preview may be truncated and must not "
        "be treated as the complete payload.\n\n"
        "Rules:\n"
        "1. Preserve existing assets unless an explicit valid remove/retire "
        "request applies. The writer replaces files with your output, so "
        "dropping an existing stable asset means deleting it.\n"
        "2. Dedupe memory entries by content+summary; preserve existing "
        "provenance where possible. Memory is for durable preferences, "
        "architecture boundaries, repo conventions, and toolchain pitfalls.\n"
        "3. Treat `memory_updates` as pre-filtered reviewer signal, not raw "
        "chat. If the content states a reusable boundary, preference, "
        "repository convention, or toolchain pitfall, preserve it as memory "
        "even when it came from MR review or has an empty source_paths list; "
        "the envelope provenance is still evidence. Do not discard concrete "
        "memory_updates solely because they mention an MR, review, or lack "
        "file paths.\n"
        "4. Recall is future reusable context, not a transcript index. Keep "
        "a recall item only when it names a future trigger, a reusable lesson "
        "or diagnostic path, and evidence such as source/document paths.\n"
        "5. Discard MR status, commit status, temporary refactor progress, "
        "one-off review discussion, and branch-local task state unless the "
        "item includes a reusable rule that will help a future task.\n"
        "6. For every suggestion you reject, add a discarded_items entry with "
        "a concrete reason such as task_state_noise, duplicate, "
        "missing_reuse_trigger, or weak_evidence.\n"
        "7. Only emit skill actions (create|patch|edit|retire) that are "
        "consistent with existing manifest ownership (managed=true entries "
        "only).\n"
        "8. Do NOT write files yourself. The writer handles final I/O.\n\n"
        "If the payload contains `retry_feedback`, your previous response was "
        "rejected by local quality gates. Fix the stated issue in the next "
        "response; do not return a silent empty artifact set. For a non-empty "
        "batch, a response with only discarded_items and no surviving "
        "memory/recall/skill artifacts is also rejected; salvage durable signal "
        "into an artifact whenever the reviewer supplied reusable content.\n\n"
        "Respond with ONE JSON object and NOTHING else — no prose, no code "
        "fence, no comments. The object MUST match this schema:\n"
        "{\n"
        '  "memory_records": {\n'
        '    "user":   [ {"summary": str, "content": str, "source_paths": [str], "confidence": float, "provenance": [...]} ],\n'
        '    "global": [ ... same shape ... ]\n'
        "  },\n"
        '  "recall_records": [\n'
        '    {"id": str, "summary": str, "content": str, "source_paths": [str], "repo_fingerprint": str, "cwd": str, "thread_id": str, "turn_id": str, "source_updated_at": str}\n'
        "  ],\n"
        '  "compiled_skills": [\n'
        '    {"skill_id": str, "title": str, "description": str, "content": str, "action": "create"|"patch"|"edit"|"retire"}\n'
        "  ],\n"
        '  "manifest_entries": [\n'
        '    {"skill_id": str, "action": str, "title": str, "path": str, "status": str, "owner": str, "managed": bool, "created_by": str, "updated_at": str, "retired_at": str|null}\n'
        "  ],\n"
        '  "discarded_items": [ {"reason": str, ...} ]\n'
        "}\n\n"
        "Required string fields (summary, content, id, etc.) must be "
        "non-empty. If a list has nothing to emit, return [] — do not fabricate "
        "entries just to fill it. Emit empty objects "
        '({"user": [], "global": []}) for memory_records when nothing merges. '
        "compiled_skills.description is required for create, patch, and edit; "
        "retire may use an empty description."
    )


def _build_edit_compile_prompt(workspace_dir: str, payload_path: str) -> str:
    assets_dir = str(Path(workspace_dir) / "assets")
    result_path = str(Path(workspace_dir) / "compiler" / "result.json")
    return (
        f"The JSON file at {payload_path} is a compile payload from "
        "the codex-self-evolution-plugin reviewer pipeline. Read it fully from disk. "
        f"Then directly edit the asset files under {assets_dir}. Do not edit files "
        "outside this workspace.\n\n"
        "Authoritative files you may edit:\n"
        f"- {assets_dir}/memory/memory.json, plus USER.md and MEMORY.md for readability\n"
        f"- {assets_dir}/recall/index.json, plus compiled.md for readability\n"
        f"- {assets_dir}/skills/manifest.json and assets/skills/managed/*.md when skill changes are needed\n"
        f"- {result_path} for discarded_items and optional compiled_skills/manifest_entries metadata\n\n"
        "Rules:\n"
        "1. Preserve existing stable memory/recall/skills unless the payload has an explicit valid removal.\n"
        "2. Keep reusable lessons and durable preferences; discard one-off MR status or temporary process state.\n"
        "3. Keep all JSON files valid. Required string fields must be non-empty.\n"
        "4. Write discarded suggestions to result.json as {\"discarded_items\": [...]} with concrete reasons.\n"
        "5. Do not return the full artifacts in your chat response. After editing files, respond exactly DONE.\n\n"
        "The local compiler will validate and promote your edited workspace."
    )


def _pi_compile_mode(options: dict[str, Any]) -> str:
    mode = str(options.get("pi_mode") or os.environ.get("CODEX_SELF_EVOLUTION_PI_MODE") or "edit").strip().lower()
    if mode not in {"edit", "json"}:
        raise AgentCompileError("invalid_pi_mode", mode)
    return mode


def _prepare_pi_edit_workspace(workspace: Path, payload: dict[str, Any], context: dict[str, Any]) -> Path:
    assets_dir = workspace / "assets"
    memory_dir = assets_dir / "memory"
    recall_dir = assets_dir / "recall"
    skills_dir = assets_dir / "skills"
    compiler_dir = workspace / "compiler"
    for directory in (memory_dir, recall_dir, skills_dir, compiler_dir):
        directory.mkdir(parents=True, exist_ok=True)

    memory_index = context.get("existing_memory_index") or {"user": [], "global": []}
    user_records = list(memory_index.get("user", []))
    global_records = list(memory_index.get("global", []))
    _write_json(memory_dir / "memory.json", {"user": user_records, "global": global_records})
    (memory_dir / "USER.md").write_text(
        context.get("existing_user_memory") or _render_memory_workspace_markdown("USER", user_records),
        encoding="utf-8",
    )
    (memory_dir / "MEMORY.md").write_text(
        context.get("existing_global_memory") or _render_memory_workspace_markdown("MEMORY", global_records),
        encoding="utf-8",
    )

    recall_records = list(context.get("existing_recall_records") or [])
    _write_json(recall_dir / "index.json", {"records": recall_records})
    (recall_dir / "compiled.md").write_text(
        context.get("existing_recall_markdown") or _render_recall_workspace_markdown(recall_records),
        encoding="utf-8",
    )

    manifest_entries = list(context.get("existing_manifest") or [])
    _write_json(skills_dir / "manifest.json", dump_manifest(manifest_entries))
    managed_source = Path(str(context.get("skills_dir") or "")) / "managed"
    managed_target = skills_dir / "managed"
    if managed_source.exists() and managed_source.is_dir():
        shutil.copytree(managed_source, managed_target, dirs_exist_ok=True)
    else:
        managed_target.mkdir(parents=True, exist_ok=True)

    _write_json(compiler_dir / "result.json", {"discarded_items": []})

    payload_for_workspace = json.loads(json.dumps(payload, ensure_ascii=False))
    payload_for_workspace.setdefault("repo", {})
    payload_for_workspace["repo"]["memory_dir"] = str(memory_dir)
    payload_for_workspace["repo"]["recall_dir"] = str(recall_dir)
    payload_for_workspace["repo"]["skills_dir"] = str(skills_dir)
    payload_for_workspace.setdefault("existing_assets", {}).setdefault("memory", {})["paths"] = {
        "user": str(memory_dir / "USER.md"),
        "global": str(memory_dir / "MEMORY.md"),
        "index": str(memory_dir / "memory.json"),
    }
    payload_for_workspace.setdefault("existing_assets", {}).setdefault("recall", {})["paths"] = {
        "index": str(recall_dir / "index.json"),
        "compiled": str(recall_dir / "compiled.md"),
    }
    payload_path = workspace / "payload.json"
    _write_json(payload_path, payload_for_workspace)
    return payload_path


def _load_pi_edit_workspace_artifacts(workspace: Path) -> dict[str, Any]:
    assets_dir = workspace / "assets"
    result_path = workspace / "compiler" / "result.json"
    memory_raw = _load_required_json(assets_dir / "memory" / "memory.json", "memory/memory.json")
    recall_raw = _load_required_json(assets_dir / "recall" / "index.json", "recall/index.json")
    if not isinstance(recall_raw, dict):
        raise AgentResponseError("recall/index.json must be an object")
    result_raw = _load_json_if_exists(result_path)
    if result_raw is None:
        result_raw = {"discarded_items": []}
    if not isinstance(result_raw, dict):
        raise AgentResponseError("compiler/result.json must be an object")
    manifest_entries = [
        entry.to_dict()
        for entry in load_manifest(assets_dir / "skills" / "manifest.json")
    ]
    response = {
        "memory_records": memory_raw,
        "recall_records": recall_raw.get("records", []),
        "compiled_skills": result_raw.get("compiled_skills", []),
        "manifest_entries": result_raw.get("manifest_entries", manifest_entries),
        "discarded_items": result_raw.get("discarded_items", []),
    }
    return parse_agent_compile_response(response)


def _load_required_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AgentResponseError(f"{label} is missing") from exc
    except ValueError as exc:
        raise AgentResponseError(f"{label} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise AgentResponseError(f"{label} could not be read: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _render_memory_workspace_markdown(title: str, records: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    if not records:
        lines.extend(["_No entries yet._", ""])
        return "\n".join(lines)
    for item in records:
        lines.extend([f"## {item.get('summary', '')}", "", str(item.get("content", "")), ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_recall_workspace_markdown(records: list[dict[str, Any]]) -> str:
    lines = ["# Compiled Recall", ""]
    if not records:
        return "\n".join(lines).rstrip() + "\n"
    for item in records:
        lines.extend(
            [
                f"## {item.get('summary', '')}",
                "",
                str(item.get("content", "")),
                "",
                f"Provenance: {', '.join(str(path) for path in item.get('source_paths', []))}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _extract_assistant_text(stdout: str) -> str:
    """Concatenate the `text` parts from opencode's `--format json` stream.

    Event stream shape (one JSON object per line):
      {"type":"step_start",...}
      {"type":"text","part":{"type":"text","text":"..."}}
      {"type":"step_finish",...}
      {"type":"error","error":{"name":"APIError","data":{"message":"..."}}}
    Trailing non-JSON noise (e.g. "Shell cwd was reset to ...") is silently
    skipped.

    When the stream contains no text events but does contain one or more
    ``type:"error"`` events we raise with the first error surfaced inline —
    otherwise a failed auth / quota / rate-limit silently shows up upstream
    as the misleading "opencode produced no assistant text" diagnostic.
    This is how we discovered the launchd-env 401 bug on 2026-04-22.
    """
    chunks: list[str] = []
    error_events: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        event_type = event.get("type")
        if event_type == "error":
            err = event.get("error")
            if isinstance(err, dict):
                error_events.append(err)
            continue
        if event_type != "text":
            continue
        part = event.get("part") or {}
        text = part.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    text = "".join(chunks).strip()
    if not text and error_events:
        raise RuntimeError(
            "opencode error event: "
            f"{_summarize_agent_error(error_events[0], 'opencode returned only error events')}"
        )
    return text


def _extract_pi_assistant_text(stdout: str) -> str:
    """Extract the final assistant text from pi's ``--mode json`` stream.

    Pi emits JSONL events with complete assistant messages at ``message_end``.
    Tool-use turns also appear as assistant messages, so we keep the last
    assistant message that contains text content.
    """
    text_messages: list[str] = []
    error_events: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        event_type = event.get("type")
        if event_type == "error":
            if isinstance(event.get("error"), dict):
                error_events.append(event["error"])
            else:
                error_events.append(event)
            continue
        if event_type == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            chunks = _text_chunks_from_pi_content(message.get("content"))
            if chunks:
                text_messages.append("".join(chunks))
            continue
        # Defensive fallback for streams cut before message_end. Do not use
        # text_delta here because pi's delta events may include overlapping
        # partials; text_end carries the finalized content for that block.
        if event_type == "message_update":
            message_event = event.get("assistantMessageEvent")
            if isinstance(message_event, dict) and message_event.get("type") == "text_end":
                content = message_event.get("content")
                if isinstance(content, str) and content:
                    text_messages.append(content)
    text = (text_messages[-1] if text_messages else "").strip()
    if not text and error_events:
        raise RuntimeError(f"pi error event: {_summarize_agent_error(error_events[0], 'pi returned only error events')}")
    return text


def _text_chunks_from_pi_content(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return chunks


def _summarize_agent_error(error: dict[str, Any], default: str) -> str:
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    message = (
        data.get("message")
        or error.get("message")
        or error.get("name")
        or default
    )
    status = data.get("statusCode") or data.get("status")
    return f"{message} (HTTP {status})" if status else str(message)


def _cleanup_agent_text(text: str) -> str:
    """Strip code fences and extract the first balanced JSON object.

    Even with an explicit "no code fence" prompt, some models still wrap
    output in ```json ... ``` or add a short preamble. Rather than relying on
    perfect compliance, we scan for the first `{...}` block.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    extracted = _extract_first_json_object(stripped)
    return extracted if extracted is not None else stripped


def _extract_first_json_object(text: str) -> str | None:
    """Return the first balanced `{...}` substring, honoring JSON strings.

    Uses a small hand-rolled scanner rather than regex because nested braces
    inside values (common in our schema) would break any greedy pattern.
    """
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start is not None:
                return text[start : i + 1]
    return None


def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _agent_empty_output_reason(
    batch: list[SuggestionEnvelope],
    context: dict[str, Any],
    parsed: dict[str, Any],
) -> str | None:
    """Reject silent empty agent output that would erase useful assets.

    The agent is allowed to discard noisy suggestions, but it must say so in
    ``discarded_items``. A fully empty response for a non-empty batch has been
    observed in production and caused useful reviewer output to disappear while
    still being reported as a successful compile.
    """
    memory_records = parsed.get("memory_records") or {}
    emitted_memory = len(memory_records.get("user", [])) + len(memory_records.get("global", []))
    emitted_assets = (
        emitted_memory
        + len(parsed.get("recall_records") or [])
        + len(parsed.get("compiled_skills") or [])
        + len(parsed.get("manifest_entries") or [])
    )
    emitted_total = emitted_assets + len(parsed.get("discarded_items") or [])
    input_suggestions = [item for envelope in batch for item in envelope.suggestions]
    if input_suggestions and emitted_total == 0:
        return "agent_output_empty_unaccounted"
    if input_suggestions and emitted_assets == 0:
        return "agent_output_no_survivors"

    existing_memory = context.get("existing_memory_index")
    existing_user = existing_global = 0
    if isinstance(existing_memory, dict):
        existing_user = len(existing_memory.get("user") or [])
        existing_global = len(existing_memory.get("global") or [])
    has_memory_remove = any(
        item.family == "memory_updates"
        and str(item.details.get("action") or "add").strip().lower() == "remove"
        for item in input_suggestions
    )
    if not has_memory_remove:
        if existing_user and not memory_records.get("user"):
            return "agent_output_dropped_existing_assets"
        if existing_global and not memory_records.get("global"):
            return "agent_output_dropped_existing_assets"

    if context.get("existing_recall_records") and not parsed.get("recall_records"):
        return "agent_output_dropped_existing_assets"
    has_skill_retire = any(
        item.family == "skill_action"
        and str(item.details.get("action") or "").strip().lower() == "retire"
        for item in input_suggestions
    )
    if context.get("existing_manifest") and not has_skill_retire and not parsed.get("manifest_entries"):
        return "agent_output_dropped_existing_assets"
    return None


def _agent_quality_detail(
    batch: list[SuggestionEnvelope],
    context: dict[str, Any],
    parsed: dict[str, Any],
) -> str:
    memory_records = parsed.get("memory_records") or {}
    input_suggestion_count = sum(len(envelope.suggestions) for envelope in batch)
    existing_memory = context.get("existing_memory_index")
    existing_user = existing_global = 0
    if isinstance(existing_memory, dict):
        existing_user = len(existing_memory.get("user") or [])
        existing_global = len(existing_memory.get("global") or [])
    return (
        f"input_suggestions={input_suggestion_count}; "
        f"output_memory_user={len(memory_records.get('user', []))}; "
        f"output_memory_global={len(memory_records.get('global', []))}; "
        f"output_recall={len(parsed.get('recall_records') or [])}; "
        f"output_skills={len(parsed.get('compiled_skills') or [])}; "
        f"output_discarded={len(parsed.get('discarded_items') or [])}; "
        f"discarded_items={_truncate(json.dumps(parsed.get('discarded_items') or [], ensure_ascii=False), 240)}; "
        f"existing_memory_user={existing_user}; "
        f"existing_memory_global={existing_global}; "
        f"existing_recall={len(context.get('existing_recall_records') or [])}; "
        f"existing_manifest={len(context.get('existing_manifest') or [])}"
    )


def get_backend(name: str) -> CompilerBackend:
    if name == "script":
        return ScriptCompilerBackend()
    if name == "agent:opencode":
        return AgentCompilerBackend()
    if name == "agent:pi":
        return PiAgentCompilerBackend()
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
