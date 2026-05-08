"""Multi-project scan: preflight+compile over every bucket under <home>/projects/.

This is what the launchd scheduler will call — a single cron-style
invocation that drains every repo with pending suggestions. The two
properties that MUST hold for scheduler use:

1. **Per-bucket exception isolation**: one corrupt bucket can't wedge
   the whole pipeline. launchd runs unattended; a crash means silent
   backlog for every *other* repo too.
2. **Zero side effects when home is missing**: fresh installs run scan
   before any reviewer has fired. Must return an empty summary, not
   raise FileNotFoundError.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from codex_self_evolution import cli
from codex_self_evolution.compiler import backends
from codex_self_evolution.compiler import engine
from codex_self_evolution.compiler.engine import scan_all_projects
from codex_self_evolution.config import DEFAULT_LOCK_STALE_SECONDS, PROJECTS_SUBDIR, build_paths
from codex_self_evolution.hooks.stop_review import stop_review
from codex_self_evolution.schemas import Suggestion, SuggestionEnvelope
from codex_self_evolution.storage import atomic_write_json


def _seed_bucket_with_pending(home: Path, project_name: str) -> Path:
    """Produce a bucket that looks like a real session ran there.

    Returns the bucket's state_dir path. Uses the dummy reviewer provider
    so the suggestion envelope lands without touching any LLM. stop_review
    expects a payload on disk (Codex hook contract), so we write one into
    the bucket next to the state it will produce.
    """
    bucket = home / PROJECTS_SUBDIR / project_name
    bucket.mkdir(parents=True)
    repo_root = Path(f"/tmp/fake-{project_name}")
    payload_path = bucket / "test-payload.json"
    payload_path.write_text(
        json.dumps({
            "thread_id": f"thread-{project_name}",
            "turn_id": "turn-1",
            "cwd": str(repo_root),
            "transcript": "seeded for scan test",
            "thread_read_output": "ctx",
            "reviewer_provider": "dummy",
            "provider_stub_response": {
                "memory_updates": [
                    {"summary": f"Fact from {project_name}",
                     "details": {"content": "Stable content.", "scope": "user"}},
                ],
            },
        }),
        encoding="utf-8",
    )
    stop_review(hook_payload=payload_path, state_dir=bucket)
    return bucket


# ---------- boundary conditions ----------


def test_scan_on_missing_home_returns_empty_summary_not_error(tmp_path):
    # home dir doesn't exist at all — first ever launchd run after a fresh
    # install. Must not raise, must report zero work.
    result = scan_all_projects(home=tmp_path / "does-not-exist")
    assert result["total_projects"] == 0
    assert result["results"] == []
    assert result["counts"] == {"run": 0, "skipped": 0, "failed": 0}


def test_scan_on_empty_projects_dir_returns_empty_summary(tmp_path):
    (tmp_path / PROJECTS_SUBDIR).mkdir()
    result = scan_all_projects(home=tmp_path)
    assert result["total_projects"] == 0


def test_scan_ignores_non_directory_entries_in_projects_dir(tmp_path):
    # If a user drops a stray file into <home>/projects/ (unlikely but
    # possible — it's a user-visible dir), scan must skip it rather than
    # choke trying to treat it as a state dir.
    projects = tmp_path / PROJECTS_SUBDIR
    projects.mkdir()
    (projects / "README.txt").write_text("stray", encoding="utf-8")
    result = scan_all_projects(home=tmp_path)
    assert result["total_projects"] == 0


# ---------- happy path: real buckets, script backend for determinism ----------


def test_scan_processes_bucket_with_pending(tmp_path):
    bucket = _seed_bucket_with_pending(tmp_path, "-fake-proj-alpha")

    result = scan_all_projects(home=tmp_path, backend="script")

    assert result["total_projects"] == 1
    entry = result["results"][0]
    assert entry["project"] == "-fake-proj-alpha"
    assert entry["preflight_status"] == "run"
    assert entry["compile_status"] == "success"
    assert entry["processed_count"] == 1
    assert entry["error"] is None
    assert result["counts"] == {"run": 1, "skipped": 0, "failed": 0}
    # Real receipt should now exist on disk — proves we actually invoked
    # run_compile, not just preflight.
    assert (bucket / "compiler" / "last_receipt.json").exists()


def test_scan_skips_bucket_with_no_pending(tmp_path):
    # An empty bucket: project directory exists (maybe reviewer set it up
    # but never produced anything worth saving) but no pending suggestions.
    (tmp_path / PROJECTS_SUBDIR / "-fake-empty").mkdir(parents=True)

    result = scan_all_projects(home=tmp_path, backend="script")

    assert result["total_projects"] == 1
    entry = result["results"][0]
    assert entry["preflight_status"] == "skip_empty"
    assert entry["compile_status"] == "skip_empty"
    assert entry["processed_count"] == 0
    assert result["counts"] == {"run": 0, "skipped": 1, "failed": 0}


def test_scan_handles_mixed_buckets(tmp_path):
    # Two buckets: one has work, one doesn't. Result must include both,
    # counts must reflect the split.
    _seed_bucket_with_pending(tmp_path, "-fake-busy")
    (tmp_path / PROJECTS_SUBDIR / "-fake-idle").mkdir(parents=True)

    result = scan_all_projects(home=tmp_path, backend="script")

    assert result["total_projects"] == 2
    statuses = {e["project"]: e["compile_status"] for e in result["results"]}
    assert statuses == {"-fake-busy": "success", "-fake-idle": "skip_empty"}
    assert result["counts"] == {"run": 1, "skipped": 1, "failed": 0}
    # Buckets must be iterated in sorted order so scan is deterministic
    # across runs (launchd logs become diffable).
    assert [e["project"] for e in result["results"]] == ["-fake-busy", "-fake-idle"]


# ---------- the critical property: per-bucket exception isolation ----------


def test_scan_isolates_preflight_exceptions(tmp_path, monkeypatch):
    # Rig: first bucket throws, second bucket has real work. The second
    # one MUST still be processed — otherwise one bad repo silently
    # wedges the entire scheduled pipeline for every other repo.
    _seed_bucket_with_pending(tmp_path, "-fake-bad")
    _seed_bucket_with_pending(tmp_path, "-fake-good")

    real_preflight = engine.preflight_compile

    def fake_preflight(state_dir=None, **kwargs):
        if state_dir and "fake-bad" in str(state_dir):
            raise RuntimeError("simulated disk corruption")
        return real_preflight(state_dir=state_dir, **kwargs)

    monkeypatch.setattr(engine, "preflight_compile", fake_preflight)

    result = scan_all_projects(home=tmp_path, backend="script")

    assert result["total_projects"] == 2
    by_project = {e["project"]: e for e in result["results"]}
    assert by_project["-fake-bad"]["compile_status"] == "error"
    assert "simulated disk corruption" in by_project["-fake-bad"]["error"]
    # The good bucket must have processed despite the bad one throwing.
    assert by_project["-fake-good"]["compile_status"] == "success"
    assert by_project["-fake-good"]["processed_count"] == 1
    assert result["counts"] == {"run": 1, "skipped": 0, "failed": 1}


def test_scan_isolates_compile_exceptions(tmp_path, monkeypatch):
    # Different failure mode: preflight succeeds ("run"), compile raises.
    # Covers the case where a bucket is fine for listing work but something
    # downstream (locked file, corrupt envelope, backend crash) blows up.
    _seed_bucket_with_pending(tmp_path, "-fake-compile-fails")
    _seed_bucket_with_pending(tmp_path, "-fake-other")

    real_compile = engine.run_compile

    def fake_compile(state_dir=None, **kwargs):
        if state_dir and "compile-fails" in str(state_dir):
            raise RuntimeError("boom")
        return real_compile(state_dir=state_dir, **kwargs)

    monkeypatch.setattr(engine, "run_compile", fake_compile)

    result = scan_all_projects(home=tmp_path, backend="script")

    by_project = {e["project"]: e for e in result["results"]}
    assert by_project["-fake-compile-fails"]["compile_status"] == "error"
    assert by_project["-fake-compile-fails"]["preflight_status"] == "run"
    assert "boom" in by_project["-fake-compile-fails"]["error"]
    assert by_project["-fake-other"]["compile_status"] == "success"
    assert result["counts"]["failed"] == 1
    assert result["counts"]["run"] == 1


def test_run_compile_marks_agent_failure_as_failed_not_processing(tmp_path, monkeypatch):
    bucket = _seed_bucket_with_pending(tmp_path, "-fake-agent-quality-fail")

    class BadBackend:
        def compile(self, batch, context, options):
            raise RuntimeError("agent quality gate failed")

    monkeypatch.setattr(engine, "get_backend", lambda _: BadBackend())

    result = engine.run_compile(state_dir=bucket, backend="agent:opencode")

    assert result["status"] == "error"
    assert "agent quality gate failed" in result["error"]
    assert not list((bucket / "suggestions" / "processing").glob("*.json"))
    failed = list((bucket / "suggestions" / "failed").glob("*.json"))
    assert len(failed) == 1
    receipt = json.loads((bucket / "compiler" / "last_receipt.json").read_text(encoding="utf-8"))
    assert receipt["run_status"] == "error"
    assert receipt["item_receipts"][0]["state"] == "failed"


def test_run_compile_passes_compile_options_to_backend(tmp_path, monkeypatch):
    bucket = _seed_bucket_with_pending(tmp_path, "-fake-agent-options")
    seen_options = []

    class OptionsBackend:
        def compile(self, batch, context, options):
            seen_options.append(dict(options))
            return backends.CompileArtifacts(
                memory_records={"user": [{"summary": "s", "content": "c"}], "global": []},
                recall_records=[],
                compiled_skills=[],
                manifest_entries=[],
                discarded_items=[],
                backend_name="agent:pi",
                compiler_observability={
                    "backend": "agent:pi",
                    "provider": "kimi",
                    "model": "kimi-k2.6",
                    "mode": "edit",
                    "duration_ms": 42,
                    "input": {"envelopes": 1, "suggestions": 1},
                    "output": {"memory_records": 1, "recall_records": 0, "compiled_skills": 0, "discarded_items": 0},
                },
            )

    monkeypatch.setattr(engine, "get_backend", lambda _: OptionsBackend())

    result = engine.run_compile(
        state_dir=bucket,
        backend="agent:pi",
        compile_options={"pi_mode": "json", "pi_model": "kimi-k2.6"},
    )

    assert result["status"] == "success"
    assert seen_options == [{"allow_fallback": True, "pi_mode": "json", "pi_model": "kimi-k2.6"}]
    assert result["compiler_observability"]["provider"] == "kimi"
    receipt = json.loads((bucket / "compiler" / "last_receipt.json").read_text(encoding="utf-8"))
    assert receipt["compiler_observability"]["model"] == "kimi-k2.6"


def test_run_compile_limits_pi_edit_mode_to_single_envelope(tmp_path, monkeypatch):
    bucket = _seed_bucket_with_pending(tmp_path, "-fake-pi-edit-single")
    second_payload = bucket / "test-payload-2.json"
    second_payload.write_text(
        json.dumps({
            "thread_id": "thread-pi-edit-2",
            "turn_id": "turn-2",
            "cwd": "/tmp/fake-pi-edit-single",
            "transcript": "second seeded item",
            "thread_read_output": "ctx",
            "reviewer_provider": "dummy",
            "provider_stub_response": {
                "memory_updates": [
                    {"summary": "Second pi edit fact", "details": {"content": "More content.", "scope": "user"}},
                ],
            },
        }),
        encoding="utf-8",
    )
    stop_review(hook_payload=second_payload, state_dir=bucket)
    seen_batch_sizes = []

    class SingleBackend:
        def compile(self, batch, context, options):
            seen_batch_sizes.append(len(batch))
            suggestion = batch[0].suggestions[0]
            return backends.CompileArtifacts(
                memory_records={"user": [{"summary": suggestion.summary, "content": suggestion.details["content"]}], "global": []},
                recall_records=[],
                compiled_skills=[],
                manifest_entries=[],
                discarded_items=[],
                backend_name="agent:pi",
            )

    monkeypatch.setattr(engine, "get_backend", lambda _: SingleBackend())

    result = engine.run_compile(
        state_dir=bucket,
        backend="agent:pi",
        batch_size=5,
        compile_options={"pi_mode": "edit"},
    )

    assert result["status"] == "success"
    assert result["processed_count"] == 1
    assert seen_batch_sizes == [1]
    assert len(list((bucket / "suggestions" / "pending").glob("*.json"))) == 1


def test_scan_drains_multiple_pi_edit_runs_without_batching_them(tmp_path, monkeypatch):
    bucket = _seed_bucket_with_pending(tmp_path, "-fake-pi-edit-drain")
    for index in range(2, 5):
        payload = bucket / f"test-payload-{index}.json"
        payload.write_text(
            json.dumps({
                "thread_id": f"thread-pi-edit-drain-{index}",
                "turn_id": f"turn-{index}",
                "cwd": "/tmp/fake-pi-edit-drain",
                "transcript": f"seeded item {index}",
                "thread_read_output": "ctx",
                "reviewer_provider": "dummy",
                "provider_stub_response": {
                    "memory_updates": [
                        {
                            "summary": f"Pi drain fact {index}",
                            "details": {"content": f"Content {index}.", "scope": "user"},
                        },
                    ],
                },
            }),
            encoding="utf-8",
        )
        stop_review(hook_payload=payload, state_dir=bucket)

    seen_batch_sizes = []

    class DrainBackend:
        def compile(self, batch, context, options):
            seen_batch_sizes.append(len(batch))
            suggestion = batch[0].suggestions[0]
            return backends.CompileArtifacts(
                memory_records={"user": [{"summary": suggestion.summary, "content": suggestion.details["content"]}], "global": []},
                recall_records=[],
                compiled_skills=[],
                manifest_entries=[],
                discarded_items=[],
                backend_name="agent:pi",
                compiler_observability={
                    "backend": "agent:pi",
                    "provider": "kimi",
                    "model": "kimi-k2.6",
                    "mode": "edit",
                    "duration_ms": 10,
                    "attempts": 1,
                },
            )

    monkeypatch.setattr(engine, "get_backend", lambda _: DrainBackend())

    result = scan_all_projects(
        home=tmp_path,
        backend="agent:pi",
        batch_size=5,
        compile_options={"pi_mode": "edit"},
        max_runs_per_project=3,
    )

    entry = result["results"][0]
    assert entry["processed_count"] == 3
    assert len(entry["runs"]) == 3
    assert seen_batch_sizes == [1, 1, 1]
    assert entry["drain_limit_reached"] is True
    assert entry["terminal_preflight_status"] == "run"
    assert len(list((bucket / "suggestions" / "pending").glob("*.json"))) == 1
    assert result["aggregate"]["total_compile_runs"] == 3
    assert result["aggregate"]["total_agent_attempts"] == 3
    assert result["aggregate"]["total_compile_duration_ms"] == 30


def test_run_compile_splits_failed_agent_batch_and_salvages_items(tmp_path, monkeypatch):
    bucket = _seed_bucket_with_pending(tmp_path, "-fake-agent-split")
    second_payload = bucket / "test-payload-2.json"
    second_payload.write_text(
        json.dumps({
            "thread_id": "thread-split-2",
            "turn_id": "turn-2",
            "cwd": "/tmp/fake-agent-split",
            "transcript": "second seeded item",
            "thread_read_output": "ctx",
            "reviewer_provider": "dummy",
            "provider_stub_response": {
                "memory_updates": [
                    {"summary": "Second fact", "details": {"content": "More stable content.", "scope": "user"}},
                ],
            },
        }),
        encoding="utf-8",
    )
    stop_review(hook_payload=second_payload, state_dir=bucket)

    class SplitBackend:
        def compile(self, batch, context, options):
            if len(batch) > 1:
                raise RuntimeError("batch collapsed")
            suggestion = batch[0].suggestions[0]
            return backends.CompileArtifacts(
                memory_records={"user": [{"summary": suggestion.summary, "content": suggestion.details["content"]}], "global": []},
                recall_records=[],
                compiled_skills=[],
                manifest_entries=[],
                discarded_items=[],
                backend_name="agent:opencode",
            )

    monkeypatch.setattr(engine, "get_backend", lambda _: SplitBackend())

    result = engine.run_compile(state_dir=bucket, backend="agent:opencode", batch_size=2)

    assert result["status"] == "success"
    assert result["processed_count"] == 2
    assert not list((bucket / "suggestions" / "processing").glob("*.json"))
    assert len(list((bucket / "suggestions" / "done").glob("*.json"))) == 2
    receipt = json.loads((bucket / "compiler" / "last_receipt.json").read_text(encoding="utf-8"))
    assert receipt["item_receipts"][0]["state"] == "batch_split"
    assert all(
        item.get("split_from_batch")
        for item in receipt["item_receipts"]
        if item.get("state") == "done"
    )


def test_run_compile_finalizes_empty_envelope_without_backend(tmp_path, monkeypatch):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    envelope = SuggestionEnvelope(
        schema_version=1,
        suggestion_id="empty-1",
        idempotency_key="empty-idem-1",
        thread_id="thread-empty",
        cwd=str(tmp_path / "repo"),
        repo_fingerprint="fp-empty",
        reviewer_timestamp="2026-05-07T00:00:00Z",
        suggestions=[],
        source_authority=[],
    )
    atomic_write_json(paths.suggestions_pending_dir / "empty-1.json", envelope.to_dict())

    class ShouldNotRunBackend:
        def compile(self, batch, context, options):
            raise AssertionError("empty envelopes should not invoke the agent compiler")

    monkeypatch.setattr(engine, "get_backend", lambda _: ShouldNotRunBackend())

    result = engine.run_compile(state_dir=paths.state_dir, backend="agent:opencode")

    assert result["status"] == "success"
    assert result["processed_count"] == 1
    assert not list(paths.suggestions_processing_dir.glob("*.json"))
    done = list(paths.suggestions_done_dir.glob("*.json"))
    assert len(done) == 1
    receipt = json.loads((paths.compiler_dir / "last_receipt.json").read_text(encoding="utf-8"))
    assert receipt["run_status"] == "success"
    assert receipt["skip_reason"] == "no_suggestions"
    assert receipt["item_receipts"] == [
        {
            "suggestion_id": "empty-1",
            "state": "done",
            "path": str(done[0]),
            "reason": "no_suggestions",
        }
    ]


def test_run_compile_reclaims_stale_processing_envelope(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    envelope = SuggestionEnvelope(
        schema_version=1,
        suggestion_id="stale-1",
        idempotency_key="stale-idem-1",
        thread_id="thread-stale",
        cwd=str(tmp_path / "repo"),
        repo_fingerprint="fp-stale",
        reviewer_timestamp="2026-05-07T00:00:00Z",
        suggestions=[
            Suggestion(
                family="memory_updates",
                summary="stale processing fact",
                details={"content": "Stable content from reclaimed processing.", "scope": "user"},
            )
        ],
        source_authority=[],
        state="processing",
        attempt_count=1,
    )
    processing_path = paths.suggestions_processing_dir / "stale-1.json"
    atomic_write_json(processing_path, envelope.to_dict())
    stale_time = time.time() - DEFAULT_LOCK_STALE_SECONDS - 5
    os.utime(processing_path, (stale_time, stale_time))

    preflight = engine.preflight_compile(state_dir=paths.state_dir)
    result = engine.run_compile(state_dir=paths.state_dir, backend="script")

    assert preflight["status"] == "run"
    assert preflight["stale_processing"] == 1
    assert result["status"] == "success"
    assert result["processed_count"] == 1
    assert not list(paths.suggestions_processing_dir.glob("*.json"))
    assert len(list(paths.suggestions_done_dir.glob("*.json"))) == 1
    done_payload = json.loads(next(paths.suggestions_done_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert done_payload["transition_log"][-2]["reason"] == "reclaimed_stale"


# ---------- CLI wiring ----------


def test_cli_scan_subcommand_prints_summary_json(tmp_path, capsys):
    _seed_bucket_with_pending(tmp_path, "-fake-cli-test")

    exit_code = cli.main(["scan", "--home", str(tmp_path), "--backend", "script"])
    assert exit_code == 0

    # CLI output is pretty-printed JSON we can parse. Schedulers / status
    # commands rely on this being stable JSON — don't let someone add a
    # "human mode" that corrupts the parse.
    out = json.loads(capsys.readouterr().out)
    assert out["total_projects"] == 1
    assert out["counts"]["run"] == 1
    assert out["results"][0]["project"] == "-fake-cli-test"


def test_cli_scan_default_backend_is_agent_pi():
    # Regression guard: if someone "fixes" the default back to script
    # to make CI faster, users who install the scheduler lose the whole
    # point of the pi agent backend for unattended runs. Agent failures should
    # stay visible as failed compile attempts, not silently synthesize through
    # script.
    parser = cli.build_parser()
    args = parser.parse_args(["scan"])
    assert args.backend == "agent:pi"
    assert args.max_runs_per_project == 3


def test_cli_scan_accepts_max_runs_per_project():
    parser = cli.build_parser()
    args = parser.parse_args(["scan", "--max-runs-per-project", "5"])
    assert args.max_runs_per_project == 5
