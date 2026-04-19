Recall is available as an on-demand tool.

Use recall only when the current turn would benefit from prior repo-specific context, repeated workflow guidance, or previously promoted patterns.

Command:

`python -m codex_self_evolution.cli recall --query "<query>" --cwd "<cwd>"`

Ranking:

1. Same repo fingerprint
2. Same cwd subtree
3. Global fallback

Do not run recall automatically at session start.
