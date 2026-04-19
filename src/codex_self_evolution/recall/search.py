from __future__ import annotations

import json
from pathlib import Path

from ..config import build_paths
from ..schemas import RecallRecord
from ..storage import repo_fingerprint


def load_recall_records(state_dir: str | Path | None = None, repo_root: str | Path | None = None) -> list[RecallRecord]:
    paths = build_paths(repo_root=repo_root, state_dir=state_dir)
    index_path = paths.recall_dir / "index.json"
    if not index_path.exists():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return [RecallRecord.from_dict(item) for item in payload.get("records", [])]


def search_recall(query: str, cwd: str | Path, state_dir: str | Path | None = None) -> list[dict]:
    resolved_cwd = Path(cwd).resolve()
    records = load_recall_records(state_dir=state_dir, repo_root=resolved_cwd)
    fingerprint = repo_fingerprint(resolved_cwd)
    query_terms = {term.lower() for term in query.split() if term.strip()}

    def score(record: RecallRecord) -> tuple[int, int, int]:
        text = f"{record.summary} {record.content}".lower()
        term_hits = sum(1 for term in query_terms if term in text)
        same_repo = 1 if record.repo_fingerprint == fingerprint else 0
        same_cwd = 1 if str(record.cwd).startswith(str(resolved_cwd)) or str(resolved_cwd).startswith(str(record.cwd)) else 0
        return (same_repo * 10 + same_cwd * 5 + term_hits, same_repo, term_hits)

    ranked = sorted(records, key=score, reverse=True)
    return [
        {
            "id": record.id,
            "summary": record.summary,
            "content": record.content,
            "source_paths": record.source_paths,
            "repo_fingerprint": record.repo_fingerprint,
            "cwd": record.cwd,
            "thread_id": record.thread_id,
            "turn_id": record.turn_id,
        }
        for record in ranked
        if not query_terms or score(record)[0] > 0
    ]
