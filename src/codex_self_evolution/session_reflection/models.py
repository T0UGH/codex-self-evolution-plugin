from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardDecision:
    """Decision returned by the Stop-entry recursion guard."""

    # Whether the caller should skip starting a reflection job.
    skip: bool
    # Stable machine-readable reason for a skipped payload.
    reason: str = ""
    # Optional path, id, or source value that explains the decision.
    detail: str = ""
