import json

from codex_self_evolution.compiler.engine import preflight_compile, run_compile
from codex_self_evolution.config import build_paths
from codex_self_evolution.storage import atomic_write_json, compiler_lock_path, utc_now



def test_compile_preflight_skips_empty_and_locked(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    assert preflight_compile(repo_root=repo, state_dir=state)["status"] == "skip_empty"
    paths = build_paths(repo_root=repo, state_dir=state)
    atomic_write_json(
        compiler_lock_path(paths),
        {"created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"), "pid": 1},
    )
    assert preflight_compile(repo_root=repo, state_dir=state)["status"] == "skip_locked"



def test_run_compile_writes_skip_receipt_for_empty_queue(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    result = run_compile(repo_root=repo, state_dir=state)
    receipt = json.loads((state / "compiler" / "last_receipt.json").read_text(encoding="utf-8"))
    assert result["status"] == "skip_empty"
    assert receipt["run_status"] == "skip_empty"
