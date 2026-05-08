from pathlib import Path

from codex_self_evolution import cli
from codex_self_evolution.compiler.replay import evaluate_compiler_fixture


FIXTURES = Path(__file__).parent / "fixtures" / "compiler_replay"


def test_compiler_replay_fixtures_pass_script_baseline():
    results = [
        evaluate_compiler_fixture(FIXTURES / "commerce_membership_api.json", backend="script"),
        evaluate_compiler_fixture(FIXTURES / "luna_tp.json", backend="script"),
    ]

    assert [result["status"] for result in results] == ["pass", "pass"]
    commerce, luna_tp = results
    assert commerce["metrics"]["memory_records"] >= 3
    assert commerce["metrics"]["discard_reasons"]["missing_reuse_trigger"] == 1
    assert luna_tp["metrics"]["recall_records"] == 1


def test_cli_eval_compiler_prints_replay_metrics(capsys):
    exit_code = cli.main([
        "eval-compiler",
        "--fixture",
        str(FIXTURES / "commerce_membership_api.json"),
        "--backend",
        "script",
    ])

    assert exit_code == 0
    assert '"status": "pass"' in capsys.readouterr().out
