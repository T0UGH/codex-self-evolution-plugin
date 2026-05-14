# Session Reflection Trigger Policy 设计

日期：2026-05-15

## 状态

已确认的第一版设计，用于后续进入实现计划。

本文把 `docs/plans/2026-05-15-session-reflection-trigger-policy-brainstorm.md` 中已经收敛的讨论整理成正式设计。brainstorm 文档保留为讨论记录；后续实现以本文为准。

## 背景

当前 session reflection worker 的方向是：在 Codex Stop 后 fork 当前 session，让 child Codex 评估是否要沉淀 memory 或 skill。

如果每次 Stop 都 fork review，会带来几个问题：

- 成本高，短 session 也会启动一轮模型评估。
- 低信号 session 很多，容易产生空转。
- 太频繁的 review 会让 memory / skill 产物质量下降。

但如果完全依赖人工显式触发，又会漏掉很多 Codex session 中自然产生的规则、工具坑、workflow 和纠错信号。

因此需要一套轻量 trigger policy：每次 Stop 都归档 session，但只有规则命中时才 fork 当前 session 做 memory / skill review。

## 目标

第一版目标：

- 每次 Stop 都 archive 当前 session。
- 使用 deterministic trigger policy 判断是否需要 fork reflection。
- 不在 trigger 阶段调用 LLM 做 eligibility 判断。
- 命中 trigger 后，fork 当前 session，由 child Codex 做语义评估。
- review 可以判断为 `Nothing to save`，且这仍然算一次成功评估。
- memory 和 skill 的触发计数按当前 session 维护，不做全局计数。
- 默认允许一次完整 workflow review 就生成 active skill，避免 skill 太难产生。

## 非目标

第一版明确不做：

- 不做每次 Stop 都 reflection。
- 不做 LLM 前置 eligibility 判断。
- 不做复杂 semantic checkpoint。
- 不做按 message index 切片的 reflection prompt。
- 不做跨 session / repo / bucket 的 trigger 计数器。
- 不做“配额命中就强制写 memory / skill”。
- 不把 root cause、代码变更、验证完成等语义信号作为第一版立即触发条件。
- 不给 skill review 增加 `skill_readable_chars` 兜底阈值；第一版 skill review 仍由工具调用密度和 skill-oriented 关键词触发。
- 不做关键词列表的 TOML override；第一版使用固定保守白名单，后续再考虑可配置。

## 总体流程

```text
Codex Stop
  -> 读取 Stop payload 和 transcript
  -> 递归防护 / active-job 防重
  -> 读取当前 session 的 reflection_trigger_state
  -> 统计本次 Stop 新增的轻量活动量
  -> deterministic trigger policy 产出 decision
  -> 写回当前 session scoped trigger state / decision
  -> spawn 后台 session archive
  -> 如果 decision = archive_only：结束
  -> 如果 decision = queued：创建 session reflection job 并 spawn 后台 worker
  -> 前台 Stop hook 返回 {"continue": true}

后台 session reflection worker
  -> fork 当前 session
  -> child Codex 做 memory / skill review
  -> child 写 memory / skill / receipt
  -> parent 校验 receipt 和产物
  -> 成功后 reset 当前 session 的对应 counters
```

trigger policy 在前台 Stop hook 中执行，因为它只做确定性规则判断：读 payload / transcript、更新 session scoped counters、扫保守关键词、写 decision。慢操作是 fork child Codex 和总结 memory / skill，这部分必须放到后台 worker。

trigger policy 只决定“是否评估”。是否真的写 memory 或 skill，必须由 forked child Codex 基于完整 session 语义判断。

## 已确认决策

### Trigger State 按 Session 维护

trigger state 属于当前 Codex session。这里的 session 指 Codex thread / session id 对应的连续会话，不是全局进程，也不是项目 bucket。

每个 session 独立维护：

- memory review 计数器
- skill review 计数器
- 最近一次 trigger decision
- 最近一次 successful review receipt 引用

不从其他 session、repo bucket 或全局状态继承 trigger 进度。

短 session 如果没有命中 trigger，只 archive，不强行 review。后续如果需要跨 session 挖掘，可以由 recall 或批处理能力处理，但那不是本 trigger policy 的职责。

session 边界说明：

- 如果 Codex resume 后仍使用同一个 `session_id`，trigger state 继续累计。
- reflection child session 不创建、不读取、不更新自己的 trigger state；它只通过 recursion guard 被跳过。
- 如果用户在同一个 Codex session 中切换 cwd / repo，trigger state 不重置；trigger 的归属边界仍然是 `session_id`。

### Trigger State 跟 Session Archive 落盘

trigger state 跟对应 session archive 的 metadata 一起落盘，不维护跨 session 的 trigger state 文件。

逻辑结构：

```text
session archive
  - transcript / messages
  - metadata
  - reflection_trigger_state
  - reflection_trigger_decisions
  - reflection_receipts
```

第一版固定使用 session-scoped sidecar 文件，不写入 session recall SQLite。

路径模板：

```text
~/.codex-self-evolution/session_reflection/triggers/<stable_session_id>/
  state.json
  decisions.jsonl
  trigger.lock
```

其中 `<stable_session_id>` 使用现有稳定 ID 规则由 `session_id` 派生，避免原始 id 中的特殊字符进入路径。

写入约束：

- 所有 trigger state 必须以 `session_id` 为归属边界。
- 不存在一个跨 session 累加的 `global_trigger_state.json`。
- `session_reflection/jobs` 和 `session_reflection/runs` 可以继续保存 worker 执行记录，但不能成为 trigger counter 的事实来源。
- 前台 trigger 不等待后台 archive 完成；它可以先写 session-scoped trigger state / decision，archive 成功后再让这些记录和 session metadata 可关联。
- trigger state 与 archive 的关联只依赖 `session_id`；archive 失败不影响 trigger state 读写。
- `state.json` 使用 `atomic_write_json` 写入。
- `decisions.jsonl` 在持有 per-session lock 时 append。
- `decisions.jsonl` 第一版固定保留最近 200 条 decision；不新增 retention 配置。
- per-session lock 使用 `fcntl.flock` 风格的独立锁文件，不复用 `storage.py:file_lock`。

### 只用技术 High Watermark 防重复计数

同一个 session 可能有多次 Stop。为了避免重复统计同一批 message，需要在当前 session 的 trigger state 中记录一个轻量 high watermark。

主字段：

- `last_counted_byte_offset`

辅助字段：

- `last_counted_message_index`
- `last_counted_event_uid`

这个 high watermark 只用于计算 counter delta，不是 semantic checkpoint：

- child reflection 仍然拿当前 session 的完整上下文。
- 不按这个 index 切 prompt。
- 不用它判断“哪些内容语义上已经总结过”。

实现约束：

- Codex transcript 按 append-only 文本文件处理，增量扫描从 `last_counted_byte_offset` 开始。
- 成功统计新增片段后，把 `last_counted_byte_offset` 更新到当前文件末尾。
- 如果 transcript 不存在、不可读，或文件大小小于已记录 offset，decision 记录 warning，并只基于 Stop payload 中可用字段做保守判断。
- `last_counted_message_index` 和 `last_counted_event_uid` 只用于 debug / receipt，不作为第一版增量扫描主依据。

### Trigger State 更新必须加 Per-Session Lock

前台 Stop hook 会同步更新 trigger state。即使 Codex 通常串行调用 hook，trigger policy 也不能依赖外部串行保证。

同一个 session 的以下操作必须在 `trigger.lock` 下执行：

- 读取 `state.json`。
- 读取 transcript 增量并计算 counter delta。
- 更新 counters 和 high watermark。
- append decision。
- 创建 queued job 时写入 active job 信息和 counter snapshot。

锁规则：

- lock 只覆盖当前 session，不是全局锁。
- 如果 lock 被占用且未过 stale TTL，前台 Stop 仍返回 `{"continue": true}`。
- lock busy 时写一条 best-effort log，decision 可记为 `skip_reason = "trigger_lock_busy"`；不能阻塞 Codex 主流程。
- stale TTL 复用本仓库默认锁 TTL。

### 递归防护不阻止 Parent Session 后续 Review

必须防止 reflection child 自己再次触发 reflection，避免出现 `fork -> fork -> fork` 无限递归。

但这个防护不能阻止同一个 parent session 在后续 Stop 中再次触发 review。一个长 session 可能先做过一次 memory review，后面又经过几轮用户输入和工具调用，再次命中 nudge 阈值；这必须被允许。

因此防护分两层：

- recursion guard：阻止 child session 触发 reflection。
- concurrency / dedupe guard：阻止同一个 parent session 同时存在多个 active job，或同一个 Stop event 重复 enqueue。

recursion guard 应保留：

- `threadSource == "memory_consolidation"` 时 skip。
- 当前 `session_id` 命中 child thread registry 时 skip。
- 当前 transcript 中出现 `CSEP_REFLECTION_CHILD=1` 或 `CSEP_REFLECTION_JOB_ID=` marker 时 skip。

concurrency / dedupe guard 应只阻止：

- 同一个 `parent_session_id` 已有 `queued` / `running` job。
- 同一个 `stop_event_id` / `turn_id` 已经创建过 job。

已成功的 parent job 不能让后续 Stop 永久 skip。也就是说，已有 `succeeded` job 只能作为历史 receipt 和 reset 依据，不能作为“这个 parent session 不许再 review”的 guard 条件。

现有实现里如果存在 `parent_job_exists` 并把 `succeeded` 也视为 skip 条件，实现计划需要调整这部分逻辑。

### Active Job 不阻止继续累计新信号

如果同一个 parent session 已经有 `queued` / `running` reflection job，前台 Stop hook 不能再 fork 第二个 child，但仍然必须统计本次 Stop 的新增内容。

原因：

- child 在后台 review 时，parent session 可能继续发生新的用户输入、工具调用和 Stop。
- active job 只覆盖它创建时已经存在的上下文。
- 如果后续 Stop 直接 skip 且不更新 counters，child 启动后新增的内容可能被错误清零。

因此：

- active job 存在时，trigger policy 仍更新当前 session 的 counters 和 high watermark。
- decision 状态可以记录为 `deferred_active_job`，表示信号已累计，但没有再次 fork。
- reflection job 创建时必须记录覆盖范围，例如 `covered_message_index` 或 `covered_event_uid`。
- reflection job 创建时还必须记录 counter snapshot。
- job 成功后只按 counter snapshot reset 它覆盖范围内的 counters。
- job 创建后新增的内容继续留在 counters 里，后续 Stop 再按阈值判断。

counter snapshot 字段：

- `snapshot_stops_since_memory_review`
- `snapshot_readable_chars_since_memory_review`
- `snapshot_tool_calls_since_skill_review`

reset 算法：

```text
job 创建时：
  snapshot_stops = stops_since_memory_review
  snapshot_chars = readable_chars_since_memory_review
  snapshot_tools = tool_calls_since_skill_review

job 成功：
  if review_memory:
    stops_since_memory_review = max(0, stops_since_memory_review - snapshot_stops)
    readable_chars_since_memory_review = max(0, readable_chars_since_memory_review - snapshot_chars)
    last_memory_review_at = now

  if review_skills:
    tool_calls_since_skill_review = max(0, tool_calls_since_skill_review - snapshot_tools)
    last_skill_review_at = now

job 失败：
  counters 不变
```

`Nothing to save` 也按成功处理。partial failure 只 reset 成功 scope 对应的 counters。

### Active Job Stale 兜底

`queued` / `running` job 不能永久阻塞同一个 parent session 后续 review。

规则：

- active job 未过 stale TTL：不再 fork 新 child，但继续累计 counters，decision 记为 `deferred_active_job`。
- active job 超过 stale TTL：标记旧 job stale / failed，允许当前 Stop 在命中规则时创建新 job。
- stale TTL 第一版复用默认锁 TTL。
- status 中应展示 stale active job，方便排查后台 worker 是否卡住。

## 配置

第一版新增最小配置：

```toml
[session_reflection.trigger]
enabled = true
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 1800
```

字段语义：

- `enabled`：关闭后只 archive，不做 trigger 判断和 fork reflection。
- `memory_stop_interval`：当前 session 内，自上次 memory review 成功后累计 Stop 次数达到阈值时触发 memory review。
- `memory_context_chars`：当前 session 内，自上次 memory review 成功后，可读活动量达到阈值时触发 memory review。
- `skill_tool_call_interval`：当前 session 内，自上次 skill review 成功后，工具调用次数达到阈值时触发 skill review。
- `high_signal_immediate`：是否启用保守关键词立即触发。
- `skill_generation_mode`：默认 `one_shot_active`；可选 `evidence_first`。
- `active_job_stale_seconds`：`queued` / `running` reflection job 超过该时间后视为 stale，允许后续 Stop 重新触发。

`skill_generation_mode` 取值：

- `one_shot_active`：一次 session 中发现完整 workflow candidate，就可以直接创建 active skill。
- `evidence_first`：只记录 workflow evidence / skipped candidate，不立即发布 active skill。

第一版默认使用 `one_shot_active`，因为当前问题更偏向 skill 太难产生，而不是 skill 太多。

## 计数口径

### Stop Counter

每次 Stop hook 成功进入 trigger policy，当前 session 的 `stops_since_memory_review` 加一。

如果 memory review 成功完成并写 receipt，则按 reset 算法更新：

- `stops_since_memory_review`
- `readable_chars_since_memory_review`

如果 child review 判断 `Nothing to save`，也算成功评估，可以重置。

如果 fork、child review、receipt 写入或父进程校验失败，不重置计数器。

如果 memory review 成功时 parent session 已经继续累计新内容，reset 使用 job 创建时的 counter snapshot 做减法，而不是把 counter 直接清零。

### Readable Chars Counter

`readable_chars_since_memory_review` 只统计可读活动量，不统计完整 tool output。

计入：

- 新增 user message 文本。
- 新增 assistant message 文本。
- 新增 assistant tool call 的名称。
- 新增 assistant tool call 的参数摘要。

不计入：

- 完整命令输出。
- 测试日志。
- 长 diff。
- 长 JSON。
- 长网页内容。
- raw json。

原因是 trigger counter 只衡量“这个 session 是否足够活跃，值得评估一次”。完整 tool output 会被日志和长文件读取放大，容易误触发。

### Tool Call Counter

`tool_calls_since_skill_review` 统计当前 session 中新增的 assistant tool call 数量。

如果 skill review 成功完成并写 receipt，则按 reset 算法更新 `tool_calls_since_skill_review`。

如果结果是 `Nothing to save`，也算成功评估，可以重置。

如果 skill review 成功时 parent session 已经继续累计新工具调用，reset 使用 job 创建时的 `snapshot_tool_calls_since_skill_review` 做减法，而不是把 counter 直接清零。

## High Signal Keywords

第一版高信号立即触发只做保守关键词白名单，不做语义判断。

匹配原则：

- 只扫描当前 session 中新增的 user message。
- 不扫描 assistant message。
- 不扫描 tool output、命令日志、文件内容或网页内容。
- 英文关键词使用 word-boundary 正则并 case-insensitive，例如 `\bskill\b`。
- 中文关键词使用 literal substring matching。
- 命中只表示必须 fork review，不表示必须写入 memory / skill。
- decision 记录 `matched_keywords` 和短的 `matched_context_snippet`，用于排查误触发。
- 第一版接受少量中文误触发；child Codex 仍会做语义过滤，可以输出 `Nothing to save`。

关键词约 30 个：

```text
记住
记录一下
下次
以后不要
不要再
规则
约定
偏好
习惯
memory
skill
技能
工作流
workflow
复用
沉淀
SOP
runbook
交接
handoff
status
收尾
状态文档
不是这个意思
你理解错了
不要改代码
别改代码
恢复
回退
过度抽象
```

建议分组：

- memory-oriented：`记住`、`记录一下`、`下次`、`以后不要`、`不要再`、`规则`、`约定`、`偏好`、`习惯`、`memory`。
- skill-oriented：`skill`、`技能`、`工作流`、`workflow`、`复用`、`沉淀`、`SOP`、`runbook`。
- handoff/status：`交接`、`handoff`、`status`、`收尾`、`状态文档`。
- correction/friction：`不是这个意思`、`你理解错了`、`不要改代码`、`别改代码`、`恢复`、`回退`、`过度抽象`。

触发 scope：

- 命中 memory-oriented、handoff/status、correction/friction：触发 memory review。
- 命中 skill-oriented：触发 skill review，同时允许 child 判断是否也有 memory 值得写。
- 如果同一 Stop 同时命中多个分组，只创建一个 reflection job，job 中同时带 `review_memory` 和 `review_skills`。
- slash command 本身不作为关键词来源；如果 slash command 后面带有用户自然语言参数，只扫描参数文本。

第一版暂不纳入：

- 稳定环境事实。
- 工具坑。
- 正确测试命令。
- 仓库约定。
- root cause。
- 设计决策。
- 验证命令。
- 代码或文档变更并完成验证。

这些内容仍然可以在 nudge 配额触发后由 child Codex 判断是否值得保存。

## Trigger Decision

trigger policy 输出机器可读 decision。

未触发：

```json
{
  "schema_version": 1,
  "status": "archive_only",
  "review_memory": false,
  "review_skills": false,
  "trigger_reasons": [],
  "skip_reason": "below_threshold",
  "counters": {
    "stops_since_memory_review": 1,
    "readable_chars_since_memory_review": 4200,
    "tool_calls_since_skill_review": 6
  }
}
```

触发：

```json
{
  "schema_version": 1,
  "status": "queued",
  "review_memory": true,
  "review_skills": false,
  "trigger_reasons": ["memory_stop_interval"],
  "matched_keywords": [],
  "counters": {
    "stops_since_memory_review": 3,
    "readable_chars_since_memory_review": 8200,
    "tool_calls_since_skill_review": 4
  }
}
```

触发原因使用稳定字符串：

- `memory_stop_interval`
- `memory_context_chars`
- `skill_tool_call_interval`
- `high_signal_memory_keyword`
- `high_signal_skill_keyword`
- `active_job_running`

`skip_reason` 使用稳定字符串：

- `disabled`
- `below_threshold`
- `recursion_guard`
- `active_job_running`
- `trigger_lock_busy`
- `stop_event_already_seen`
- `transcript_unreadable`

## Trigger State Schema

建议 logical schema：

```json
{
  "schema_version": 1,
  "session_id": "019e...",
  "last_counted_byte_offset": 123456,
  "last_counted_message_index": 42,
  "last_counted_event_uid": "event-...",
  "stops_since_memory_review": 2,
  "readable_chars_since_memory_review": 9300,
  "tool_calls_since_skill_review": 11,
  "last_memory_review_at": "2026-05-15T12:00:00Z",
  "last_skill_review_at": null,
  "active_job_id": null,
  "last_decision": {
    "status": "archive_only",
    "trigger_reasons": []
  },
  "updated_at": "2026-05-15T12:30:00Z"
}
```

`reflection_trigger_decisions` 建议 append-only 保存每次 Stop 的 decision snapshot，方便排查：

```json
{
  "schema_version": 1,
  "session_id": "019e...",
  "stop_event_id": "stop-...",
  "created_at": "2026-05-15T12:30:00Z",
  "decision": {
    "status": "queued",
    "review_memory": true,
    "review_skills": true,
    "trigger_reasons": ["high_signal_skill_keyword"],
    "matched_keywords": ["skill", "工作流"]
  }
}
```

## Reflection Job Contract

如果 decision 为 `queued`，Stop hook 创建一个 session reflection job。job 必须包含 trigger decision：

```json
{
  "schema_version": 1,
  "job_id": "20260515T123000Z-abcdef",
  "parent_session_id": "019e...",
  "review_memory": true,
  "review_skills": true,
  "trigger_reasons": ["high_signal_skill_keyword"],
  "skill_generation_mode": "one_shot_active",
  "covered_message_index": 42,
  "covered_event_uid": "event-...",
  "covered_byte_offset": 123456,
  "counter_snapshot": {
    "stops_since_memory_review": 2,
    "readable_chars_since_memory_review": 9300,
    "tool_calls_since_skill_review": 11
  },
  "trigger_decision": {
    "status": "queued",
    "matched_keywords": ["skill"]
  }
}
```

worker 对 child Codex 的 prompt 必须明确：

- 这次 review 是 trigger 命中后的评估，不是强制写入。
- 如果没有长期价值，输出 `Nothing to save` 并写 receipt。
- 只有 `review_memory = true` 时评估 memory。
- 只有 `review_skills = true` 时评估 skill。
- 如果 `review_skills = true` 且发现完整 workflow candidate，默认允许一次生成 active skill。
- `skill_generation_mode` 由 worker 从 job JSON 注入 prompt；它不影响 trigger decision，只影响 child 如何处理 workflow candidate。

同一个 Stop 即使命中 memory 和 skill，也只 fork 一个 child Codex，避免重复读取同一 session。

## Skill 生成边界

skill review 比 memory 更严格，但默认不要求同类 workflow 在多个 session 重复出现。

默认模式 `one_shot_active`：

- 一次 session 中发现完整 workflow candidate，就可以创建 active skill。
- active skill 必须通过 managed skill schema / publish 校验。
- active skill 必须使用既有 `csep-reflect-*` 命名空间。

active skill 必须满足：

- 有清晰触发条件。
- 有明确输入。
- 有可执行 workflow。
- 有验证步骤。
- 有失败处理。
- 能解释为什么这是 skill，而不是 memory。
- 不包含敏感信息。

不能生成 active skill 的内容：

- 一次性事实。
- 用户偏好。
- 临时状态。
- 单个命令片段。
- 单个 repo 当前状态。
- 敏感信息。
- 没有稳定触发条件的经验。

如果只有弱证据，child Codex 应写 skipped candidate / evidence，不硬凑 skill。

## Reset 与失败处理

计数器 reset 只发生在对应 review 成功完成之后。

成功定义：

- child review 正常结束。
- receipt 写入成功。
- parent 对 receipt 和产物完成后验校验。
- 如果没有长期价值，receipt 中明确记录 `no_durable_signal` 或 `Nothing to save`。

失败时：

- 不 reset 对应 counters。
- decision 和错误写入当前 session 的 reflection receipt / run log。
- 前台 Stop 仍必须快速返回，不阻塞 Codex。
- 下一次 Stop 可以再次触发补评估。

如果只评估 memory 成功、skill 失败：

- 按 snapshot 更新 memory counters。
- 不 reset skill counters。
- receipt 必须记录 partial failure。

状态对照：

| 结果 | Reset 行为 |
| --- | --- |
| child 正常结束，receipt 合法，产物校验通过，写入 memory / skill | reset 成功 scope 的 counter snapshot |
| child 正常结束，receipt 合法，结果为 `Nothing to save` | reset 本次评估 scope 的 counter snapshot |
| receipt 缺失、JSON 非法或 schema validation 失败 | 不 reset |
| memory 产物通过、skill 产物失败 | reset memory snapshot，不 reset skill snapshot |
| skill 产物通过、memory 产物失败 | reset skill snapshot，不 reset memory snapshot |
| child 超时或 app-server / fork / turn 失败 | 不 reset |

## 可观测性

`codex-self-evolution status` 后续应能展示 trigger policy 的最小状态：

- 当前 session trigger state snapshot。
- 当前 session 最近 decision。
- 最近 24 小时 archive-only 次数。
- 最近 24 小时 queued reflection jobs。
- 最近 24 小时 trigger reasons。
- 最近 24 小时 failed / stale review jobs。

receipt 中必须记录：

- `session_id`
- `stop_event_id`
- `trigger_reasons`
- counters snapshot
- matched keywords
- review scopes
- memory changes
- skill changes
- skipped candidates
- errors

这些字段用于解释“为什么这次 Stop 触发了 / 为什么没触发 / 为什么没写东西”。

## 测试策略

第一版应补充聚焦测试：

- archive-only：低于所有阈值时不创建 reflection job。
- memory stop interval：当前 session 内累计 Stop 达到 3 次时触发 memory review。
- memory context chars：可读活动量达到 16000 chars 时触发 memory review。
- readable chars：完整 tool output 不计入 `readable_chars_since_memory_review`。
- skill tool calls：工具调用累计达到 15 次时触发 skill review。
- high signal keywords：只扫描 user message，且英文关键词 case-insensitive。
- high signal scope：skill-oriented 关键词触发 skill review；memory-oriented 关键词触发 memory review。
- session scope：两个 session 的 counters 不互相影响。
- session resume：同一个 `session_id` resume 后继续累计。
- cwd switch：同一个 session 内切换 cwd 不重置 counters。
- child session：reflection child 不写 trigger state。
- per-session lock：同 session 并发 Stop 不重复计数。
- trigger lock busy：lock 被占用时 Stop 不阻塞，decision / log 记录 `trigger_lock_busy`。
- reset success：`Nothing to save` 也会 reset 对应 counters。
- reset snapshot：active job 期间新增内容不会被 job success 清零。
- reset failure：fork / receipt / validation 失败不 reset counters。
- partial failure：memory 成功但 skill 失败时只按 snapshot 更新 memory counters。
- stale active job：`queued` / `running` 超过 stale TTL 后下一次 Stop 可重新 enqueue。
- keyword matching：英文关键词使用 word boundary；中文关键词 substring；记录 matched snippet。
- decision output：decision JSON 字段稳定，适合日志和 status 展示。

测试命令按本仓库现状使用：

```bash
uv run pytest -q
```

## 迁移与兼容

第一版不要求迁移历史 session 的 trigger state。历史 session 仍可被 recall / archive 检索，但不会补算 trigger counters。

如果旧 session 没有 `reflection_trigger_state`：

- 首次 Stop 时创建新的 session-scoped trigger state。
- counters 从当前 Stop 看到的新增消息开始累计。
- 不回扫历史消息强行触发。

旧 session reflection worker 设计中的 `session_reflection/jobs` 和 `session_reflection/runs` 仍可用于 worker 执行记录；本设计只收窄 trigger counter 的归属边界。

## 现有实现需调整的代码点

实现计划必须覆盖以下代码衔接：

- `src/codex_self_evolution/session_reflection/state.py`：`FINDABLE_PARENT_STATUSES` 不能把 `succeeded` 用作 guard skip 条件；新增 `find_active_parent_job`，只匹配 `queued` / `running` 且未 stale 的 job。
- `src/codex_self_evolution/session_reflection/guard.py`：`parent_job_exists` 语义拆开。recursion guard 只防 child；active parent job 只导致 `deferred_active_job`，不能阻止 counter 更新。
- `src/codex_self_evolution/session_reflection/state.py:create_job_from_payload`：扩展 job schema，写入 `trigger_decision`、`review_memory`、`review_skills`、`skill_generation_mode`、`covered_*` 和 `counter_snapshot`。
- `src/codex_self_evolution/session_reflection/prompt.py`：prompt 接收 review scope、trigger reasons 和 `skill_generation_mode`。
- 新增 `src/codex_self_evolution/session_reflection/trigger.py`：负责 trigger state sidecar、per-session lock、delta 统计、关键词匹配、decision 输出和 counter reset。
- `src/codex_self_evolution/cli.py`：Stop hook 前台先执行 trigger policy；只在 decision queued 时 enqueue / spawn reflection worker；archive 仍后台执行。
- `src/codex_self_evolution/session_reflection/runner.py`：job success / partial / failure 后调用 trigger reset 逻辑，按 counter snapshot 减法更新 session trigger state。
- 不复用 `src/codex_self_evolution/storage.py:file_lock` 作为 trigger state lock；该锁的 TOCTOU 修复作为独立维护项处理。

## 成功标准

实现完成后应满足：

- Stop hook 始终触发后台 archive；前台 trigger 不等待 archive 完成。
- 低信号 Stop 不 fork reflection。
- 命中阈值或保守关键词时，能创建一个包含 trigger decision 的 reflection job。
- 同一 session 内 counters 正确累计和 reset。
- 不同 session counters 互不影响。
- child Codex 可以基于 trigger scope 做 memory / skill review。
- `one_shot_active` 模式下，完整 workflow candidate 可以一次生成 active skill。
- `Nothing to save` 被视为成功评估，不会导致下一次 Stop 立即重复评估同一信号。

## 一句话总结

CSEP 不照搬 Hermes 的 10-turn nudge，而是采用 Codex-tuned nudge：每次 Stop 都 archive，但只有当前 session 内的 Stop 次数、可读活动量、工具调用密度或保守用户关键词命中时，才 fork 当前 session 做 memory / skill review；触发只保证评估，不保证写入，完整 workflow 默认可以一次生成 active skill。
