# Hermes 风格 Session Recall 设计

日期：2026-05-14

## 状态

已确认的第一版设计，用于后续进入实现计划。

## 目标

为 CSEP 实现第一版 Hermes 风格的 session recall 底座：

```text
Codex session jsonl
  -> CSEP 全局 SQLite + FTS 导入
  -> csep recall 查询已索引历史
  -> recall 返回有预算控制的原始消息窗口
```

第一版目标是让过去的 Codex 会话在本机变得可检索、可复用，并且输出边界可控。第一版暂不做 prompt-time 自动 recall，也不做 LLM 总结。

## 已确认决策

- 使用全局 SQLite 数据库，不按项目拆多个数据库。
- `csep recall` 默认只查同仓库范围。
- 跨仓库 recall 必须显式使用 `--global`。
- 同一个 git repository 的多个 worktree 算作同一个 repo；worktree 路径只作为更细粒度 metadata 和排序信号。
- 第一版返回原始消息窗口，不做 LLM focused summary。
- 第一版保持当前触发方式，不接 `UserPromptSubmit` 自动 recall。
- 第一版支持增量 ingest 和手动历史 backfill。
- recall 输出必须有总长度预算和单条消息预算。
- 现有 `recall/index.json` 行为保留为 fallback，SQLite 不可用或无结果时不打断旧能力。

## 架构

CSEP 新增一个全局 session recall store：

```text
~/.codex-self-evolution/session_recall/state.db
```

这个 store 负责可查询的 recall 索引。Codex session jsonl 仍然是原始事实来源；CSEP 只把这些文件导入到自己的 SQLite 表和 FTS 索引里。

第一版最小表集合：

- `sessions`
- `messages`
- `messages_fts`
- `ingest_checkpoints`
- `ingest_errors`

`csep recall` 优先查询这个 store。默认情况下，它根据当前 `--cwd` 或进程 cwd 解析 repo fingerprint，只返回同仓库命中，并围绕命中消息返回有预算控制的上下文窗口。`--global` 会移除 repo 过滤，允许跨仓库检索。

repo identity 和 worktree identity 分开处理：

- `repo_fingerprint` 表示同一个 git repository 的稳定身份。同一个 repo 的多个 worktree 应共享这个 fingerprint。
- `worktree_root` / `cwd` 表示具体发生在哪个工作区，用于展示、排序和更细粒度过滤。
- 默认 same-repo recall 使用 `repo_fingerprint`，所以可以跨同仓库 worktree 召回。
- 排序时可以给 same `worktree_root`、cwd prefix、same branch 额外加权，但这些不能把同仓库其他 worktree 排除掉。

repo fingerprint 的实现要优先选择稳定信号，例如 git common dir、origin URL 或归一化后的 repository root。不能只用当前 worktree 路径直接做唯一身份，否则同一仓库的多个 worktree 会被错误拆开。

## Ingest 与 Backfill

新增 CLI：

```bash
csep session-ingest
csep session-ingest --backfill
```

`session-ingest` 从 Codex session jsonl 做增量导入。它使用 checkpoint，只处理上次导入后新增或变更过的文件。

`--backfill` 扫描已有的 `~/.codex/sessions` 历史，并导入同一个全局数据库。第一版 backfill 是手动命令，不自动跑。它必须支持中断后重跑。

ingest 必须幂等。重复处理同一个文件或同一条消息，不应该生成重复行。

解析失败不能阻塞整批任务。坏文件或暂不支持的 event shape 应记录 ingest error，然后继续处理其他文件。

session metadata 至少包括：

- `session_id`
- `session_path`
- `source = codex_jsonl`
- `cwd`
- `repo_root`
- `repo_fingerprint`
- `worktree_root`
- `git_branch`
- `started_at`
- `updated_at`
- `message_count`

message metadata 至少包括：

- `session_id`
- `message_index`
- `role`
- `content`
- `timestamp`
- `tool_name`
- `raw_event_type`

## Recall 查询

默认命令：

```bash
csep recall "query"
```

默认查询流程：

1. 从 `--cwd` 或 `PWD` 解析当前 repo。
2. 搜索 `messages_fts`。
3. 默认过滤到相同 `repo_fingerprint`。
4. 按 FTS score、时间、轻量 role 权重排序，并给相同 worktree / cwd prefix / branch 额外加权。
5. 按 session 和 message 聚合命中。
6. 提取每个命中周围的原始消息窗口。
7. 默认渲染 Markdown，请求 JSON 时输出机器可读结果。

跨仓库命令：

```bash
csep recall "query" --global
```

`--global` 可以跨仓库搜索，但输出里仍必须带 repo / cwd / source metadata，方便模型判断相关性。

第一版支持这些 CLI flags：

```bash
--global
--limit 3
--before 3
--after 5
--budget-chars 12000
--message-chars 1200
--format markdown|json
```

## 输出形态

Markdown 输出要短、清晰，适合模型直接阅读：

```text
## Focused Recall
Status: matched
Scope: repo
Results: 3
Budget: 9340/12000 chars

### 1. 2026-05-10 session <id>
Score: ...
Matched: ...
Source: ~/.codex/sessions/...
Repo: /path/to/repo
Worktree: /path/to/worktree
Branch: feature/example

[user] ...
[assistant] ...
[tool:exec_command] ...
[assistant] ...
```

JSON 输出要包含便于调试的结构化 metadata：

```json
{
  "query": "...",
  "scope": "repo",
  "count": 3,
  "budget": {
    "budget_chars": 12000,
    "used_chars": 9340,
    "truncated": true,
    "truncation_reason": "budget_exceeded"
  },
  "results": []
}
```

## 消息窗口提取

对每个 FTS 命中，CSEP 返回原始消息窗口，而不是整段 transcript。

默认值：

- `before = 3`
- `after = 5`
- `limit = 3`
- `message_chars = 1200`
- `budget_chars = 12000`

内容超过预算时，按以下优先级裁剪：

1. 保留命中消息。
2. 保留距离命中最近的前后消息。
3. 优先丢弃更远的上下文。
4. 单条长消息用 head/tail 截断。
5. tool output 比 user / assistant 文本使用更紧的截断策略。

渲染结果必须显式标记截断：

```text
[truncated: message exceeded 1200 chars]
[truncated: recall budget exceeded]
```

## 隐私与脱敏

数据库只存本机，但 recall 输出仍然不能打印明显的 secret-like 内容。

Markdown 或 JSON 渲染前，必须复用现有脱敏规则或等价检查，至少覆盖这些明显敏感字符串：

- `Authorization`
- `Bearer`
- `api_key`
- `token`
- `password`
- cookie-like credential fields

第一版只要求输出时脱敏，不要求修改数据库中已存储的 row。

## 兼容性

第一版不删除、不迁移现有 `recall/index.json` records。

fallback 行为：

- SQLite 数据库不存在时，使用当前 `search_recall()`。
- SQLite 存在但无命中时，可以 fallback 到当前 `recall/index.json`。
- SQLite 查询失败时，在 debug metadata 中记录 SQLite failure，并 fallback 到当前 recall search。

这样新底座上线时，不会打断已有 recall 能力。

## 非目标

第一版不实现：

- `UserPromptSubmit` 自动 recall 注入。
- LLM focused summary。
- vector search。
- graph retrieval。
- 整段 session transcript 输出。
- scheduler 自动 backfill。
- 远程同步或共享 recall 存储。

## 风险与兜底

### Codex Jsonl 格式漂移

ingest parser 必须 best-effort、足够防御。未知 event shape 可以跳过，必要时保存 raw metadata。parser error 要记录下来，但不能中断整批任务。

### Recall 污染

默认 same-repo 过滤，避免普通 recall 混入其他仓库。跨仓库必须显式使用 `--global`。

同仓库多 worktree 不视为污染。它们默认可互相召回，但输出必须展示 `worktree_root` / `cwd` / `git_branch`，让模型知道经验来自哪个工作区。

### 输出爆炸

recall 输出受总预算、单消息预算、命中数量和窗口大小共同约束。所有截断都必须显式标记。

### Provider 稳定性

第一版 recall 路径不调用 LLM，所以 provider timeout 或 quota 不影响 recall。

### 现有运行状态

新能力写入新的全局数据库路径，不改写现有 memory / recall 产物。

## 测试计划

### Ingest Parser 测试

使用小型 fake Codex jsonl fixture，覆盖：

- user、assistant、tool message
- 缺字段事件
- 缺 cwd / repo metadata
- 重复 ingest 幂等
- parser error 写入 receipt 且不中断整批

### SQLite / FTS 测试

使用临时 state dir，验证：

- 导入内容能通过 FTS 搜到
- same-repo 默认过滤生效
- `--global` 能返回跨 repo 结果
- 当前 repo 无命中时，不加 `--global` 不返回其他 repo

### 消息窗口测试

覆盖：

- `before` / `after` 行为
- session 开头和结尾边界
- 同一 session 多个命中
- 重叠窗口去重
- tool output 截断

### Budget 测试

覆盖：

- 总 `budget_chars`
- 单条 `message_chars`
- 命中消息优先保留
- 显式截断标记
- JSON budget metadata

### CLI 测试

覆盖：

- `csep session-ingest`
- `csep session-ingest --backfill`
- `csep recall "query"`
- `csep recall "query" --global`
- SQLite 不可用时 fallback 到现有 recall search

主要验证命令：

```bash
uv run pytest -q
```
