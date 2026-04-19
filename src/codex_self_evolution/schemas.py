from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SuggestionFamily = Literal["memory_updates", "recall_candidate", "skill_action"]
SkillActionType = Literal["create", "patch", "edit", "retire"]
SuggestionState = Literal["pending", "processing", "done", "failed", "discarded"]

ALLOWED_FAMILIES = {"memory_updates", "recall_candidate", "skill_action"}
ALLOWED_SKILL_ACTIONS = {"create", "patch", "edit", "retire"}
ALLOWED_STATES = {"pending", "processing", "done", "failed", "discarded"}


class SchemaError(ValueError):
    pass


class RuntimeErrorBase(ValueError):
    pass


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{name} must be an object")
    return value


def _require_non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{name} must be a non-empty string")
    return value.strip()


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaError(f"{name} must be a list")
    return value


@dataclass(frozen=True)
class Suggestion:
    family: SuggestionFamily
    summary: str
    details: dict[str, Any]
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Suggestion":
        data = _require_mapping(payload, "suggestion")
        family = _require_non_empty_string(data.get("family"), "family")
        if family not in ALLOWED_FAMILIES:
            raise SchemaError(f"unsupported suggestion family: {family}")
        summary = _require_non_empty_string(data.get("summary"), "summary")
        details = _require_mapping(data.get("details"), "details")
        if family == "skill_action":
            action = _require_non_empty_string(details.get("action"), "details.action")
            if action not in ALLOWED_SKILL_ACTIONS:
                raise SchemaError(f"unsupported skill action: {action}")
        confidence = data.get("confidence", 1.0)
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise SchemaError("confidence must be between 0 and 1")
        return cls(family=family, summary=summary, details=details, confidence=float(confidence))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewerOutput:
    memory_updates: list[Suggestion] = field(default_factory=list)
    recall_candidate: list[Suggestion] = field(default_factory=list)
    skill_action: list[Suggestion] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewerOutput":
        data = _require_mapping(payload, "reviewer_output")
        unexpected = set(data) - ALLOWED_FAMILIES
        if unexpected:
            raise SchemaError(f"unexpected reviewer keys: {sorted(unexpected)}")
        converted: dict[str, list[Suggestion]] = {}
        for family in ALLOWED_FAMILIES:
            items = _require_list(data.get(family, []), family)
            converted[family] = [Suggestion.from_dict({**item, "family": family}) for item in items]
        return cls(**converted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_updates": [item.to_dict() for item in self.memory_updates],
            "recall_candidate": [item.to_dict() for item in self.recall_candidate],
            "skill_action": [item.to_dict() for item in self.skill_action],
        }

    def all_suggestions(self) -> list[Suggestion]:
        return self.memory_updates + self.recall_candidate + self.skill_action


@dataclass(frozen=True)
class SuggestionEnvelope:
    schema_version: int
    suggestion_id: str
    idempotency_key: str
    thread_id: str
    cwd: str
    repo_fingerprint: str
    reviewer_timestamp: str
    suggestions: list[Suggestion]
    source_authority: list[str]
    state: SuggestionState = "pending"
    attempt_count: int = 0
    review_snapshot_path: str | None = None
    failure_reason: str | None = None
    transition_log: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SuggestionEnvelope":
        data = _require_mapping(payload, "suggestion_envelope")
        suggestions = [Suggestion.from_dict(item) for item in _require_list(data.get("suggestions", []), "suggestions")]
        source_authority = _require_list(data.get("source_authority", []), "source_authority")
        if any(not isinstance(item, str) for item in source_authority):
            raise SchemaError("source_authority must contain strings")
        schema_version = data.get("schema_version")
        if schema_version != 1:
            raise SchemaError("schema_version must be 1")
        state = data.get("state", "pending")
        if state not in ALLOWED_STATES:
            raise SchemaError(f"unsupported suggestion state: {state}")
        transition_log = _require_list(data.get("transition_log", []), "transition_log")
        if any(not isinstance(item, dict) for item in transition_log):
            raise SchemaError("transition_log must contain objects")
        return cls(
            schema_version=1,
            suggestion_id=_require_non_empty_string(data.get("suggestion_id"), "suggestion_id"),
            idempotency_key=_require_non_empty_string(data.get("idempotency_key"), "idempotency_key"),
            thread_id=_require_non_empty_string(data.get("thread_id"), "thread_id"),
            cwd=_require_non_empty_string(data.get("cwd"), "cwd"),
            repo_fingerprint=_require_non_empty_string(data.get("repo_fingerprint"), "repo_fingerprint"),
            reviewer_timestamp=_require_non_empty_string(data.get("reviewer_timestamp"), "reviewer_timestamp"),
            suggestions=suggestions,
            source_authority=source_authority,
            state=state,
            attempt_count=int(data.get("attempt_count", 0)),
            review_snapshot_path=data.get("review_snapshot_path"),
            failure_reason=data.get("failure_reason"),
            transition_log=transition_log,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "suggestion_id": self.suggestion_id,
            "idempotency_key": self.idempotency_key,
            "thread_id": self.thread_id,
            "cwd": self.cwd,
            "repo_fingerprint": self.repo_fingerprint,
            "reviewer_timestamp": self.reviewer_timestamp,
            "suggestions": [item.to_dict() for item in self.suggestions],
            "source_authority": list(self.source_authority),
            "state": self.state,
            "attempt_count": self.attempt_count,
            "review_snapshot_path": self.review_snapshot_path,
            "failure_reason": self.failure_reason,
            "transition_log": list(self.transition_log),
        }


@dataclass(frozen=True)
class RecallRecord:
    id: str
    summary: str
    content: str
    source_paths: list[str]
    repo_fingerprint: str
    cwd: str
    thread_id: str = ""
    turn_id: str = ""
    source_updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RecallRecord":
        data = _require_mapping(payload, "recall_record")
        source_paths = _require_list(data.get("source_paths", []), "source_paths")
        return cls(
            id=_require_non_empty_string(data.get("id"), "id"),
            summary=_require_non_empty_string(data.get("summary"), "summary"),
            content=_require_non_empty_string(data.get("content"), "content"),
            source_paths=[_require_non_empty_string(item, "source_path") for item in source_paths],
            repo_fingerprint=_require_non_empty_string(data.get("repo_fingerprint"), "repo_fingerprint"),
            cwd=_require_non_empty_string(data.get("cwd"), "cwd"),
            thread_id=str(data.get("thread_id", "")),
            turn_id=str(data.get("turn_id", "")),
            source_updated_at=str(data.get("source_updated_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillManifestEntry:
    skill_id: str
    action: SkillActionType
    title: str
    path: str
    status: str
    owner: str
    managed: bool
    created_by: str
    updated_at: str
    retired_at: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SkillManifestEntry":
        data = _require_mapping(payload, "skill_manifest_entry")
        action = _require_non_empty_string(data.get("action"), "action")
        if action not in ALLOWED_SKILL_ACTIONS:
            raise SchemaError(f"unsupported skill action: {action}")
        managed = data.get("managed")
        if not isinstance(managed, bool):
            raise SchemaError("managed must be a boolean")
        return cls(
            skill_id=_require_non_empty_string(data.get("skill_id"), "skill_id"),
            action=action,
            title=_require_non_empty_string(data.get("title"), "title"),
            path=_require_non_empty_string(data.get("path"), "path"),
            status=_require_non_empty_string(data.get("status"), "status"),
            owner=_require_non_empty_string(data.get("owner"), "owner"),
            managed=managed,
            created_by=_require_non_empty_string(data.get("created_by"), "created_by"),
            updated_at=_require_non_empty_string(data.get("updated_at"), "updated_at"),
            retired_at=data.get("retired_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompilerReceipt:
    run_status: str
    backend: str
    processed_count: int
    archived_count: int
    memory_records: int
    recall_records: int
    managed_skills: int
    item_receipts: list[dict[str, Any]] = field(default_factory=list)
    skip_reason: str | None = None
    fallback_backend: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
