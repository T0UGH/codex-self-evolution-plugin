from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import SKILL_SYNTHESIS_SUBDIR, get_home_dir
from ..managed_skills.publish import codex_skills_dir


@dataclass(frozen=True)
class SkillSynthesisPaths:
    home: Path
    root: Path
    lock_path: Path
    last_receipt_path: Path
    evidence_index_path: Path
    published_index_path: Path
    receipts_dir: Path
    runs_dir: Path
    discarded_dir: Path
    run_dir: Path
    run_input_dir: Path
    run_output_dir: Path


def build_skill_synthesis_paths(home: str | Path | None = None, run_id: str = "") -> SkillSynthesisPaths:
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    root = home_dir / SKILL_SYNTHESIS_SUBDIR
    run_dir = root / "runs" / run_id if run_id else root / "runs"
    return SkillSynthesisPaths(
        home=home_dir,
        root=root,
        lock_path=root / "lock",
        last_receipt_path=root / "last_receipt.json",
        evidence_index_path=root / "evidence_index.json",
        published_index_path=root / "published_index.json",
        receipts_dir=root / "receipts",
        runs_dir=root / "runs",
        discarded_dir=root / "discarded",
        run_dir=run_dir,
        run_input_dir=run_dir / "input",
        run_output_dir=run_dir / "output",
    )


def resolve_real_skills_root(override: str | Path | None = None) -> Path:
    return codex_skills_dir(override)
