from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path


def _write_executable(path: Path, text: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_skill_synthesis_scheduler_plist(tmp_path: Path, monkeypatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_cli = fake_bin / "codex-self-evolution"
    _write_executable(local_cli)
    _write_executable(fake_bin / "pi")
    _write_executable(fake_bin / "launchctl")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    subprocess.run(["bash", "scripts/install-skill-synthesis-scheduler.sh"], check=True)

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.codex-self-evolution.skill-synthesis.plist"
    plist = plistlib.loads(plist_path.read_bytes())

    assert plist["Label"] == "com.codex-self-evolution.skill-synthesis"
    assert plist["ProgramArguments"] == [
        str(local_cli),
        "skill-synthesize",
        "--mode",
        "incremental",
        "--lookback-hours",
        "24",
    ]
    assert plist["StartInterval"] == 14400
    assert plist["RunAtLoad"] is False
    assert "skill-synthesis.launchd.stdout.log" in plist["StandardOutPath"]
    assert "uvx" not in json.dumps(plist)
