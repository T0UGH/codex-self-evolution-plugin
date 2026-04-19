import json

from codex_self_evolution.schemas import CompilerReceipt, RecallRecord, SkillManifestEntry
from codex_self_evolution.writer import write_memory, write_recall, write_receipt, write_skills



def test_writer_owns_final_asset_writes(tmp_path):
    memory_paths = write_memory(
        tmp_path / "memory",
        {
            "user": [{"summary": "User pref", "content": "Be concise."}],
            "global": [{"summary": "Repo fact", "content": "Run focused tests first."}],
        },
    )
    recall_paths = write_recall(
        tmp_path / "recall",
        [RecallRecord(id="1", summary="S", content="C", source_paths=["a"], repo_fingerprint="r", cwd="/tmp")],
    )
    skill_paths = write_skills(
        tmp_path / "skills",
        [{"skill_id": "alpha", "title": "Alpha", "content": "Do alpha tasks.", "action": "create"}],
        [SkillManifestEntry(skill_id="alpha", action="create", title="Alpha", path="skills/managed/alpha.md", status="active", owner="codex-self-evolution-plugin", managed=True, created_by="codex-self-evolution-plugin", updated_at="2026-01-01T00:00:00Z")],
    )
    receipt_path = write_receipt(tmp_path / "compiler", CompilerReceipt("success", "script", 1, 1, 1, 1, 1))

    assert memory_paths[0].exists()
    assert memory_paths[1].exists()
    assert memory_paths[2].exists()
    assert "Be concise." in memory_paths[0].read_text(encoding="utf-8")
    assert "Run focused tests first." in memory_paths[1].read_text(encoding="utf-8")
    assert json.loads(memory_paths[2].read_text(encoding="utf-8"))["user"][0]["summary"] == "User pref"
    assert recall_paths[0].exists()
    assert recall_paths[1].exists()
    assert skill_paths[0][0].exists()
    assert skill_paths[1].exists()
    assert skill_paths[0][0].parent.name == "managed"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["processed_count"] == 1
