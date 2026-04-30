# Session Recall Skill

Use recall as a model-initiated self-check, not as an external rule router.

## Recall Contract

Before answering, you MUST run focused recall when the task is repo/workspace related, non-trivial, and could depend on prior local context, repeated workflow guidance, or previously promoted patterns.

Hard-skip recall for clearly self-contained turns only:

- simple arithmetic
- one-sentence translation or rewrite
- trivial formatting
- a pure current-file edit where the user supplied all needed context

## Command

Generate one focused query that says what prior context you need, then run:

`csep recall "<focused query>"`

If `csep` is not on PATH, fall back to:

`uvx --from codex-self-evolution-plugin csep recall "<focused query>"`

Use the user's wording plus the current repo/task shape to form the focused query. Do not pass vague phrases like "continue this" when you can name the concrete topic.

## Retrieval order
1. same-repo results first
2. same-cwd subtree results second
3. global fallback only when local context is insufficient

## Behavior
- Do not preload large recall material at session start.
- If recall returns matches that affect the answer, use them naturally.
- If recall returns no matches or fails, continue with the current repo and conversation context. Do not invent prior context.
- Do not announce empty recall separately unless the user asks.
- Preserve provenance in results.
