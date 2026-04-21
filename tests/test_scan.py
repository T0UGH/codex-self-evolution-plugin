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
from pathlib import Path

import pytest

from codex_self_evolution import cli
from codex_self_evolution.compiler import engine
from codex_self_evolution.compiler.engine import scan_all_projects
from codex_self_evolution.config import PROJECTS_SUBDIR
from codex_self_evolution.hooks.stop_review import stop_review


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


def test_cli_scan_default_backend_is_agent_opencode():
    # Regression guard: if someone "fixes" the default back to script
    # to make CI faster, users who install the scheduler lose the whole
    # point of agent backend for unattended runs. Agent falls back to
    # script automatically if opencode missing — the default being
    # agent:opencode is intentional.
    parser = cli.build_parser()
    args = parser.parse_args(["scan"])
    assert args.backend == "agent:opencode"
