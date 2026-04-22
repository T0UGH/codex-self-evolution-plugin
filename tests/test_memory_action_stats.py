"""Observability: ``_tally_memory_actions`` extracts reviewer behavior into receipts.

Without this breakdown, the only signal Phase 1 leaves behind is the aggregate
``memory_records`` count in the receipt — which doesn't distinguish "reviewer
is actually using replace" from "reviewer keeps adding duplicates and existing
entries pass through untouched". These tests pin the breakdown shape so the
observability dashboards we build next week have a stable contract to read.
"""
from __future__ import annotations

from codex_self_evolution.compiler.engine import (
    _aggregate_scan_stats,
    _tally_memory_actions,
)
from codex_self_evolution.schemas import Suggestion, SuggestionEnvelope


def _envelope(suggestions: list[Suggestion]) -> SuggestionEnvelope:
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="sug",
        idempotency_key="idem",
        thread_id="t",
        cwd="/tmp/repo",
        repo_fingerprint="fp",
        reviewer_timestamp="2026-04-22T00:00:00Z",
        suggestions=suggestions,
        source_authority=[],
    )


def test_tally_returns_empty_when_no_memory_updates() -> None:
    envelopes = [_envelope([])]
    assert _tally_memory_actions(envelopes) == {}


def test_tally_counts_default_add_when_action_missing() -> None:
    """Legacy suggestions written before Phase 1 have no ``action`` key — they
    must still be counted as ``add`` so the time series stays continuous
    across the upgrade boundary."""
    envelopes = [
        _envelope(
            [Suggestion(family="memory_updates", summary="s", details={"content": "c"})]
        )
    ]
    stats = _tally_memory_actions(envelopes)
    assert stats == {
        "total": 1,
        "by_action": {"add": 1, "replace": 0, "remove": 0},
        "by_scope": {"user": 0, "global": 1},  # default scope is global
    }


def test_tally_aggregates_actions_and_scopes_across_envelopes() -> None:
    envelopes = [
        _envelope(
            [
                Suggestion(
                    family="memory_updates",
                    summary="user pref",
                    details={"content": "terse", "scope": "user"},
                ),
                Suggestion(
                    family="memory_updates",
                    summary="repo fact",
                    details={"content": "ok", "scope": "global", "action": "add"},
                ),
            ]
        ),
        _envelope(
            [
                Suggestion(
                    family="memory_updates",
                    summary="updated fact",
                    details={
                        "content": "new",
                        "scope": "global",
                        "action": "replace",
                        "old_summary": "old",
                    },
                ),
                Suggestion(
                    family="memory_updates",
                    summary="drop stale",
                    details={"scope": "global", "action": "remove", "old_summary": "stale"},
                ),
            ]
        ),
    ]
    stats = _tally_memory_actions(envelopes)
    assert stats["total"] == 4
    assert stats["by_action"] == {"add": 2, "replace": 1, "remove": 1}
    assert stats["by_scope"] == {"user": 1, "global": 3}


def test_tally_ignores_non_memory_families() -> None:
    envelopes = [
        _envelope(
            [
                Suggestion(family="memory_updates", summary="m", details={"content": "x"}),
                Suggestion(family="recall_candidate", summary="r", details={"content": "y"}),
                Suggestion(
                    family="skill_action",
                    summary="s",
                    details={
                        "action": "create",
                        "skill_id": "xyz",
                        "title": "T",
                        "content": "body",
                    },
                ),
            ]
        )
    ]
    stats = _tally_memory_actions(envelopes)
    assert stats["total"] == 1  # only the memory_updates one


def test_aggregate_scan_stats_rolls_up_across_buckets() -> None:
    """The scan-level aggregate is what hits plugin.log once per scan — it
    has to correctly sum action breakdowns across all processed buckets so
    a single log line captures the full day's reviewer behaviour."""
    results = [
        {
            "compile_status": "success",
            "fallback_backend": None,
            "discarded_count": 0,
            "memory_action_stats": {
                "total": 3,
                "by_action": {"add": 2, "replace": 1, "remove": 0},
                "by_scope": {"user": 1, "global": 2},
            },
        },
        {
            "compile_status": "success",
            "fallback_backend": "script",  # backend fell back — surfaced in aggregate
            "discarded_count": 1,
            "memory_action_stats": {
                "total": 1,
                "by_action": {"add": 0, "replace": 0, "remove": 1},
                "by_scope": {"user": 0, "global": 1},
            },
        },
        {
            # skip_empty bucket — no stats, no fallback, no counts
            "compile_status": "skip_empty",
            "memory_action_stats": {},
        },
    ]
    agg = _aggregate_scan_stats(results)
    assert agg["buckets_processed"] == 2
    assert agg["buckets_with_fallback"] == 1
    assert agg["total_memory_suggestions"] == 4
    assert agg["actions"] == {"add": 2, "replace": 1, "remove": 1}
    assert agg["scopes"] == {"user": 1, "global": 3}
    assert agg["total_discarded"] == 1


def test_aggregate_handles_scan_with_no_runs() -> None:
    """All-skipped scan (the common case when nothing has pending work)
    must not leak zero-filled keys into plugin.log via _observability_extras."""
    results = [
        {"compile_status": "skip_empty"},
        {"compile_status": "skip_empty"},
    ]
    agg = _aggregate_scan_stats(results)
    assert agg["buckets_processed"] == 0
    assert agg["total_memory_suggestions"] == 0
