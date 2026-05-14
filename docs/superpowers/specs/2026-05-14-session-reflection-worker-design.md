# Session Reflection Worker 设计

日期：2026-05-14

## 状态

已确认的 MVP 设计，用于后续进入实现计划。

## 背景

当前 CSEP 的 Stop reviewer 会把会话复盘结果写成 pending suggestions，再由 compiler 和定时任务晋升为 memory、recall 或 managed skill。这个链路可审计，但对“什么应该成为 skill”判断偏弱，并且离完整 session 上下文较远。

本设计引入一个新的 session reflection worker。它在每次 Codex Stop 后，通过 Codex app-server fork 当前 thread，让 `gpt-5.3-codex-spark` 在 fork 出来的后台 thread 中总结 memory 和 skills，并直接写入长期资产。

Hermes-style session recall 设计继续作为独立系统推进。两者职责不同：

- session recall：归档、索引和检索历史 session。
- session reflection：每次 Stop 后总结 memory 和 skills。

## 已确认决策

- 新功能在独立 worktree 和分支中开发：`feature/session-reflection-appserver`。
- Stop hook 前台仍必须快速返回 `{"continue": true}`。
- 新 reflection worker 替换旧 Stop reviewer 主路径；不做旧 reviewer 和新 worker 并行对照。
- 后台 worker 使用 Codex app-server，而不是 `codex fork` TUI 或 `codex exec`。
- app-server 使用原生 `thread/fork`，优先按 `threadId` fork，必要时按 rollout `path` fallback。
- forked thread 使用 `model = "gpt-5.3-codex-spark"`。
- forked thread 使用 `threadSource = "memory_consolidation"`。
- 默认 `ephemeral = true`，debug 时允许配置为 `false`。
- MVP 使用 `sandbox = "danger-full-access"` 和 `approvalPolicy = "never"`。
- memory 写 CSEP 资产层，不直接写 Codex 全局规则。
- skills 直接写入 Codex skills 根目录，但必须使用 `csep-reflect-*` 前缀。
- MVP 允许 child 直接写文件；父进程只承认 receipt 中声明且通过后验校验的产物。
- 防递归必须在 hook 入口层完成，不能依赖 prompt 约束模型。
- MVP 不实现复杂 retry 队列；失败要写 receipt / log，并保持当前 Codex session 不被阻塞。

## 架构

总体链路：

```text
Codex Stop hook
  -> codex-self-evolution stop-review --from-stdin
  -> recursion guard / parent job lock
  -> write session_reflection job
  -> spawn detached background worker
  -> connect Codex app-server
  -> thread/fork(parent_session_id)
  -> turn/start(reflection prompt)
  -> child writes memory + csep-reflect skills + receipt
  -> parent validates receipt and generated artifacts
```

`stop-review --from-stdin` 可以保留命令名，以减少 hook 安装和迁移成本，但它的默认主行为会从“spawn old reviewer”切换为“spawn session reflection worker”。旧 reviewer 代码可暂时保留为 fallback/debug 命令，但不作为默认 Stop 主链路。

新增运行状态目录：

```text
~/.codex-self-evolution/session_reflection/
  jobs/
  runs/
  child_threads/
  locks/
```

`jobs/` 保存待执行和已执行 job 元信息。`runs/` 保存每次 worker 的 receipt、prompt、app-server event 摘要和校验结果。`child_threads/` 保存 reflection child thread id，用于递归防护。`locks/` 保存 parent session 和全局并发锁。

## Stop Hook 数据流

Stop hook 从 Codex payload 中读取：

```text
session_id
turn_id
transcript_path
cwd
model
hook_event_name
```

然后创建 job：

```json
{
  "schema_version": 1,
  "job_id": "20260514T120000Z-abcdef",
  "parent_session_id": "019e...",
  "parent_turn_id": "019e...",
  "parent_transcript_path": "/Users/.../rollout-....jsonl",
  "cwd": "/path/to/repo",
  "model": "gpt-5.3-codex-spark",
  "status": "queued",
  "created_at": "2026-05-14T12:00:00Z",
  "updated_at": "2026-05-14T12:00:00Z"
}
```

前台 hook 只负责创建 job、spawn worker、打印 `{"continue": true}`。任意异常都只能变成 warning 或 log，不能阻塞 Codex Stop。

## App-Server Fork

后台 worker 连接 Codex app-server 后调用 `thread/fork`：

```json
{
  "threadId": "<parent_session_id>",
  "path": "<parent_transcript_path fallback>",
  "model": "gpt-5.3-codex-spark",
  "cwd": "<cwd>",
  "threadSource": "memory_consolidation",
  "ephemeral": true,
  "sandbox": "danger-full-access",
  "approvalPolicy": "never"
}
```

优先使用 `threadId`。如果 app-server 无法按 `threadId` 找到历史 thread，并且 Stop payload 提供了 `transcript_path`，再使用 `path` fallback。fallback 失败则 job 标记 failed，不回退到旧 reviewer。

fork 成功后，worker 立即写 child registry：

```text
~/.codex-self-evolution/session_reflection/child_threads/<child_thread_id>.json
```

registry 内容至少包括：

```json
{
  "schema_version": 1,
  "child_thread_id": "...",
  "parent_session_id": "...",
  "job_id": "...",
  "thread_source": "memory_consolidation",
  "created_at": "2026-05-14T12:00:00Z"
}
```

随后 worker 对 child thread 调用 `turn/start`，注入 reflection prompt。

## Reflection Prompt Contract

child 只负责两类长期资产：

1. memory：稳定事实、偏好、规则、工具经验。
2. skills：可复用 workflow。

prompt 必须带固定 marker：

```text
CSEP_REFLECTION_JOB_ID=<job_id>
CSEP_REFLECTION_CHILD=1
```

这些 marker 同时用于递归防护。child 必须写 receipt，并在最终回复中只简短说明结果；父进程不依赖最终回复作为唯一事实来源。

prompt 要求 child 分类每个候选：

```text
fact | rule | preference | workflow | duplicate | transient | sensitive
```

分类规则：

- `fact`、`rule`、`preference` 可以进入 memory。
- 只有 `workflow` 可以生成 active skill。
- `duplicate` 应编辑已有 `csep-reflect-*` skill 或跳过。
- `transient` 和 `sensitive` 必须跳过。

## Memory 写入

memory 写入 CSEP 资产层：

```text
<project bucket>/memory/USER.md
<project bucket>/memory/MEMORY.md
```

写入规则：

- 用户偏好、个人工作习惯进入 `USER.md`。
- 项目规则、工具经验、稳定事实进入 `MEMORY.md`。
- 不写本轮进度、临时状态、一次性 ID、密钥、token、cookie、隐私 ID 或敏感内部链接。
- 更新必须去重，避免同义重复堆积。
- 更新必须保留 Markdown 可读性。

receipt 中每个 memory 变更至少记录：

```json
{
  "path": "/abs/path/MEMORY.md",
  "scope": "global",
  "action": "add|edit|remove|skip",
  "before_hash": "...",
  "after_hash": "...",
  "summary": "..."
}
```

## Skill 写入

skills 直接写入 Codex skills 根目录：

```text
~/.codex/skills/csep-reflect-*/SKILL.md
```

skill 目录名必须以 `csep-reflect-` 开头。父进程后验校验只承认这个命名空间，任何其他 skill 写入都标记为越界。

active skill 必须满足：

- frontmatter `name` 等于目录名。
- frontmatter `description` 是单行、具体触发条件。
- description 不使用 block scalar。
- body 包含 `Skill Decision`、`When to Use`、`Inputs`、`Workflow`、`Verification`、`Failure Handling`。
- `Skill Decision` 明确说明为什么这是 skill、为什么不是 memory、和已有 skill 的边界。
- 不能为一次性事实、偏好、规则、临时状态或敏感信息生成 skill。

建议的 skill body 结构：

```markdown
# <Skill title>

## Skill Decision

- Why this is a skill: ...
- Why not memory: ...
- Existing skill boundary: ...

## When to Use

...

## Inputs

...

## Workflow

...

## Verification

...

## Failure Handling

...
```

receipt 中每个 skill 变更至少记录：

```json
{
  "skill_id": "csep-reflect-example",
  "path": "/Users/.../.codex/skills/csep-reflect-example/SKILL.md",
  "action": "create|edit|retire|skip",
  "evidence_kind": "workflow",
  "why_skill_not_memory": "...",
  "existing_skill_overlap": "...",
  "before_hash": "",
  "after_hash": "..."
}
```

## Receipt

child 必须写：

```text
~/.codex-self-evolution/session_reflection/runs/<job_id>/receipt.json
```

receipt schema：

```json
{
  "schema_version": 1,
  "job_id": "...",
  "parent_session_id": "...",
  "child_thread_id": "...",
  "status": "succeeded|partial|failed|skipped",
  "memory_changes": [],
  "skill_changes": [],
  "skipped_candidates": [],
  "validation_notes": [],
  "errors": [],
  "started_at": "...",
  "finished_at": "..."
}
```

父进程以后验校验结果为准，可以把 child 写的 `status` 降级为 `partial` 或 `failed`。

## 递归防护

递归防护在 Stop hook 入口最前面执行：

1. 如果 hook payload 或 app-server metadata 表明当前 thread source 是 `memory_consolidation`，直接 skip。
2. 如果当前 `session_id` 命中 `child_threads/` registry，直接 skip。
3. 如果 transcript 中出现 `CSEP_REFLECTION_JOB_ID=` 或 `CSEP_REFLECTION_CHILD=1`，直接 skip。
4. 如果 `jobs/` 中已经存在同一个 `parent_session_id` 的 active 或 succeeded job，直接 skip。
5. 如果全局 lock 存在且未超时，直接 skip 或记录 locked。

skip 仍要写轻量日志或 receipt，方便确认不是静默失效。递归防护不能依赖模型遵守 prompt。

## 校验与状态

父进程在 child 结束或超时后执行校验：

- receipt 缺失：job `failed`。
- app-server fork 失败：job `failed`。
- child 超时：job `failed`。
- 写入路径越界：job `failed`。
- memory hash 与 receipt 不一致：job `partial`。
- skill frontmatter 错误：对应 skill 标记 invalid，job `partial`。
- skill 缺 required sections：对应 skill 标记 invalid，job `partial`。
- receipt 存在但无候选：job `skipped_empty`。
- 只有 memory 或只有 skill 变更：如果 receipt 明确说明另一类无候选，可以是 `succeeded`。

invalid skill 的处理方式：

- 写 `.csep-invalid.json` 到对应 skill 目录，记录原因和 job id。
- 该 skill 不应被视为可用 skill。
- MVP 不自动删除越界文件；越界写入要高亮报告，避免误删用户内容。

## 配置

新增配置：

```toml
[session_reflection]
enabled = true
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = true
sandbox = "danger-full-access"
approval_policy = "never"
skill_prefix = "csep-reflect-"
timeout_seconds = 900
max_concurrent_jobs = 1
replace_stop_reviewer = true
```

默认启用 session reflection 并替换旧 Stop reviewer。若 `enabled = false`，Stop hook 只快速返回并记录 disabled skip；MVP 不自动回退旧 reviewer。

## CLI

保留现有 Stop hook 入口：

```bash
codex-self-evolution stop-review --from-stdin
```

新增调试命令：

```bash
codex-self-evolution session-reflect --hook-payload <file>
codex-self-evolution session-reflect --job <job_id>
codex-self-evolution session-reflect status
```

`session-reflect --hook-payload` 用于重放某次 Stop payload。`session-reflect --job` 用于重跑或检查指定 job。`status` 展示最近 job、child thread、失败原因和 invalid skill 数。

## 失败处理

MVP 不实现复杂 retry 队列。失败策略：

- app-server 不可用：job failed，记录错误。
- `thread/fork` 失败：job failed，不 fallback。
- `turn/start` 失败：job failed。
- child 超时：job failed，保留 child thread id 和最后 event 摘要。
- child 写了 receipt 但无实际改动：status `skipped_empty`。
- child 写了越界路径：job failed，并把越界路径写入高优先级诊断。
- 任一失败不阻塞当前 Codex Stop。

## 测试策略

测试重点是系统边界，而不是模型总结质量。

Stop hook 测试：

- `--from-stdin` 快速返回 `{"continue": true}`。
- 默认 spawn reflection worker，而不是旧 reviewer。
- invalid payload 不阻塞 Stop。

递归 guard 测试：

- `threadSource = memory_consolidation` 时 skip。
- child registry 命中时 skip。
- transcript marker 命中时 skip。
- parent job lock 命中时 skip。

app-server client 测试：

- fake JSON-RPC server 收到 `thread/fork`。
- `thread/fork` 参数包含 `model`、`threadSource`、`ephemeral`、`sandbox`、`approvalPolicy`。
- fake server 返回 child thread id 后写 child registry。
- worker 调用 `turn/start` 并带 reflection marker。

receipt 校验测试：

- receipt 缺失。
- receipt schema 错误。
- memory hash 不一致。
- skill 路径越界。
- skill frontmatter 错误。
- skill 缺 required sections。

写入边界测试：

- memory 只承认 CSEP project bucket 下的 `USER.md` / `MEMORY.md`。
- skill 只承认 `~/.codex/skills/csep-reflect-*/SKILL.md`。
- 越界写入不会被标记为成功。

## MVP 非目标

- 不实现旧 reviewer 和新 reflection 的并行对比。
- 不把 reflection 输出先转 pending suggestions。
- 不做多轮 retry 队列。
- 不做质量评分 UI。
- 不自动清理旧 `csep-synth-*` skill。
- 不把 Hermes-style SQLite recall 和 reflection 合并。
- 不要求最小权限 sandbox。
- 不依赖 `codex fork` TUI。
- 不用 `codex exec` 模拟 fork。
- 不自动删除越界写入文件。

## 风险

主要风险是权限过大、app-server 协议仍带实验性质、child 直写长期资产后质量不稳定，以及后台 thread 可能制造递归 Stop。MVP 用 `threadSource = memory_consolidation`、child registry、prompt marker、parent job lock 和后验校验降低这些风险。

权限风险在 MVP 中接受，但不扩大承认边界。child 可以拿到 `danger-full-access`，但 CSEP 只把指定 memory 路径、`csep-reflect-*` skill 和 receipt 计为有效产物。

app-server 风险通过 fake server 测试和失败响亮化处理。若 app-server 不可用或协议变更，job 必须 failed，不能静默跳过。

## 成功标准

- Stop hook 前台始终快速返回。
- 正常 Stop 可以创建 reflection job。
- worker 可以通过 app-server fork 当前 session。
- forked child 使用 `gpt-5.3-codex-spark` 和 `threadSource = memory_consolidation`。
- reflection child 不会再次触发 reflection。
- memory 变更只落在 CSEP 资产层。
- skill 变更只落在 `~/.codex/skills/csep-reflect-*`。
- 每次运行都有可审计 receipt。
- 越界、超时、receipt 缺失、skill invalid 都会响亮失败。
