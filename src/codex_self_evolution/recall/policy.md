Recall is available as an on-demand tool.

Codex should decide whether recall is needed. For repo/workspace-related non-trivial tasks that could depend on prior local context, repeated workflow guidance, or previously promoted patterns, run focused recall before answering.

Command:

`csep recall "<focused query>"`

If `csep` is not on PATH, use:

`uvx --from codex-self-evolution-plugin csep recall "<focused query>"`

Generate the focused query yourself. Prefer a concrete topic over the user's vague continuation wording.

Hard-skip recall for clearly self-contained turns only: simple arithmetic, one-sentence translation or rewrite, trivial formatting, or a pure current-file edit where the user supplied all needed context.

Ranking:

1. Same repo fingerprint
2. Same cwd subtree
3. Global fallback

Do not run recall automatically at session start. Run it in-turn only when the contract above applies.

If recall has no match or fails, continue with the current repo and conversation context without inventing prior context.
