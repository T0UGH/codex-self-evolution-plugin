# Session Recall Implementation Plan

日期：2026-05-16

## 1. Goal

把 `docs/design-session-recall.md` 落成可实施的工程计划。

目标结果：

- CSEP 插件自带 `csep-session-recall` skill，安装插件后进入 Codex agent harness。
- `SessionStart` 不再注入完整 recall contract / 操作手册。
- `csep recall` 支持 grep-like query：`|` OR alias、strict -> OR fallback、显式 `--all-terms`。
- Recall 输出 session-level extractive evidence window，不使用 LLM summary。
- 默认 evidence 展示降噪 `developer` / `system`，优先 `user` / `assistant` / `tool`。
- CLI 不自动 repo -> global fallback；跨 repo 由 agent 按 skill 显式 `--global`。

本计划只覆盖 Session Recall 线。Stable Memory 单文件模型和 `memory/refs/` 的实现另走 Stable Memory plan。

## 2. Architecture Summary

Recall 分成三层：

1. 插件 skill 层
   - 位置：CSEP plugin bundle 的 `skills/csep-session-recall/SKILL.md`
   - 职责：教 agent 把 recall 当作 `rg over past sessions` 使用。
   - 不负责执行检索，不进入 `SessionStart` 大段上下文。

2. CLI 查询层
   - 位置：`src/codex_self_evolution/csep.py`
   - 职责：解析 recall 参数，传递 query mode、window 参数和 scope 参数。
   - 默认 grep-like auto query，显式 `--all-terms` 才保持 AND。

3. Recall storage/render 层
   - 位置：`src/codex_self_evolution/session_recall/`
   - 职责：FTS 检索、session 聚合、排序、evidence window 抽取和 Markdown / JSON 渲染。
   - 不使用 LLM，不写 Memory，不自动跨 scope。

## 3. File / Module Decomposition

新增文件：

- `src/codex_self_evolution/plugin_bundle/skills/csep-session-recall/SKILL.md`
  - packaged plugin 内置 skill 源。
- `plugins/codex-self-evolution/skills/csep-session-recall/SKILL.md`
  - marketplace / repo plugin 副本。

修改文件：

- `src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json`
  - 增加 `"skills": "./skills/"`。
- `plugins/codex-self-evolution/.codex-plugin/plugin.json`
  - 与 package bundle manifest 保持一致。
- `pyproject.toml`
  - 将 plugin skill 文件加入 package data。
- `src/codex_self_evolution/hooks/session_start.py`
  - 移除完整 `session_recall/session_recall.md` 注入。
  - 保留 `MEMORY.md` 注入；最多保留一句短 recall skill 提示。
- `src/codex_self_evolution/session_recall/policy.md`
  - 缩短为轻量 recall availability / skill pointer，或完全不再注入。
- `src/codex_self_evolution/session_recall/session_recall.md`
  - 作为历史文档来源保留或改为指向 plugin skill；不再作为 SessionStart 注入内容。
- `src/codex_self_evolution/csep.py`
  - 增加 `--all-terms`、`--windows-per-session`、developer/system 展示控制 flag。
  - 将参数传给 workflow。
- `src/codex_self_evolution/session_recall/workflow.py`
  - 接收 query mode、windows per session、role display policy。
- `src/codex_self_evolution/session_recall/store.py`
  - 实现 query normalization：`|` OR alias、strict -> OR fallback、`--all-terms`。
  - 从单 best hit 扩展为每个 session 多 evidence window。
  - 保持 repo scope 不自动 fallback 到 global。
- `src/codex_self_evolution/session_recall/render.py`
  - 渲染多个 evidence windows。
  - 默认不优先展示 `developer` / `system`。
  - `no_match` 保持极简。

测试文件：

- `tests/test_plugin_bundle_hooks.py`
  - 覆盖 manifest `skills` 字段、skill 文件存在、source bundle 与 marketplace bundle 一致、package data 包含 skill。
- `tests/test_session_start.py`
  - 覆盖 SessionStart 不再注入完整 recall contract / session recall skill 正文。
- `tests/test_session_recall_cli.py`
  - 覆盖 CLI flags 和端到端 recall 行为。
- `tests/test_session_recall_store.py`
  - 覆盖 OR alias、fallback、AND、session windows、role 降噪、scope。
- `tests/test_session_recall_parser.py`
  - 如索引 `tool_calls` 时补解析覆盖。

## 4. Phase-by-Phase Implementation Tasks

### Phase 1: Ship Plugin Skill

Objective:

让 CSEP 插件安装后自带 `csep-session-recall` skill，并把 recall 使用心智从 SessionStart contract 迁移到 harness skill。

Files:

- `src/codex_self_evolution/plugin_bundle/skills/csep-session-recall/SKILL.md`
- `plugins/codex-self-evolution/skills/csep-session-recall/SKILL.md`
- `src/codex_self_evolution/plugin_bundle/.codex-plugin/plugin.json`
- `plugins/codex-self-evolution/.codex-plugin/plugin.json`
- `pyproject.toml`
- `tests/test_plugin_bundle_hooks.py`

Implementation notes:

- `SKILL.md` 使用 frontmatter，`name` 建议为 `csep-session-recall`。
- skill 内容写成操作手册：
  - Treat recall like `rg over past sessions`.
  - Search needles, not questions.
  - Use 2-6 concrete terms.
  - Use `|` for alternatives.
  - Repo scope first, `--global` only when needed.
  - `no_match` 后换更短 needle。
  - 命中后引用原文证据，由模型总结。
- 两份 plugin manifest 都增加 `"skills": "./skills/"`。
- `pyproject.toml` package data 增加 `plugin_bundle/skills/csep-session-recall/SKILL.md`。
- 测试断言 package bundle、repo plugin bundle 和 pyproject package data 三者一致。

Verification:

- `python3.11 -m pytest -q tests/test_plugin_bundle_hooks.py`
- `python3.11 -m build` 能把 skill 文件放入 wheel / sdist。

Exit criteria:

- 源包 plugin bundle 和 `plugins/codex-self-evolution` plugin bundle 都有 skill。
- manifest 明确声明 skills directory。
- 测试能防止后续只改一份 manifest 或漏打包 skill。

### Phase 2: Stop Injecting Recall Contract at SessionStart

Objective:

`SessionStart` 只负责注入 Memory 和极短 recall skill pointer，不再把完整 recall 操作手册塞进 `additionalContext`。

Files:

- `src/codex_self_evolution/hooks/session_start.py`
- `src/codex_self_evolution/session_recall/policy.md`
- `src/codex_self_evolution/session_recall/session_recall.md`
- `tests/test_session_start.py`
- `tests/test_session_start_codex_hook.py`

Implementation notes:

- 删除 `session_start()` 对 `session_recall/session_recall.md` 的读取。
- `combined_prefix` 不再包含 `## Recall Contract` 大段正文。
- `recall` 返回结构可以保留最小 metadata，但不再包含完整 skill content。
- `format_session_start_for_codex()` 只追加极短提示，例如：
  - `For historical session search, use the csep-session-recall skill.`
- 如果决定完全依赖 skill，则 `additionalContext` 中不出现 recall policy。
- 更新测试中旧断言：
  - 不再要求 `# Session Recall Skill` 出现在 combined prefix。
  - 断言 `additionalContext` 不包含长 recall contract。
  - 断言 memory 内容仍正常注入。

Verification:

- `python3.11 -m pytest -q tests/test_session_start.py tests/test_session_start_codex_hook.py`
- 手动运行一次 `csep session-start --from-stdin` fixture，确认输出 JSON valid 且上下文显著变短。

Exit criteria:

- `SessionStart` 不再承担 recall 教学。
- 现有 memory 注入行为不被破坏。
- Fresh install 没有 skill 时也不会阻断 SessionStart。

### Phase 3: Add Grep-Like Query Semantics

Objective:

让 `csep recall "a|b|c"` 成为有效 OR 查询，并让普通自然语言 query 在 strict no-match 后自动 fallback 到 OR。

Files:

- `src/codex_self_evolution/csep.py`
- `src/codex_self_evolution/session_recall/store.py`
- `src/codex_self_evolution/session_recall/workflow.py`
- `tests/test_session_recall_store.py`
- `tests/test_session_recall_cli.py`

Implementation notes:

- CLI 增加：
  - `--all-terms`：强制 AND / strict FTS。
- store 增加 query normalization helper：
  - `|` 转成 FTS `OR`，并对 token 做 FTS 安全转义。
  - 已包含显式 FTS operator、quoted phrase、prefix `*`、`NOT` 时尽量保留原意。
  - 默认先跑 strict query；如果 no-match 且不是 `--all-terms`，再跑 OR fallback。
- workflow payload 可记录 `query_mode` / `fallback_used`，但默认 Markdown 不必增加噪音。
- 保持失败软处理：FTS syntax error 返回 no-match，不抛给用户。

Verification:

- store 单测：
  - `alpha|beta` 命中任一词。
  - `alpha beta gamma` strict no-match 后 OR fallback 命中。
  - `--all-terms` 下 `alpha beta gamma` 不 fallback。
  - quoted phrase 和 `NOT` 仍按 FTS 语义工作。
- CLI 单测：
  - `csep recall "alpha|beta"` matched。
  - `csep recall "alpha beta gamma"` 在 fallback 后 matched。
  - `csep recall "alpha beta gamma" --all-terms` no_match。

Exit criteria:

- Agent 可以像写 `rg "a|b|c"` 一样写 recall query。
- 长自然语言 query 不再因为 FTS AND 默认语义大量空召回。
- 显式 AND 使用者仍有可控入口。

### Phase 4: Session-Level Evidence Windows

Objective:

Recall 默认按 session 返回 extractive evidence，而不是只围绕一个 best message 展示一段 window。

Files:

- `src/codex_self_evolution/csep.py`
- `src/codex_self_evolution/session_recall/store.py`
- `src/codex_self_evolution/session_recall/workflow.py`
- `src/codex_self_evolution/session_recall/render.py`
- `tests/test_session_recall_store.py`
- `tests/test_session_recall_cli.py`

Implementation notes:

- CLI 增加 `--windows-per-session`，默认 `2`。
- store 仍先按 message 搜索，再 group by session。
- 每个 session 选择最多 N 个 hit 作为 window anchor：
  - 优先 user / assistant / tool hit。
  - 避免相邻 anchors 生成高度重叠 window。
  - 多个 window 进入 result，例如 `windows: [{anchor, messages, matched}]`。
- 为兼容旧渲染，可以短期保留 top-level `messages`，但新渲染优先 `windows`。
- session score 由 best hit、hit_count、role、recency 组合。

Verification:

- 同一 session 中两个远距离 hit 时，返回两个 windows。
- 相邻 hit 被合并或只保留一个 window，避免重复输出。
- `--windows-per-session 1` 只输出一个 window。
- `--format json` 包含 windows 结构。
- Markdown 输出保持紧凑，不出现重 UI 卡片。

Exit criteria:

- Recall 结果以 session 为单位，有足够证据上下文。
- 默认输出不再是散点 message hit。
- 输出预算仍能截断并保持 valid Markdown / JSON。

### Phase 5: Role Noise Reduction and Recent Preview

Objective:

降低 Stable Background、DeveloperInstructions、Recall Contract 对 recall 输出的污染。

Files:

- `src/codex_self_evolution/session_recall/store.py`
- `src/codex_self_evolution/session_recall/render.py`
- `src/codex_self_evolution/csep.py`
- `tests/test_session_recall_store.py`
- `tests/test_session_recall_cli.py`

Implementation notes:

- `developer` / `system` 仍可进入索引，保证需要查底层上下文时能找。
- 默认 evidence anchor selection 降权或跳过 `developer` / `system`。
- CLI 增加显式 flag，例如：
  - `--include-system`
  - 或 `--include-background`
- `recent()` preview 改成第一条 `user` message。
- 如果 session 没有 user message，fallback 到 assistant / tool / first message。

Verification:

- 构造 developer 命中和 user 命中同词，默认展示 user window。
- 开启 include flag 后可以展示 developer/system。
- `csep recall --recent` preview 显示第一条 user 内容。
- 没有 user message 的 recent session 不崩溃。

Exit criteria:

- 默认 recall 输出不再优先展示系统/开发者背景。
- 调试场景仍能显式查到底层背景。
- 最近 session 列表更接近真实任务摘要。

### Phase 6: Index Tool Metadata and Prepare CJK Fallback

Objective:

补齐 Hermes-like retrieval 的非 LLM 部分，提升命令、工具调用和中英混合 query 的召回能力。

Files:

- `src/codex_self_evolution/session_recall/parser.py`
- `src/codex_self_evolution/session_recall/models.py`
- `src/codex_self_evolution/session_recall/store.py`
- `tests/test_session_recall_parser.py`
- `tests/test_session_recall_store.py`

Implementation notes:

- 确认 parser 已保留 `tool_name`；若 transcript 中有 `tool_calls`，增加轻量 extraction。
- FTS table 已索引 content / role / tool_name，可考虑增加 `tool_calls` column。
- 对 CJK / 短 query 增加 LIKE fallback：
  - 仅在 FTS no-match 后触发。
  - 限制 candidate 数量，避免全库慢扫。
- trigram 可以作为后续增强，不阻塞 P0/P1。

Verification:

- 搜工具名如 `exec_command` 能命中 tool message。
- 搜具体命令片段能命中。
- 搜短中文词如 `二级引用` 命中。
- 中英混合无空格 query 有 LIKE fallback 覆盖。

Exit criteria:

- 命令名、工具名、中文短词不再高度依赖自然分词。
- 不引入外部运行时依赖。
- 大库查询仍有明确 limit。

### Phase 7: Documentation and Status Alignment

Objective:

让 README / docs / status 与新 recall 行为一致，避免 agent 或用户继续按旧 contract 使用。

Files:

- `docs/design-session-recall.md`
- `docs/session-recall.md`
- `docs/status-2026-05-16.md`
- `README.md`
- `docs/getting-started.md`
- `docs/architecture.md`

Implementation notes:

- 文档统一使用 `rg over past sessions` 心智。
- 示例全部改成 needle / OR query。
- 移除“生成 focused natural language query”的旧说法。
- 明确 `no_match` 简单、`--global` 显式、no LLM summary。
- README 只保留用户可见操作，不塞完整设计细节。

Verification:

- `rg -n "focused query|Recall Contract|自然语言 query|no_match 自救|USER.md" docs README.md`
- `git diff --check`

Exit criteria:

- 用户文档、设计文档、状态记录和实现行为不互相矛盾。
- 新用户能从 plugin skill 学会正确使用 recall。

## 5. Testing Strategy

Focused tests:

```bash
python3.11 -m pytest -q tests/test_plugin_bundle_hooks.py
python3.11 -m pytest -q tests/test_session_start.py tests/test_session_start_codex_hook.py
python3.11 -m pytest -q tests/test_session_recall_store.py tests/test_session_recall_cli.py
python3.11 -m pytest -q tests/test_session_recall_parser.py
```

Full regression:

```bash
make test PYTHON=python3.11
```

Manual probes:

```bash
csep recall "MEMORY.md|refs|二级引用" --cwd "$PWD"
csep recall "summary需要llm|不要llm|Hermes" --cwd "$PWD"
csep recall "summary需要llm 不要llm Hermes" --cwd "$PWD"
csep recall "summary需要llm 不要llm Hermes" --cwd "$PWD" --all-terms
csep recall --recent --cwd "$PWD"
```

Packaging probes:

```bash
python3.11 -m build
python3.11 - <<'PY'
from importlib.resources import files
root = files("codex_self_evolution")
print(root.joinpath("plugin_bundle/skills/csep-session-recall/SKILL.md").is_file())
PY
```

Acceptance checks:

- Installed plugin manifest exposes `skills`.
- Harness can see `csep-session-recall`.
- SessionStart output is shorter and no longer contains full recall contract.
- Recall grep-like query succeeds where old long query no-matched.
- Recall output remains deterministic Markdown / JSON.

## 6. Remaining Open Questions

No blocking design questions remain for P0/P1.

Non-blocking implementation choices:

- The include flag name for developer/system evidence can be `--include-system` or `--include-background`; choose the clearer CLI wording during implementation.
- `tool_calls` indexing depends on actual transcript shapes observed in parser fixtures; if the field is sparse, implement `tool_name` first and leave deep `tool_calls` indexing to Phase 6.
- Trigram indexing can wait until LIKE fallback proves insufficient.
