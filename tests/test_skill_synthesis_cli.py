from __future__ import annotations

import json

from codex_self_evolution import cli


def test_skill_synthesize_cli_prints_result(monkeypatch, capsys, tmp_path):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "dry_run": kwargs["dry_run"]}

    monkeypatch.setattr(cli, "run_skill_synthesis", fake_run)

    exit_code = cli.main([
        "skill-synthesize",
        "--home",
        str(tmp_path),
        "--mode",
        "full",
        "--lookback-days",
        "30",
        "--dry-run",
    ])

    assert exit_code == 0
    assert captured["home"] == str(tmp_path)
    assert captured["mode"] == "full"
    assert captured["lookback_days"] == 30
    assert captured["dry_run"] is True
    assert json.loads(capsys.readouterr().out)["status"] == "success"


def test_skill_synthesize_cli_defaults_to_config_mode(monkeypatch, capsys):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "skip_unconfigured"}

    monkeypatch.setattr(cli, "run_skill_synthesis", fake_run)

    exit_code = cli.main(["skill-synthesize"])

    assert exit_code == 0
    assert captured["mode"] is None
    assert captured["lookback_hours"] is None
    assert captured["lookback_days"] is None
