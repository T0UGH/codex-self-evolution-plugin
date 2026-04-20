#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codex_self_evolution.review.providers import ReviewProviderError
from codex_self_evolution.review.runner import run_reviewer
from codex_self_evolution.schemas import SchemaError


PROVIDERS = ["minimax", "openai-compatible", "anthropic-style"]


def build_snapshot() -> dict:
    return {
        "reviewer_provider": "dummy",
        "context": {
            "session_id": "provider-smoke-session",
            "thread_id": "provider-smoke-thread",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "repo_root": str(Path.cwd()),
        },
        "turn_snapshot": {
            "transcript": "User asked to remember that focused pytest runs are preferred before full suite runs.",
            "thread_read_output": "We created a reusable workflow for focused pytest before broader regression.",
            "last_assistant_message": "I updated the workflow and documented the preferred testing sequence.",
        },
        "comparison_materials": {
            "current_user_md": "Prefer concise summaries.",
            "current_memory_md": "Run focused pytest before full suite when iterating on local changes.",
            "managed_skills_summary": [],
        },
        "source_authority": ["thread_read_output", "transcript", "memory_files"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--api-base")
    parser.add_argument("--model")
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args(argv)

    snapshot = build_snapshot()
    snapshot["reviewer_provider"] = args.provider
    provider_options = {
        "timeout_seconds": args.timeout_seconds,
    }
    if args.api_base:
        provider_options["api_base"] = args.api_base
    if args.model:
        provider_options["model"] = args.model

    last_error: Exception | None = None
    for attempt in range(1, max(args.attempts, 1) + 1):
        try:
            reviewer_output, provider_result = run_reviewer(snapshot, provider_name=args.provider, provider_options=provider_options)
            payload = {
                "provider": provider_result.provider,
                "attempt": attempt,
                "request_payload": provider_result.request_payload,
                "memory_updates": [item.to_dict() for item in reviewer_output.memory_updates],
                "recall_candidate": [item.to_dict() for item in reviewer_output.recall_candidate],
                "skill_action": [item.to_dict() for item in reviewer_output.skill_action],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        except (SchemaError, ReviewProviderError) as exc:
            last_error = exc
            if attempt == max(args.attempts, 1):
                break
    print(f"provider smoke test failed after {max(args.attempts, 1)} attempts: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
