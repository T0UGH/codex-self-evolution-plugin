"""Status diagnostic: must be read-only, fault-tolerant, and never leak secrets.

These tests cover the four invariants that make ``status`` useful as a
"did my install work?" / "is it doing anything?" command:

1. **Never leak env values** — .env.provider parsing reports key names
   only. A regression here could print API keys into logs.
2. **Never crash** — a missing hooks.json, missing home dir, unavailable
   CLI, or broken launchctl must each surface as a typed error/flag,
   not an unhandled exception.
3. **Count accuracy** — pending/done/failed counts drive the user's
   mental model of "is the pipeline moving?". Off-by-one here misleads.
4. **Subprocess isolation** — each external probe (launchctl, codex,
   opencode) runs independently so one hang doesn't wedge the rest.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codex_self_evolution import cli, diagnostics
from codex_self_evolution.config import PROJECTS_SUBDIR
from codex_self_evolution.diagnostics import (
    HOOK_MARKER,
    LAUNCHD_LABEL,
    _check_env_provider,
    _check_hooks,
    _check_scheduler,
    _check_tools,
    _inspect_bucket,
    _read_last_receipt,
    collect_status,
)


# ---------- env_provider parsing (the "never leak secrets" contract) ----


def test_env_provider_reports_key_names_never_values(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env.provider").write_text(
        "# Comment line\n"
        "MINIMAX_API_KEY=sk-real-secret-value-MUST-NOT-APPEAR\n"
        "OPENAI_API_KEY=\n"          # empty → counts as unset
        "ANTHROPIC_API_KEY=some-val\n"
        "MINIMAX_REGION=global\n"    # non-well-known key
        "\n",
        encoding="utf-8",
    )
    result = _check_env_provider(home)

    assert "sk-real-secret-value-MUST-NOT-APPEAR" not in json.dumps(result)
    assert "some-val" not in json.dumps(result)
    assert "MINIMAX_API_KEY" in result["keys_set"]
    assert "ANTHROPIC_API_KEY" in result["keys_set"]
    assert "OPENAI_API_KEY" in result["keys_unset"]
    assert "MINIMAX_REGION" in result["other_keys_set"]


def test_env_provider_strips_quotes_before_emptiness_check(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env.provider").write_text(
        'MINIMAX_API_KEY="actually-set"\n'
        "OPENAI_API_KEY=''\n"  # empty-string inside quotes → unset
        "ANTHROPIC_API_KEY=\"  \"\n",  # whitespace inside quotes → unset
        encoding="utf-8",
    )
    result = _check_env_provider(home)
    assert "MINIMAX_API_KEY" in result["keys_set"]
    assert "OPENAI_API_KEY" in result["keys_unset"]
    assert "ANTHROPIC_API_KEY" in result["keys_unset"]


def test_env_provider_handles_export_prefix(tmp_path):
    # Some users run .env.provider through bash source + export; the parser
    # must recognize `export KEY=value` shell syntax too.
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env.provider").write_text(
        "export MINIMAX_API_KEY=shellstyle\n",
        encoding="utf-8",
    )
    result = _check_env_provider(home)
    assert "MINIMAX_API_KEY" in result["keys_set"]


def test_env_provider_missing_file_is_clean_report(tmp_path):
    result = _check_env_provider(tmp_path / "does-not-exist")
    assert result["exists"] is False
    assert result["keys_set"] == []
    # All well-known keys must appear in keys_unset so the user sees what
    # they need to set rather than having to memorize the list.
    assert set(result["keys_unset"]) == {
        "MINIMAX_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    }


# ---------- hooks probe --------------------------------------------------


def test_hooks_probe_detects_both_managed_entries(tmp_path, monkeypatch):
    # Fake $HOME so the real ~/.codex/hooks.json isn't touched.
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    # Write a hooks.json that looks like real install output, plus a
    # neighboring third-party hook that MUST be ignored (vibe-island etc).
    (fake_home / ".codex" / "hooks.json").write_text(json.dumps({
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "/other/tool/bridge"}]},
                {"hooks": [{"type": "command",
                            "command": f"bash -c ': {HOOK_MARKER}; exec stop'"}]},
            ],
            "SessionStart": [
                {"hooks": [{"type": "command",
                            "command": f"bash -c ': {HOOK_MARKER}; exec sstart'"}]},
            ],
        }
    }), encoding="utf-8")
    result = _check_hooks()
    assert result["stop_installed"] is True
    assert result["session_start_installed"] is True


def test_hooks_probe_reports_missing_file_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = _check_hooks()
    assert result["exists"] is False
    assert result["stop_installed"] is False
    assert result["session_start_installed"] is False
    assert result["error"] is None


def test_hooks_probe_tolerates_malformed_json(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".codex" / "hooks.json").write_text("{broken", encoding="utf-8")
    result = _check_hooks()
    # A garbage hooks.json must not crash status; it should surface the
    # parse error so the user knows to fix it.
    assert result["error"] is not None
    assert result["stop_installed"] is False


def test_hooks_probe_ignores_unmarked_entries(tmp_path, monkeypatch):
    # Stop hook exists with a marker-less command — must NOT be counted as
    # ours. Protects against "my colleague hand-edited a similar command
    # and the status said installed even though ours wasn't".
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".codex" / "hooks.json").write_text(json.dumps({
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "some/other/stop-handler"}]}],
        }
    }), encoding="utf-8")
    result = _check_hooks()
    assert result["stop_installed"] is False


# ---------- scheduler probe (launchctl isolation) -----------------------


def test_scheduler_detects_loaded_job(monkeypatch, tmp_path):
    # Fake the plist existing + launchctl list finding our label.
    monkeypatch.setenv("HOME", str(tmp_path))
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>", encoding="utf-8")

    def fake_run(argv, **_):
        class R:
            stdout = f"1234\t0\t{LAUNCHD_LABEL}\n"
            stderr = ""
            returncode = 0
        return R()

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: "/bin/launchctl")

    result = _check_scheduler()
    assert result["loaded"] is True
    assert result["plist_exists"] is True
    assert result["error"] is None


def test_scheduler_reports_not_loaded_when_launchctl_silent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def fake_run(argv, **_):
        class R:
            stdout = ""
            stderr = ""
            returncode = 0
        return R()

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: "/bin/launchctl")
    result = _check_scheduler()
    assert result["loaded"] is False
    assert result["plist_exists"] is False


def test_scheduler_handles_non_macos_host(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    # No launchctl on Linux/CI — report honestly rather than "loaded=False"
    # (which would misleadingly suggest the user needs to run install-scheduler).
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    result = _check_scheduler()
    assert result["loaded"] is False
    assert "launchctl" in result["error"]


def test_scheduler_tolerates_launchctl_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: "/bin/launchctl")

    def fake_run(argv, **_):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=5)

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    result = _check_scheduler()
    # Whole status report must still be completable — timeout isolates to
    # this probe only.
    assert result["loaded"] is False
    assert "timed out" in result["error"].lower()


# ---------- tool version probe ------------------------------------------


def test_tools_probe_handles_missing_binary(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    result = _check_tools()
    assert result["codex"]["available"] is False
    assert result["opencode"]["available"] is False


def test_tools_probe_grabs_first_line_of_version_output(monkeypatch):
    def fake_which(binary):
        return f"/fake/{binary}"

    def fake_run(argv, **_):
        class R:
            stdout = "opencode\n1.4.0\n"
            stderr = ""
            returncode = 0
        return R()

    monkeypatch.setattr(diagnostics.shutil, "which", fake_which)
    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    result = _check_tools()
    # Takes the first non-empty line — matches "codex-cli 0.122.0" shape
    # as well as the multi-line opencode banner.
    assert result["codex"]["version"] == "opencode"
    assert result["opencode"]["available"] is True


# ---------- bucket inspection -------------------------------------------


def test_inspect_bucket_counts_only_json_files(tmp_path):
    bucket = tmp_path / "-Users-alice-repo"
    (bucket / "suggestions" / "pending").mkdir(parents=True)
    (bucket / "suggestions" / "done").mkdir(parents=True)
    # Valid suggestions
    (bucket / "suggestions" / "pending" / "a.json").write_text("{}")
    (bucket / "suggestions" / "pending" / "b.json").write_text("{}")
    (bucket / "suggestions" / "done" / "c.json").write_text("{}")
    # Noise that must NOT be counted
    (bucket / "suggestions" / "pending" / "README.txt").write_text("notes")
    (bucket / "suggestions" / "pending" / ".DS_Store").write_text("mac junk")

    result = _inspect_bucket(bucket)
    assert result["counts"]["pending"] == 2
    assert result["counts"]["done"] == 1
    # Directories that don't exist yet must report 0, not KeyError
    assert result["counts"]["failed"] == 0
    assert result["counts"]["discarded"] == 0
    assert result["last_receipt"] is None


def test_inspect_bucket_returns_last_receipt_summary(tmp_path):
    bucket = tmp_path / "-repo"
    (bucket / "compiler").mkdir(parents=True)
    (bucket / "compiler" / "last_receipt.json").write_text(json.dumps({
        "run_status": "success",
        "backend": "agent:opencode",
        "fallback_backend": None,
        "processed_count": 7,
        "skip_reason": None,
        "memory_records": 2,
        "item_receipts": [{"full": "detail not surfaced by default"}],
    }), encoding="utf-8")
    result = _inspect_bucket(bucket)
    assert result["last_receipt"]["run_status"] == "success"
    assert result["last_receipt"]["processed_count"] == 7
    # item_receipts deliberately NOT in the summary output — they can be
    # large and contain absolute paths. Users who need them read the file.
    assert "item_receipts" not in result["last_receipt"]


def test_read_last_receipt_handles_corrupt_file(tmp_path):
    receipt = tmp_path / "last_receipt.json"
    receipt.write_text("not json", encoding="utf-8")
    assert _read_last_receipt(receipt) is None


# ---------- collect_status end-to-end -----------------------------------


def test_collect_status_runs_cleanly_with_no_home(monkeypatch, tmp_path):
    # Fresh machine: home doesn't exist, hooks.json doesn't exist,
    # launchctl doesn't find anything. Nothing must raise.
    monkeypatch.setenv("HOME", str(tmp_path / "freshly-minted"))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)

    result = collect_status(home=tmp_path / "does-not-exist")
    # Must be fully JSON-serializable — status is piped to logs / jq.
    json.dumps(result)
    assert result["buckets"] == []
    assert result["hooks"]["exists"] is False
    assert result["env_provider"]["exists"] is False


def test_cli_status_outputs_valid_json(tmp_path, capsys, monkeypatch):
    # Make every external probe deterministic so CI can assert on content.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)

    exit_code = cli.main(["status", "--home", str(tmp_path)])
    assert exit_code == 0

    out = capsys.readouterr().out
    parsed = json.loads(out)
    # Required top-level sections — if someone renames/removes one, status
    # consumers (future monitoring scripts / install-verify CI) break silently.
    for section in ("timestamp", "home", "hooks", "scheduler", "env_provider", "tools", "buckets"):
        assert section in parsed
