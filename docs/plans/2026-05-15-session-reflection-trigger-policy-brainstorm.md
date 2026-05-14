# Session Reflection Trigger Policy Brainstorm

日期：2026-05-15

## 当前现场

当前仓库已切回最新 `main`：

- 分支：`main`
- HEAD / `origin/main`：`79bb37b docs: trim session reflection plan whitespace`
- session reflection worker 已合入 `main`

因此，本讨论基于最新 `main` 的 app-server fork 方案继续。

如果本文和早期 `brainstorm.md` 中“轻 review pass / 不 fork 当前 agent”的判断冲突，以本文为准。早期内容是探索记录，本文是当前收敛方向。

## 目标

新增一套轻量规则系统，判断每次 Codex `Stop` 时是否要触发一轮自我进化评估：

- 是否总结 memory
- 是否产生或更新 skill
- 是否只归档，不做总结

核心问题不是“每次 Stop 都产出东西”，而是：

> 每次 Stop 都归档，但只有在规则命中时才 fork 当前 session 做 memory / skill review；review 成功后也允许判断为 `Nothing to save`。

## Hermes 参考结论

Hermes 的 memory / skill review 不是复杂 checkpoint 系统，而是简单 nudge：

- memory：每 `nudge_interval` 个 user turn 触发一次，默认 10。
- skill：每 `creation_nudge_interval` 个工具迭代触发一次，默认 10。
- 触发后 fork 一个后台 agent，给它当前完整 `messages_snapshot`。
- review prompt 明确要求：如果没有值得保存的内容，输出 `Nothing to save.`。

Hermes 的“增量”只体现在消息持久化时用 `_last_flushed_db_idx` 防重复写 DB，不是语义 reflection checkpoint。

对 CSEP 的结论：

- 不做复杂的 `last_successful_reflection_checkpoint`。
- 不做“增量窗口”语义切片。
- 采用 Hermes 风格 nudge，但参数和触发条件要适配 Codex。

## Codex 和 Hermes 的差异

不能直接照搬 Hermes 的 `10 user turns`，因为 Codex session 常见形态不同：

- 很多 Codex session 只有 1 到 3 个 user turn。
- 一个 user turn 内可能读很多文件、跑很多命令、改代码、修测试。
- “回合数少”不代表“信号少”。
- 工具调用密度和 transcript 增长量比 user turn 更能代表 Codex 的工作量。

因此，Codex 的 trigger policy 需要同时看：

- Stop / user turn 次数
- transcript 可读内容增长量
- 工具调用次数
- 高信号关键词或人机摩擦

## 已收敛的触发语义

触发规则只负责决定是否 fork 当前 session；不负责直接决定写 memory 或 skill。

```text
Codex Stop
  -> archive 当前 session
  -> 更新轻量计数器
  -> deterministic trigger policy 判断 review_memory / review_skills
  -> 如果未触发：archive_only
  -> 如果触发：fork 当前 session
  -> child Codex 做语义评估
  -> 有价值才写 memory / skill
  -> 没价值则 receipt 记录 no_durable_signal / Nothing to save
```

这意味着：

- 规则系统不先调用 LLM 做 eligibility 判断。
- fork 出来的 Codex child 才做语义判断。
- 配额型触发只保证“认真评估一次”，不保证一定写入长期资产。
- 写入仍必须过 memory / skill 的质量门槛。

## 已确认决策

### 1. Trigger state 按 session 维护

计数器按当前 Codex session 维护，不做全局计数，也不按 repo / bucket 共享计数。

原因：

- memory / skill review 的输入是当前 session fork，触发状态也应该和这个输入边界一致。
- 跨 session 计数会把不相关的短任务混在一起，导致 review 语义变脏。
- 某个 session 太短、没有触发 review 是可以接受的；它仍然会被 archive，后续可以由 recall 或批处理机制再挖掘。

因此：

- Stop 只更新当前 session 的 trigger counters。
- 成功评估只 reset 当前 session 的对应计数器。
- 不从其他 session、repo bucket 或全局状态继承 trigger 进度。

### 2. `readable_chars_since_memory_review` 只统计可读活动量

`readable_chars_since_memory_review` 不统计完整 tool output。

建议口径：

- 统计 user message 文本。
- 统计 assistant message 文本。
- 统计 assistant tool call 的名称和参数摘要。
- 不统计完整命令输出、测试日志、长 diff、长 JSON、长网页内容。

原因：

- trigger counter 只需要衡量“这段 session 是否足够活跃，值得评估一次”。
- 完整 tool output 很容易被日志、测试失败、长文件读取放大，导致误触发。
- 完整上下文仍然会被 archive；如果规则触发，child Codex 可以基于当前 session 做语义评估，不需要 trigger counter 承担语义判断。

### 3. Trigger state 跟 session archive 落盘

session scoped trigger state 放在对应 session archive 的 metadata 旁边，不维护跨 session 的 trigger state 文件。

原因：

- trigger state 只解释“当前 session 的这次 Stop 为什么触发或没触发”。
- 计数器已经确定按 session 维护，落盘位置也应该跟 session 边界一致。
- 单独放到 `session_reflection/` 这类全局目录，容易重新引入跨 session 聚合和清理问题。

建议 archive 结构表达为：

```text
session archive
  - transcript / messages
  - metadata
  - reflection_trigger_state
  - reflection_receipts
```

其中：

- `reflection_trigger_state` 记录 Stop 时的 counters 和 decision。
- `reflection_receipts` 记录 forked child review 的执行结果。
- 这些信息用于排查和回放，不作为其他 session 的触发输入。

## 推荐默认规则

### 1. Memory nudge

建议默认：

```toml
memory_stop_interval = 3
memory_context_chars = 16000
```

触发条件：

- 自上次 memory review 成功后，累计 `3` 次 Stop / user turn。
- 或自上次 memory review 成功后，transcript 可读文本增长超过 `16000` chars。

原因：

- Codex 很多 session 不会到 10 个 turn。
- 3 次 Stop 足够避免长期漏记。
- 16k chars 可以覆盖“单轮很长、工具很多”的情况。

### 2. Skill nudge

建议默认：

```toml
skill_tool_call_interval = 15
```

触发条件：

- 自上次 skill review 成功后，工具调用累计超过 `15` 次。

原因：

- skill 更关心“做法是否可复用”。
- 对 Codex 来说，工具调用次数比 user turn 更贴近实际 workflow 复杂度。
- 单次工具密集排查、冲突解决、测试修复，都可能只有一个 user turn，但有 skill 价值。

### 3. 高信号立即触发

建议默认：

```toml
high_signal_immediate = true
```

第一版只做保守关键词白名单，先不做语义信号触发。

匹配原则：

- 只扫描当前 session 中新增的 user message。
- 不扫描 assistant message、tool output、命令日志、文件内容或网页内容。
- 使用 literal substring matching；英文关键词做 case-insensitive。
- 命中只表示必须 fork review，不表示必须写入 memory / skill。

建议关键词白名单约 30 个：

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

暂不纳入第一版立即触发：

- “出现稳定环境事实、工具坑、正确测试命令、仓库约定”这类语义判断。
- “出现明确 root cause、设计决策、验证命令”这类语义判断。
- “有代码/文档变更并完成验证”这类行为判断。

这些内容仍然可以在 nudge 配额触发后由 child Codex 判断是否值得保存。

高信号触发不代表强制写入，只代表必须 fork review。

## Trigger 决策输出

trigger policy 应输出机器可读 decision，方便调试和评估：

```json
{
  "status": "queued",
  "review_memory": true,
  "review_skills": false,
  "trigger_reasons": ["memory_stop_interval"],
  "counters": {
    "stops_since_memory_review": 3,
    "readable_chars_since_memory_review": 8200,
    "tool_calls_since_skill_review": 4
  }
}
```

如果没有触发：

```json
{
  "status": "archive_only",
  "review_memory": false,
  "review_skills": false,
  "trigger_reasons": [],
  "skip_reason": "below_threshold"
}
```

如果触发但 child 判断没有长期价值：

```json
{
  "status": "succeeded",
  "review_memory": true,
  "review_skills": true,
  "trigger_reasons": ["tool_call_interval"],
  "memory_changes": [],
  "skill_changes": [],
  "skipped_candidates": [
    {
      "reason": "no_durable_signal",
      "summary": "No memory or workflow worth saving."
    }
  ]
}
```

## 状态维护原则

只在当前 session 内维护简单计数器，不做复杂 checkpoint。

建议状态：

- `stops_since_memory_review`
- `readable_chars_since_memory_review`
- `tool_calls_since_skill_review`
- `last_memory_review_at`
- `last_skill_review_at`
- `last_trigger_reasons`

计数器 reset 规则：

- review 成功完成并写 receipt 后，重置对应计数器。
- 即使结果是 `Nothing to save`，也算成功评估，可以重置。
- 如果 fork / child review 失败，不重置，下一次 Stop 可继续补评估。

## Skill 质量边界

skill review 比 memory 更严格。

默认策略应该允许一次 review 就产生 active skill。

原因：

- CSEP 的目标之一是让 Codex 更容易沉淀可复用能力。
- 过去的问题更偏向 skill 太难产生，而不是 skill 过多。
- 如果必须跨多次 session 重复出现才生成 skill，很多低频但明确的本地流程会一直沉淀不下来。

因此，第一版建议：

- 默认模式：`one_shot_active`，一次 session 中发现完整 workflow candidate，就可以创建 active skill。
- 可选模式：`evidence_first`，只记录 workflow evidence，不立即发布 active skill，供后续更保守场景使用。
- 无论哪种模式，弱证据都可以记录为 skipped candidate / evidence，但不是默认路径。

active skill 的门槛不看“出现了几次”，而看“是不是一个完整 workflow”：

- 一次性事实不能生成 skill。
- 用户偏好不能生成 skill。
- 临时状态不能生成 skill。
- 敏感信息不能进入 skill。
- active skill 必须有清晰触发条件、输入、工作流、验证和失败处理。
- active skill 必须能通过 managed skill schema / publish 校验，避免产生坏格式 skill。

如果只有零散线索，比如一句偏好、一个 repo 状态、一个临时命令，child Codex 应该输出 skipped candidate，而不是硬凑 skill。

## 不做的事

当前收敛为不做：

- 不做每次 Stop 都 reflection。
- 不做 LLM 前置 eligibility 判断。
- 不做复杂增量窗口 checkpoint。
- 不做按 message index 切片的 semantic checkpoint。
- 不做“配额命中就强制写 memory / skill”。

## 当前推荐

先实现最小 nudge policy：

```toml
[session_reflection.trigger]
enabled = true
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
```

执行策略：

- 每次 Stop 都 archive。
- trigger policy 是 deterministic。
- 命中才 fork 当前 session。
- forked Codex child 决定是否真的写 memory / skill。
- receipt 必须记录触发原因、计数器、写入结果和 skipped reason。

一句话总结：

> CSEP 不照搬 Hermes 的 10-turn nudge，而是采用 Codex-tuned nudge：少量 Stop、较大上下文增长、工具调用密度和高信号摩擦共同触发 forked reflection；触发只保证评估，不保证产出。
