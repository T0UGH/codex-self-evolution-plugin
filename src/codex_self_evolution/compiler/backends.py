from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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

    # Upper bound for the opencode subprocess. Kept strictly below the 30-minute
    # compile-lock hard limit so a hung agent times out, yields in finally, and
    # releases the lock before the next preflight evicts it.
    DEFAULT_TIMEOUT_SECONDS = 15 * 60

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
        # opencode 1.4.0's `run` takes the message as a positional argument and
        # attaches files via `--file`, so we cannot pipe payload on stdin.
        # Writing the JSON payload to a temp file and attaching it keeps us
        # clear of argv size limits (a full batch plus existing_assets can
        # easily blow past typical MAX_ARG_STRLEN).
        payload_path = _write_payload_tempfile(payload)
        try:
            command = (
                options.get("opencode_command")
                or _command_from_env()
                or _build_default_opencode_command(payload_path, options)
            )
            timeout = float(options.get("opencode_timeout_seconds", self.DEFAULT_TIMEOUT_SECONDS))
            proc = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"opencode exit={proc.returncode}; stderr={proc.stderr.strip()[:400]}"
                )
            # `--format json` emits one JSON event per line plus a trailing
            # "Shell cwd was reset to ..." noise line. We concatenate the
            # assistant's `text` parts and strip any code fence / prose the
            # model might still wrap around the JSON payload.
            assistant_text = _extract_assistant_text(proc.stdout)
            if not assistant_text:
                raise RuntimeError(
                    f"opencode produced no assistant text; "
                    f"stderr={proc.stderr.strip()[:400]}"
                )
            return _cleanup_agent_text(assistant_text)
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass


def _command_from_env() -> list[str] | None:
    raw = os.environ.get("CODEX_SELF_EVOLUTION_OPENCODE_COMMAND")
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


def _build_compile_prompt(payload_path: str) -> str:
    # Kept inline (not a separate file) so the contract travels with the code
    # that depends on it. If you update this prompt, also update
    # parse_agent_compile_response in agent_io.py — they are two halves of
    # the same wire protocol.
    return (
        f"The attached JSON file at {payload_path} is a compile payload from "
        "the codex-self-evolution-plugin reviewer pipeline. Your job is to "
        "merge its `batch` into its `existing_assets` and emit the merged "
        "artifacts.\n\n"
        "Rules:\n"
        "1. Dedupe memory entries by content+summary; preserve existing "
        "provenance where possible.\n"
        "2. Dedupe recall entries by id; keep stable ones untouched.\n"
        "3. Only emit skill actions (create|patch|edit|retire) that are "
        "consistent with existing manifest ownership (managed=true entries "
        "only).\n"
        "4. Do NOT write files yourself. The writer handles final I/O.\n\n"
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
        first = error_events[0]
        data = first.get("data") if isinstance(first.get("data"), dict) else {}
        message = (
            data.get("message")
            or first.get("message")
            or first.get("name")
            or "opencode returned only error events"
        )
        status = data.get("statusCode")
        summary = f"{message} (HTTP {status})" if status else str(message)
        raise RuntimeError(f"opencode error event: {summary}")
    return text


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
