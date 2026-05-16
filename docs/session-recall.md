# Session Recall

Session Recall 负责把过去 Codex session 中的具体上下文找回来。它和 Stable Memory 分层：启动时必须知道的压缩上下文进 `MEMORY.md`，具体发生过什么、验证过什么、踩过什么坑留在 Session Recall。

设计细节见：[`docs/design-session-recall.md`](design-session-recall.md)

## 目标

- `Stop` hook 自动归档 Codex transcript。
- 支持手动归档单个 transcript。
- 支持回填 `~/.codex/sessions` 下的历史 sessions。
- 使用 SQLite / FTS 做本地检索底座。
- 默认只查当前 repo；显式 `--global` 才跨 repo。
- 输出历史原文 evidence window，不做 LLM summary。
- 默认纯文本 / Markdown，结构化消费使用 `--format json`。

## 写入路径

默认数据库：

```text
~/.codex-self-evolution/session_recall/state.db
```

写入入口：

| 入口 | 用途 |
| --- | --- |
| `csep session-stop --from-stdin` | Stop hook 中后台归档当前 transcript |
| `csep session-archive --transcript-path ...` | 手动归档单个 transcript |
| `csep recall bootstrap --root ~/.codex/sessions` | 回填历史 Codex sessions |

归档时会解析 transcript 中的 messages，并记录：

- session id
- session path
- cwd / repo scope
- message index
- role
- content
- tool name
- timestamp
- 原始事件 metadata

## 使用方式

`csep recall` 的使用心智是：

```text
rg over past sessions
```

日常使用应该搜索短 needle，而不是长自然语言问题：

```bash
csep recall "MEMORY.md|refs|二级引用"
csep recall "Stop hook|transcript_path|missing transcript_path"
csep recall "Hermes|session_search|trigram|LIKE"
csep recall --recent
csep recall "session reflection" --global
```

`csep recall` 默认按当前工作目录解析 repo scope，只返回同 repo / worktree 相关内容。跨 repo 需要显式加 `--global`。

查询规则：

- `|` 表示 OR 候选词。
- 普通多 token query 可以在 strict no-match 后自动 OR fallback。
- 需要 AND 语义时使用 `--all-terms`。
- `no_match` 保持简单，不输出模板化建议。

主要参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--limit` / `--top-k` | `3` | 最多返回几个命中 session |
| `--before` | `3` | 命中消息前保留几条 |
| `--after` | `5` | 命中消息后保留几条 |
| `--windows-per-session` | `2` | 每个 session 展示几个 evidence window |
| `--budget-chars` | `12000` | 总输出预算 |
| `--message-chars` | `1200` | 普通消息截断预算 |
| `--tool-message-chars` | `600` | 工具消息截断预算 |
| `--format` | `markdown` | 可选 `json` |
| `--global` | `false` | 跨 repo 检索 |
| `--all-terms` | `false` | 使用 AND 语义 |

历史回填参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--root` | `~/.codex/sessions` | Codex 历史 transcript 根目录 |
| `--since-days` | `30` | 只回填最近多少天修改过的 transcript |
| `--all-history` | `false` | 不按时间截断，回填全部历史 |
| `--limit-files` | 无 | 限制处理文件数，按 transcript mtime 最新优先 |

## Plugin Skill

Recall 的操作手册应该作为 CSEP 插件自带 skill 暴露给 Codex harness，而不是通过 `SessionStart` 注入完整 recall contract。

插件结构：

```text
plugin_bundle/
├── .codex-plugin/
│   ├── plugin.json
│   └── hooks.json
└── skills/
    └── csep-session-recall/
        └── SKILL.md
```

`csep-session-recall` skill 负责教 agent：

- 把 `csep recall` 当作 `rg` over past sessions。
- 从当前任务中抽取 2-6 个 needle。
- 使用 `|` 组合候选词。
- 先查 repo scope，必要时再显式 `--global`。
- `no_match` 后换更短、更确定的 needle。
- 命中后引用原文证据，由模型自己总结和判断。

`SessionStart` 不再注入完整 recall 操作手册。最多只保留短提示，或者完全依赖 plugin skill。

## 输出模型

Recall 默认返回 session-level extractive evidence：

```text
message search
-> group by session
-> deterministic session ranking
-> extract evidence windows
-> render markdown/json
```

默认 evidence window 优先展示 `user` / `assistant` / `tool`。`developer` / `system` 内容可以参与索引，但默认不优先展示，避免 Stable Background、DeveloperInstructions、Recall Contract 污染结果。

`--recent` preview 应优先使用第一条 `user` message，而不是 transcript 的第一条 message。

## 和 Memory 的分层

应该进 `MEMORY.md` 的内容：

- 启动时必须主动知道的当前项目状态
- 用户或项目稳定规则
- 人机协作 gotcha
- 需要每次进入项目就看见的压缩上下文

应该留在 Session Recall 的内容：

- 某次具体排障过程
- 某个方案为什么被否掉
- 某次测试命令和结果
- 一段阶段性计划或交接
- 需要按关键词查回来的历史上下文

如果某段历史有价值但不适合默认注入，后台 review agent 可以整理进 `memory/refs/`，主 `MEMORY.md` 只留下摘要和绝对路径。Recall 自身不写 memory。

## 边界

- 数据只存在本机 SQLite。
- 默认不跨 repo 检索。
- CLI 不自动 repo -> global fallback。
- 当前 session 已在上下文中，recall 主要找过去 session。
- 空 query 只支持 `--recent` 模式。
- 查不到时返回 `no_match`，不 fallback 到旧 `recall/index.json`。
- 不使用 LLM summary。
- 不使用 embedding。

## 排障

检查数据库是否存在：

```bash
csep status | python3 -m json.tool
```

查看最近归档：

```bash
csep recall --recent --format json | python3 -m json.tool
```

手动归档：

```bash
csep session-archive \
  --transcript-path /path/to/session.jsonl \
  --cwd /path/to/repo \
  --session-id <id>
```

回填最近 14 天：

```bash
csep recall bootstrap --root ~/.codex/sessions --since-days 14
```
