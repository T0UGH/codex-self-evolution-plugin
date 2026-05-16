# Session Recall 设计

日期：2026-05-16

本文记录 Session Recall 线的设计决策。核心方向是：`csep recall` 是本地历史 session 的证据检索工具，不是问答工具，也不依赖 LLM 总结。

## 目标

Session Recall 负责把过去 Codex session 中发生过的具体上下文找回来，包括讨论、命令、排障路径、测试结果和用户原话。

它的目标是提供可引用的历史证据：

- 默认查当前 repo / worktree 的历史 session。
- 显式 `--global` 才跨 repo 查。
- 使用 SQLite / FTS 作为本地检索底座。
- 输出原文 evidence window，不做 LLM summary。
- 默认纯文本 / Markdown，结构化消费使用 `--format json`。
- `no_match` 保持简单，不做模板化建议器。

## 非目标

Recall 不负责：

- 替模型回答“历史里结论是什么”。
- 用 LLM 总结 session。
- 维护 Stable Memory。
- 索引 repo 文档。
- 自动跨 scope fallback。
- 生成复杂诊断或 workflow 路由建议。

模型应该把 recall 结果当成证据，再自己结合当前任务判断下一步。

## 使用心智

Recall 的正确心智是：

```text
rg over past sessions
```

也就是说，agent 不应该把 `csep recall` 当作问答接口，而应该像使用 `rg` 搜文件一样，搜短、准、可复现的 needle。

好的 recall query 是：

- 文件名：`MEMORY.md`、`hooks.json`、`session_recall`
- 命令：`csep status`、`recall bootstrap`、`python3.11 -m pytest`
- 错误文本：`missing transcript_path`、`ModuleNotFoundError`
- 代码符号：`messages_fts`、`build_focused_recall`、`review_memory`
- 用户原话：`二级引用`、`保持简单`、`不要 LLM`
- 候选词组：`MEMORY.md|refs|二级引用`

不好的 recall query 是长自然语言问题：

```bash
csep recall "what did we decide about no LLM extractive session-level retrieval design agreement"
```

更好的写法是：

```bash
csep recall "LLM|extractive|session-level|retrieval"
csep recall "summary需要llm|不要llm|Hermes"
```

## Plugin Skill

Recall 的使用说明应该作为 CSEP 插件自带 skill 暴露给 Codex harness，而不是通过 `SessionStart` 注入完整 contract。

插件结构建议：

```text
plugin_bundle/
├── .codex-plugin/
│   ├── plugin.json
│   └── hooks.json
└── skills/
    └── csep-session-recall/
        └── SKILL.md
```

`plugin.json` 需要声明：

```json
{
  "skills": "./skills/"
}
```

同时保持 marketplace / packaged plugin 副本一致。

`csep-session-recall` skill 负责教 agent：

- 把 recall 当作 `rg` over past sessions。
- 从用户问题和当前任务里抽取 2-6 个 needle。
- 使用 `|` 表达 OR 候选。
- 先查 repo scope。
- `no_match` 后换更短、更确定的 needle。
- 仍然需要跨项目历史时，才显式加 `--global`。
- 查最近工作时使用 `--recent`。
- 命中后引用原文证据，不让 recall 替自己总结。

`SessionStart` 不再注入完整 recall 操作手册。最多保留一句短提示：

```text
For historical session search, use the csep-session-recall skill.
```

## CLI 查询语义

CLI 也应该配合 grep-like 心智，而不是只靠 skill 教 agent。

默认查询模式建议为 `auto`：

- `|` 作为 OR alias，例如 `MEMORY.md|refs|二级引用`。
- 普通多 token 自然语言 query 可以在 strict no-match 后自动 OR fallback。
- 显式 FTS 语法和 quoted phrase 尽量保留用户原意。
- 需要 AND 语义时使用显式参数，例如 `--all-terms`。

这样 agent 可以自然写：

```bash
csep recall "Stop hook|transcript_path|missing transcript_path"
```

而不是必须知道 FTS5 的 `OR` 语法。

CLI 不自动从 repo scope fallback 到 global scope。跨 repo 噪音风险太高，应该由 skill 指导 agent 明确执行第二次查询：

```bash
csep recall "needle1|needle2" --global
```

## 检索与排序

Recall 应该采用 Hermes `session_search` 的前半段思想，但去掉 LLM summary：

```text
message search
-> group by session
-> deterministic session ranking
-> extract evidence windows
-> render markdown/json
```

排序保持确定性，可以组合以下信号：

- FTS / BM25 命中强度。
- 同一 session 多 hit 加分。
- 命中 role 加权：`user` / `assistant` / `tool` 优先。
- recency 加分。
- repo / worktree scope 精确匹配。

后续可以参考 Hermes 增强 CJK / 中英混合召回：

- 索引 `content`、`tool_name`、`tool_calls`。
- 对 CJK 或短 query 增加 trigram / LIKE fallback。

这不改变“不使用 LLM”的原则。

## 输出模型

Recall 默认输出 session-level extractive evidence，而不是 message 散点。

每个 session 默认展示 2 个 evidence window，可通过参数调整：

```bash
csep recall "MEMORY.md|refs|二级引用" --windows-per-session 2
```

默认 evidence window 应优先展示：

- `user`
- `assistant`
- `tool`

`developer` / `system` 内容可以参与索引，但默认不优先作为 evidence 展示，避免 Stable Background、DeveloperInstructions、Recall Contract 污染结果。需要调试底层 prompt 时，可以增加显式参数打开。

`--recent` 的 preview 应优先使用第一条 `user` message，而不是 transcript 的第一条 message。这样最近 session 列表更接近“这个 session 做了什么”。

`no_match` 保持简单：

```text
Status: no_match
Scope: repo
Results: 0
```

不追加 repo 统计、不追加 bootstrap 建议、不替 agent 规划下一步。

## 和 Memory 的关系

Memory 保存启动时应该主动知道的热上下文。Recall 保存可追溯的历史证据。

当 recall 查到某段历史很重要，但不适合每次启动都注入时，后台 review agent 可以把整理后的材料放入：

```text
memory/refs/
```

主 `MEMORY.md` 只保留一句摘要和绝对路径。Recall 本身不写 memory，也不写 refs。

## 实现影响

优先实现项：

- 新增插件 skill：`skills/csep-session-recall/SKILL.md`。
- 在插件 manifest 中声明 `"skills": "./skills/"`，并同步 source bundle 与 marketplace bundle。
- 移除 `SessionStart` 中完整 recall contract 注入，改成短提示或完全依赖 skill。
- `csep recall` 支持 `|` OR alias。
- `csep recall` 支持普通 query 的 strict -> OR fallback。
- 增加 `--all-terms` 表示显式 AND。
- 增加 `--windows-per-session`。
- 默认 evidence window 降噪 developer/system。
- `--recent` preview 优先第一条 user message。

测试重点：

- 插件打包后 skill 能被安装到 agent harness。
- `a|b|c` 能按 OR 查询。
- 普通长 query strict no-match 后能 OR fallback。
- `--all-terms` 保持 AND。
- repo scope 不自动 fallback 到 global。
- evidence window 默认优先 user/assistant/tool。
- `developer/system` 可通过显式 flag 打开。
- `--recent` preview 不再被 developer/system 开场污染。
