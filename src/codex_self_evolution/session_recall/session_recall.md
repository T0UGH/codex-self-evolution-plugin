# Session Recall

The runtime recall guide now lives in the CSEP plugin skill:

```text
skills/csep-session-recall/SKILL.md
```

`SessionStart` does not inject this file. It only injects stable background plus a short pointer to the plugin skill.

Use recall like `rg` over past sessions:

```bash
csep recall "MEMORY.md|refs|二级引用" --cwd "$PWD"
csep recall "summary需要llm|不要llm|Hermes" --cwd "$PWD"
csep recall --recent --cwd "$PWD"
```

Search short concrete needles, not long natural-language questions.
