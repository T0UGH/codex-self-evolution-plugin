import json
import os
import plistlib
import subprocess

from codex_self_evolution.compiler.engine import preflight_compile, run_compile
from codex_self_evolution.config import build_paths
from codex_self_evolution.storage import atomic_write_json, compiler_lock_path, utc_now


def _write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_scheduler_plist_uses_local_cli_not_uvx(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_cli = fake_bin / "codex-self-evolution"
    _write_executable(local_cli, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "uvx", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "opencode", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "launchctl", "#!/usr/bin/env bash\nexit 0\n")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    subprocess.run(["bash", "scripts/install-scheduler.sh"], check=True)

    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / "com.codex-self-evolution.preflight.plist"
    )
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["ProgramArguments"] == [
        str(local_cli),
        "scan",
        "--backend",
        "agent:opencode",
    ]
    assert str(fake_bin) in plist["EnvironmentVariables"]["PATH"].split(os.pathsep)
    assert "uvx" not in json.dumps(plist)


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
