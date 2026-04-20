# Session Recall Skill

Use recall only when the current turn signals a need for prior context.

## Trigger cues
- The user references previous work, decisions, or workflows.
- The query contains markers like `remember`, `previous`, `again`, `before`.
- The turn needs repo-specific historical context to continue correctly.

## Retrieval order
1. same-repo results first
2. same-cwd subtree results second
3. global fallback only when local context is insufficient

## Behavior
- Do not preload large recall material at session start.
- Trigger recall in-turn when cues are present.
- Return focused recall, not full history dumps.
- Preserve provenance in results.
