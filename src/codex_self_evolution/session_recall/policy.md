# CSEP Session Recall Policy

<IMPORTANT>
Before answering or taking action, check whether this task may depend on prior local session context.

If there is a reasonable chance that previous Codex or Claude Code sessions contain useful evidence, you MUST use the `csep-session-recall` plugin skill before proceeding.

Do not rely only on the stable background injected at startup. This policy does not install or rely on UserPromptSubmit automatic recall; the agent must actively choose the recall skill. When unsure, use recall.
</IMPORTANT>

## Use Recall For

- today's, recent, previous, unfinished, or continued work
- status, handoff, cleanup, wrap-up, or "what remains"
- bugs, failures, repeated issues, regressions, logs, or test output
- previous commands, branches, commits, reviews, hooks, skills, local tools, or config state
- exact user wording from earlier conversations
- anything mentioning `recall`, memory, session history, CSEP, hooks, or skills

Chinese trigger examples: `今天`, `最近`, `刚才`, `之前`, `上次`, `继续`, `接着`, `状态`, `运行状态`, `交接`, `收尾`, `还剩什么`, `又报错`, `怎么又`, `查一下`, `看看`, `本地`, `hook`, `skill`, `recall`.

## Skip Recall Only When

Skip recall only when one of these boundaries clearly applies:

- the task is clearly self-contained, such as translation, formatting, arithmetic, or a tiny edit with complete context
- the user explicitly says not to use recall or not to check history

## Query Style

Treat `csep recall` like `rg` over past sessions. Extract 2-6 short concrete needles: file names, commands, errors, symbols, hook names, config keys, branch names, or exact user wording.

Prefer:

```bash
csep recall "hooks.json|SessionStart|Recall Policy" --cwd "$PWD"
csep recall "missing transcript_path|discovery_ambiguous" --cwd "$PWD"
csep recall --recent --cwd "$PWD"
```

Avoid long natural-language questions. Use recall results as evidence, not authority; current files, live command output, and explicit user instructions take precedence.
