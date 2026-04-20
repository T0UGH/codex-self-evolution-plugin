import json

from codex_self_evolution.compiler.engine import apply_compiler_outputs, write_receipt
from codex_self_evolution.schemas import CompilerReceipt, RecallRecord, SkillManifestEntry



def test_compiler_engine_owns_final_asset_writes(tmp_path):
    paths = apply_compiler_outputs(
        memory_dir=tmp_path / "memory",
        recall_dir=tmp_path / "recall",
        skills_dir=tmp_path / "skills",
        memory_records={
            "user": [{"summary": "User pref", "content": "Be concise."}],
            "global": [{"summary": "Repo fact", "content": "Run focused tests first."}],
        },
        recall_records=[RecallRecord(id="1", summary="S", content="C", source_paths=["a"], repo_fingerprint="r", cwd="/tmp")],
        compiled_skills=[{"skill_id": "alpha", "title": "Alpha", "content": "Do alpha tasks.", "action": "create"}],
        manifest_entries=[SkillManifestEntry(skill_id="alpha", action="create", title="Alpha", path="skills/managed/alpha.md", status="active", owner="codex-self-evolution-plugin", managed=True, created_by="codex-self-evolution-plugin", updated_at="2026-01-01T00:00:00Z")],
        existing_entries=[],
    )
    receipt_path = write_receipt(tmp_path / "compiler", CompilerReceipt("success", "script", 1, 1, 1, 1, 1))

    assert paths["memory"][0].exists()
    assert paths["memory"][1].exists()
    assert paths["memory"][2].exists()
    assert "Be concise." in paths["memory"][0].read_text(encoding="utf-8")
    assert "Run focused tests first." in paths["memory"][1].read_text(encoding="utf-8")
    assert json.loads(paths["memory"][2].read_text(encoding="utf-8"))["user"][0]["summary"] == "User pref"
    assert paths["recall"][0].exists()
    assert paths["recall"][1].exists()
    assert paths["skills"][0][0].exists()
    assert paths["skills"][1].exists()
    assert paths["skills"][0][0].parent.name == "managed"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["processed_count"] == 1
