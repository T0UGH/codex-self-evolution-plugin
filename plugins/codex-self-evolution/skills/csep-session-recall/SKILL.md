---
name: csep-session-recall
description: Use CSEP session recall as grep-like search over past Codex sessions. Trigger when local repo/workspace work may depend on prior discussion, commands, errors, decisions, or user wording.
---

# CSEP Session Recall

Use `csep recall` like `rg` over past sessions.

## When To Use

Use this skill when the current task may depend on prior local context:

- a previous design decision
- a command or test result that was already run
- a repeated error or gotcha
- exact user wording from an earlier session
- a repo-specific workflow or status handoff

Skip it for self-contained tasks such as simple formatting, translation, arithmetic, or an edit where the user supplied all needed context.

## Query Style

Good recall queries are needles, not questions.

Prefer:

- file names: `MEMORY.md`, `hooks.json`, `session_recall`
- commands: `csep status`, `recall bootstrap`, `python3.11 -m pytest`
- errors: `missing transcript_path`, `ModuleNotFoundError`
- symbols: `messages_fts`, `build_focused_recall`, `review_memory`
- exact user wording: `二级引用`, `保持简单`, `不要 LLM`
- alternatives: `MEMORY.md|refs|二级引用`

Avoid long natural-language questions.

Bad:

```bash
csep recall "what did we decide about no LLM extractive session-level retrieval design agreement"
```

Good:

```bash
csep recall "LLM|extractive|session-level|retrieval"
csep recall "summary需要llm|不要llm|Hermes"
```

## Workflow

1. Extract 2-6 concrete needles from the current task.
2. Search repo scope first:

   ```bash
   csep recall "needle1|needle2|needle3" --cwd "$PWD"
   ```

3. If `no_match`, do not assume history does not exist. Try shorter or more exact needles.
4. Use `--recent` when the task is about recent work rather than a named topic:

   ```bash
   csep recall --recent --cwd "$PWD"
   ```

5. Use `--global` only when repo-local history is insufficient and cross-repo noise is acceptable:

   ```bash
   csep recall "needle1|needle2" --global
   ```

6. Treat matches as evidence. Summarize and decide yourself; recall does not answer the question for you.

## Output Handling

- Prefer Markdown output for interactive work.
- Use `--format json` when another script or tool needs structured results.
- Preserve provenance when citing a result: session time, source path, and the matched original text.
- Do not report empty recall separately unless the user is debugging recall itself.
