# Session Reflection

Session reflection 是 CSEP 的后台沉淀链路。它不在每次 `Stop` 都运行；`Stop` 前台只做归档、轻量计数和触发判断，真正的 memory / skill review 放到后台 worker。

## 目标

- 每次 `Stop` 都归档 transcript。
- 用确定性 trigger policy 判断是否需要启动 reflection。
- 命中后 fork 当前 Codex thread，让 child Codex review memory 和 skill。
- 允许 child 返回 nothing to save；这也是一次成功 review。
- 写入必须留下 `receipt.json`，并由 parent 做边界校验。

## Stop hook 前台流程

```text
csep session-stop --from-stdin
  -> 解析 Codex Stop payload
  -> recursion guard 排除 reflection child
  -> 后台归档 transcript 到 session_recall
  -> 读取当前 session trigger state
  -> 增量统计 readable chars / tool calls / stop count
  -> deterministic trigger policy 输出 decision
  -> archive_only: 直接返回 {"continue": true}
  -> queued: 创建 job，后台运行 session-reflect worker
```

前台不能调用 LLM，也不能等待 child 完成。Stop hook 的失败策略是尽量放行 Codex 正常退出。

## Trigger policy

Trigger state 按 Codex session 维护，不做全局计数。路径：

```text
~/.codex-self-evolution/session_reflection/triggers/<stable_session_id>/
  state.json
  decisions.jsonl
  trigger.lock
```

当前计数器：

| 字段 | 含义 |
| --- | --- |
| `stops_since_memory_review` | 距离上次 memory review 的 Stop 次数 |
| `readable_chars_since_memory_review` | 距离上次 memory review 的可读文本增量 |
| `tool_calls_since_skill_review` | 距离上次 skill review 的工具调用增量 |
| `last_counted_byte_offset` | transcript 增量扫描 high watermark |
| `active_job_id` | 当前 parent session 的活跃 job |

默认触发来源：

- memory stop interval
- memory context chars
- skill tool call interval
- high-signal keywords

Trigger 只回答“要不要评估”。它不决定一定要写 memory 或 skill。写不写由 fork 出来的 child Codex 根据完整 session 语义判断。

## Worker 流程

```text
csep session-reflect --job <job_id>
  -> 获取全局 reflection lock
  -> 读取 job 和 config
  -> 通过 Codex app-server fork parent thread
  -> 注册 child thread，避免递归触发
  -> 构造 reflection prompt
  -> start reflection turn
  -> 等待 receipt.json 可解析为 JSON object
  -> validate_receipt()
  -> reset 对应 trigger counters
  -> 写 latest/job 状态
```

`run_reflection_job()` 会把 prompt、receipt、validation 和 app-server response 保存在 run 目录，方便排障。

## 写入边界

Child Codex 只能写两类产物：

1. 当前 repo bucket 的 memory：
   - `MEMORY.md`
   - `refs/**/*.md`
2. 用户 Codex skills 目录下的 `csep-reflect-*` skill。

Child 必须写 `receipt.json`，声明：

- job id
- parent session id
- child thread id
- 写入文件路径
- 写入前后 hash
- 是否写入 memory
- 是否写入 skill

Parent 校验：

- receipt 必须是合法 JSON object。
- job / parent / child id 必须匹配。
- memory path 只接受当前 repo bucket 下的 `MEMORY.md` 和 `memory/refs/**/*.md`；旧 `USER.md` 不再是合法写入目标。
- skill path 必须落在 skills root 下，且名称必须以 `csep-reflect-` 开头。
- 文件 hash 必须和 receipt 声明一致。

校验失败时 job 标记为 `failed`，不会把 child 的口头声明当作成功。

## 递归和并发

递归防护用于避免 `fork -> fork -> fork`：

- `threadSource == "memory_consolidation"` 时跳过。
- child thread registry 命中时跳过。
- transcript 中出现 reflection child marker 时跳过。

并发防护用于避免同一个 parent session 同时跑多个 job：

- 有 `queued` / `running` job 时不再新建 job。
- 已经 `succeeded` 的旧 job 不会永久阻止后续 review。
- 有活跃 job 时，新的 Stop 仍会继续累计 counters，避免漏掉 child 启动后的新增内容。

## 配置

核心配置：

```toml
[stable_memory]
enabled = true

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

[session_reflection.trigger]
enabled = true
memory_review = true
skill_review = true
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 1800
```

## 执行模型和安全边界

默认配置里的 `sandbox = "danger-full-access"` 和 `approval_policy = "never"` 是有意选择的高自治模式：reflection child 需要在后台完成读 transcript、写 memory、生成 skill、落 receipt 等动作，不能在 Codex 退出路径上等待人工确认。

这个默认值不表示 child 输出天然可信。CSEP 的安全边界放在父进程：

- child 只能通过 receipt 声明自己写了什么。
- 父进程用 `validate_receipt()` 校验 job / parent / child identity、写入路径、文件 hash、skill namespace 和 `SKILL.md` 结构。
- 合法 memory 写入只允许落在当前 repo bucket 的 `MEMORY.md` 或 `memory/refs/**/*.md`。
- 合法 skill 只允许落到 `~/.codex/skills/csep-reflect-*` 命名空间。
- validation 失败的 job 不会被视为成功沉淀。

如果你在共享机器、低信任 provider、或更严格的合规环境运行，可以把 sandbox / approval policy 调低，但要预期后台 reflection 可能需要人工确认或无法完成。

用户可以换模型。CSEP 会把 job 使用的模型记录到 job/fork response 中，但当前不做模型 allowlist 拦截。

## 排障文件

```text
~/.codex-self-evolution/session_reflection/latest.json
~/.codex-self-evolution/session_reflection/jobs/<job_id>.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/prompt.txt
~/.codex-self-evolution/session_reflection/runs/<job_id>/receipt.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/validation.json
~/.codex-self-evolution/logs/plugin.log
/tmp/codex-self-evolution/session-reflect-*.log
```

常用命令：

```bash
csep session-reflect --status | python3 -m json.tool
csep status | python3 -m json.tool
```
