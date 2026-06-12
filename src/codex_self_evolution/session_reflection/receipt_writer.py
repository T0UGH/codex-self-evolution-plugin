from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..storage import utc_now
from .validation import REQUIRED_LIST_FIELDS, VALID_RECEIPT_STATUSES


class ReceiptDraftError(ValueError):
    """Raised when a semantic receipt draft cannot become a canonical receipt."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_field",
        field: str = "",
        expected: str = "",
        actual: str = "",
    ) -> None:
        """Store a compact machine-readable writer failure."""
        super().__init__(message)
        self.code = code
        self.field = field
        self.expected = expected
        self.actual = actual


def write_receipt_from_draft(
    *,
    draft_path: str | Path,
    output_path: str | Path,
    job_id: str,
    parent_session_id: str,
    child_thread_id: str,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    """Validate a child draft and atomically write a canonical v1 receipt."""
    draft = _load_draft(Path(draft_path))
    _validate_draft_shape(draft)
    receipt = _canonical_receipt(
        draft,
        job_id=job_id,
        parent_session_id=parent_session_id,
        child_thread_id=child_thread_id,
        started_at=started_at,
        finished_at=finished_at,
    )
    output = Path(output_path).expanduser().resolve()
    _atomic_write_json_preserve_order(output, receipt)
    try:
        receipt_writer_error_path(output).unlink()
    except FileNotFoundError:
        pass
    return {"status": "written", "receipt_path": str(output)}


def receipt_writer_error_path(output_path: str | Path) -> Path:
    """Return the sidecar path used for machine-readable writer failures."""
    output = Path(output_path).expanduser().resolve()
    return output.with_name(f"{output.stem}.writer-error{output.suffix or '.json'}")


def write_receipt_writer_error(output_path: str | Path, error: ReceiptDraftError) -> Path:
    """Persist a compact writer error sidecar without touching receipt output."""
    path = receipt_writer_error_path(output_path)
    payload = {
        "status": "failed",
        "reason": "draft_invalid",
        "code": error.code,
        "field": error.field,
        "message": str(error),
        "expected": error.expected,
        "actual": error.actual,
    }
    _atomic_write_json_preserve_order(path, payload)
    return path


def _load_draft(path: Path) -> dict[str, Any]:
    """Read and parse the draft JSON object from disk."""
    try:
        loaded = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReceiptDraftError(
            f"receipt draft invalid: draft file not found: {path}",
            code="draft_missing",
            expected="existing JSON object file",
            actual="missing",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ReceiptDraftError(
            f"receipt draft invalid: draft JSON is invalid: {exc.msg}",
            code="invalid_json",
            expected="JSON object",
            actual="invalid JSON",
        ) from exc
    if not isinstance(loaded, dict):
        raise ReceiptDraftError(
            "receipt draft invalid: top-level value must be a JSON object",
            code="invalid_top_level",
            expected="object",
            actual=type(loaded).__name__,
        )
    return loaded


def _validate_draft_shape(draft: dict[str, Any]) -> None:
    """Validate semantic draft fields before deterministic envelope injection."""
    status = draft.get("status")
    if status not in VALID_RECEIPT_STATUSES:
        allowed = ", ".join(sorted(VALID_RECEIPT_STATUSES))
        raise ReceiptDraftError(
            f'receipt draft invalid: field "status" must be one of [{allowed}]',
            field="status",
            expected=f"one of [{allowed}]",
            actual=repr(status),
        )
    for field in REQUIRED_LIST_FIELDS:
        if not isinstance(draft.get(field), list):
            raise ReceiptDraftError(
                f'receipt draft invalid: field "{field}" must be a JSON array',
                field=field,
                expected="array",
                actual=type(draft.get(field)).__name__,
            )


def _canonical_receipt(
    draft: dict[str, Any],
    *,
    job_id: str,
    parent_session_id: str,
    child_thread_id: str,
    started_at: str | None,
    finished_at: str | None,
) -> dict[str, Any]:
    """Return a deterministic receipt object with parent-owned envelope fields."""
    now = _utc_timestamp()
    started = started_at or now
    finished = finished_at or now
    _validate_timestamp("started_at", started)
    _validate_timestamp("finished_at", finished)
    return {
        "schema_version": 1,
        "job_id": job_id,
        "parent_session_id": parent_session_id,
        "child_thread_id": child_thread_id,
        "status": draft["status"],
        "memory_changes": draft["memory_changes"],
        "skill_changes": draft["skill_changes"],
        "skipped_candidates": draft["skipped_candidates"],
        "validation_notes": draft["validation_notes"],
        "errors": draft["errors"],
        "started_at": started,
        "finished_at": finished,
    }


def _validate_timestamp(field: str, value: str) -> None:
    """Fail fast when caller-provided timestamps are not concrete UTC ISO strings."""
    if not isinstance(value, str) or not value.endswith("Z") or "$" in value or "<" in value or ">" in value:
        raise ReceiptDraftError(
            f'receipt draft invalid: field "{field}" must be a concrete UTC ISO timestamp',
            field=field,
            expected="UTC ISO timestamp ending in Z",
            actual=repr(value),
        )
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptDraftError(
            f'receipt draft invalid: field "{field}" must be a concrete UTC ISO timestamp',
            field=field,
            expected="UTC ISO timestamp ending in Z",
            actual=repr(value),
        ) from exc


def _utc_timestamp() -> str:
    """Return a second-precision UTC timestamp for generated receipt fields."""
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_json_preserve_order(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write UTF-8 JSON while preserving insertion order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        if temp_name:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass
        raise
