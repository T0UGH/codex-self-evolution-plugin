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
- SQLite 是本机 durable recall archive，不只是镜像缓存或临时索引。
- SQLite 要保存原始 session 消息和 raw json，避免 `~/.codex/sessions` 本地日志丢失后 recall 失效。
- 原始 Codex jsonl 文件被删除时，SQLite 中已归档 session 不自动删除。
- `csep recall` 默认只查同仓库范围。
- 跨仓库 recall 必须显式使用 `--global`。
- 同一个 git repository 的多个 worktree 算作同一个 repo；worktree 路径只作为更细粒度 metadata 和排序信号。
- 第一版提供 `[session_recall]` 总开关，默认启用。
- Stop hook 第一版自动归档当前 session；归档通过独立后台子进程执行，不是 scheduler、队列或定时任务。
- 第一版不做 ingest 失败重试队列，失败只记录日志、status 和 ingest error，后续靠手动 backfill 补。
- 第一版不做 purge / 显式删除能力；已归档数据不会自动删除，删除能力留到后续版本。
- user、assistant、tool call、tool output 都要入库；recall 默认搜索 tool output，但 tool 命中降权，输出时强截断。
- FTS 只索引提取出的可读文本；raw json 保存到库里，但不进 FTS，也不在默认 recall 输出里展示。
- 第一版支持 Hermes 风格 FTS5 高级语法，默认保留 FTS5 多词 AND 语义；广搜由用户或模型显式写 `OR`。
- 默认按 session 聚合结果，`--limit` 表示最多返回几个 session；同一 session 默认只返回一个最佳命中窗口。
- `--recent` 支持最近 session 浏览，默认同仓库，`--recent --global` 才看全局。
- 能识别当前 session 时，recall 默认排除当前 session；手动命令识别不了时不强行猜。
- 第一版返回原始消息窗口，不做 LLM focused summary。
- 第一版保持当前触发方式，不接 `UserPromptSubmit` 自动 recall。
- 第一版支持 Stop hook 单 session archive 和手动历史 backfill；不做无参数 `csep session-ingest` 增量扫描。
- recall 输出必须有总长度预算和单条消息预算。
- 现有 `recall/index.json` 行为保留为 fallback，SQLite 不可用或无结果时不打断旧能力。

## 架构

CSEP 新增一个全局 session recall store：

```text
~/.codex-self-evolution/session_recall/state.db
```

这个 store 负责可查询的 recall 索引，也负责本机长期归档。Codex session jsonl 是初始事实来源；一旦导入成功，SQLite 中的消息内容不依赖原始 jsonl 文件继续存在。

第一版最小表集合：

- `sessions`
- `messages`
- `messages_fts`
- `ingest_checkpoints`
- `ingest_errors`

需要保留最小 schema version：

- `schema_version`

第一版只实现轻量版本升级，不引入 Alembic 或复杂迁移框架。

`csep recall` 优先查询这个 store。默认情况下，它根据当前 `--cwd` 或进程 cwd 解析 repo fingerprint，只返回同仓库命中，并围绕命中消息返回有预算控制的上下文窗口。`--global` 会移除 repo 过滤，允许跨仓库检索。

repo identity 和 worktree identity 分开处理：

- `repo_fingerprint` 表示同一个 git repository 的稳定身份。同一个 repo 的多个 worktree 应共享这个 fingerprint。
- `worktree_root` / `cwd` 表示具体发生在哪个工作区，用于展示、排序和更细粒度过滤。
- 默认 same-repo recall 使用 `repo_fingerprint`，所以可以跨同仓库 worktree 召回。
- 排序时可以给 same `worktree_root`、cwd prefix、same branch 额外加权，但这些不能把同仓库其他 worktree 排除掉。

repo fingerprint 的实现要优先选择稳定信号，例如 git common dir、origin URL 或归一化后的 repository root。不能只用当前 worktree 路径直接做唯一身份，否则同一仓库的多个 worktree 会被错误拆开。

## 配置开关

第一版新增最小配置：

```toml
[session_recall]
enabled = true
stop_hook_archive = true
```

默认开启，避免用户忘记归档导致日志丢失。

- `enabled = false`：SQLite session recall 整体关闭，`csep recall` fallback 到现有 `recall/index.json`。
- `stop_hook_archive = false`：手动 backfill / recall 仍可用，但 Stop hook 不自动归档当前 session。

## Ingest 与 Backfill

新增 CLI：

```bash
csep session-archive --from-hook-payload <payload.json>
csep session-archive --transcript-path <path> --cwd <cwd> --session-id <id>
csep session-ingest --backfill
csep session-ingest --backfill --since-days 30
csep session-ingest --backfill --limit-files 500
```

`session-archive` 归档一个明确的 session / transcript，主要给 Stop hook 使用，也支持手动补录单个 transcript。

`session-ingest --backfill` 扫描历史目录，批量导入旧 session。第一版不提供无参数 `csep session-ingest` 增量扫描，避免把第一版做成半个同步器或 scheduler。

Stop hook 会单独 spawn 一个独立后台子进程，执行 `csep session-archive --from-hook-payload <payload.json>`，归档当前 hook payload 指向的 transcript。这个子进程不与 stop-review 共用；任意一边失败都不影响另一边。前台 Stop hook 仍然快速返回。

这里的“后台”只表示 detached process，不是定时任务、队列或重试系统。

第一版不做 ingest 失败重试队列。Stop hook archive 失败时只记录日志、`ingest_errors` 和 status 可见状态；后续通过手动 `session-ingest --backfill` 补。

`--backfill` 默认扫描已有的 `~/.codex/sessions` 全量历史，并导入同一个全局数据库。第一版 backfill 是手动命令，不自动跑。它必须支持中断后重跑，也支持 `--since-days` 和 `--limit-files` 控制范围。

ingest 必须幂等。重复处理同一个文件或同一条消息，不应该生成重复行。

同一 session 多次 ingest 时，不删除后重建，而是做幂等增量 upsert。唯一键使用 `(session_id, message_uid)`；`message_index` 只作为排序、窗口提取和 fallback 信息。

`message_uid` 解析顺序：

1. 优先使用 Codex event 中稳定 id 字段。
2. 其次使用 item id、turn id、tool call id 等稳定字段组合。
3. 没有稳定字段时，用 `sha256(session_id + raw_line)`。

解析失败不能阻塞整批任务。坏文件或暂不支持的 event shape 应记录 ingest error，然后继续处理其他文件。

如果原始 Codex jsonl 后续被删除，SQLite 中已归档数据不自动删除。ingest 或 backfill 发现源文件缺失时，只更新类似 `source_missing` / `last_seen_at` 的 metadata，不删除消息。

`session-archive` 成功标准：

- 成功写入或更新 session row。
- 至少写入 1 条可读 message row。
- 写入的可读 message 能通过 FTS 搜到。
- repo metadata 尽量完整；无法解析 git repo 时可用 `cwd` fallback。
- tool metadata 解析失败但 tool output 文本已入库时，只记 warning，不算整体失败。
- 0 条可读 message 算 archive failed。

session metadata 至少包括：

- `session_id`
- `session_path`
- `source = codex_jsonl`
- `cwd`
- `repo_root`
- `repo_fingerprint`
- `worktree_root`
- `git_branch`
- `source_missing`
- `started_at`
- `updated_at`
- `message_count`

message metadata 至少包括：

- `session_id`
- `message_uid`
- `message_index`
- `role`
- `content`
- `raw_json`
- `metadata_json`
- `timestamp`
- `tool_name`
- `raw_event_type`

入 `messages.content` 并进入 FTS 的只应是可读文本：

- user 文本
- assistant 文本
- tool call 名称和参数摘要
- tool output / tool result
- 必要的 system / developer / context 片段

不直接进入 FTS 的内容：

- 纯 telemetry
- token accounting
- 空 content
- 未提取文本的大段结构对象
- `raw_json`

## Recall 查询

默认命令：

```bash
csep recall "query"
csep recall --recent
```

默认查询流程：

1. 从 `--cwd` 或 `PWD` 解析当前 repo。
2. 使用 FTS5 搜索 `messages_fts`。
3. 默认过滤到相同 `repo_fingerprint`。
4. 按 FTS score、role 权重、时间排序，并给相同 worktree / cwd prefix / branch 额外加权。
5. 默认按 session 聚合命中。
6. 每个 session 只选择一个最佳命中窗口。
7. 默认渲染 Markdown，请求 JSON 时输出机器可读结果。

FTS 查询语法尽量接近 Hermes：

- 支持普通关键词。
- 支持 `"exact phrase"`。
- 支持 `OR` / `NOT`。
- 支持 prefix，例如 `deploy*`。
- 对破坏 FTS5 语法的字符做 sanitize。
- 查询语法异常不能 crash；应返回空结果或 fallback，并在 debug metadata 中标记。

多词普通 query 默认保留 FTS5 原生 AND 语义。需要 broad recall 时，由用户或模型显式写 `OR`。

跨仓库命令：

```bash
csep recall "query" --global
csep recall --recent --global
```

`--global` 可以跨仓库搜索，但输出里仍必须带 repo / cwd / source metadata，方便模型判断相关性。

`--recent` 返回最近 session metadata 和 preview，不回读完整消息窗口。默认同仓库过滤；配合 `--global` 才展示全局最近 session。

第一版支持这些 CLI flags：

```bash
--global
--recent
--limit 3
--before 3
--after 5
--budget-chars 12000
--message-chars 1200
--tool-message-chars 600
--format markdown|json
--current-session-id <id>
```

能识别当前 session 时，默认排除当前 session。普通手动命令没有 current session id 时，不做复杂运行时推断。

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
- `tool_message_chars = 600`
- `budget_chars = 12000`

`limit` 表示最多返回几个 session。每个 session 默认只返回一个最佳命中窗口；如果同一 session 内还有其他命中，JSON 记录 `hit_count` 和其他 hit 的 snippet / index，Markdown 只做短提示。

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

SQLite 中保存原始消息和 `raw_json` 是有意设计：它是本机归档库，不是只读镜像缓存。第一版不提供 purge / 显式删除能力；后续若引入删除能力，必须是显式操作；不会因为源 jsonl 消失而自动删除。

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
- ingest 失败重试队列。
- purge / 显式删除能力。
- 无参数 `csep session-ingest` 增量扫描。
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
- tool call 参数摘要和 tool output
- raw json 保存但不进 FTS
- 缺字段事件
- 缺 cwd / repo metadata
- 重复 ingest 幂等
- 同一 session 多次 Stop hook ingest 不重复
- 原始 jsonl 删除后不自动删除 SQLite 消息
- parser error 写入 receipt 且不中断整批
- `session-archive` 0 条可读 message 时失败
- `session-archive` tool metadata 解析失败但文本入库时只 warning

### SQLite / FTS 测试

使用临时 state dir，验证：

- 导入内容能通过 FTS 搜到
- FTS5 phrase / OR / NOT / prefix 查询可用
- FTS5 语法异常被 sanitize 或软失败
- same-repo 默认过滤生效
- `--global` 能返回跨 repo 结果
- 当前 repo 无命中时，不加 `--global` 不返回其他 repo
- tool output 默认可搜，但排序低于 user / assistant 命中
- `--recent` 默认同仓库，`--recent --global` 跨仓库

### 消息窗口测试

覆盖：

- `before` / `after` 行为
- session 开头和结尾边界
- 同一 session 多个命中
- 同一 session 多个分散命中只返回最佳窗口
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

- `csep session-archive --from-hook-payload <payload.json>`
- `csep session-archive --transcript-path <path> --cwd <cwd> --session-id <id>`
- `csep session-ingest --backfill`
- `csep session-ingest --backfill --since-days 30`
- `csep recall --recent`
- `csep recall "query"`
- `csep recall "query" --global`
- `session_recall.enabled = false` 时 fallback 到现有 recall search
- `session_recall.stop_hook_archive = false` 时 Stop hook 不归档
- SQLite 不可用时 fallback 到现有 recall search

主要验证命令：

```bash
uv run pytest -q
```
