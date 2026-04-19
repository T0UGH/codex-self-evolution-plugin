---
title: Codex 自我进化插件设计 Phase 2（Runtime Abstractions）
date: 2026-04-19
status: draft
project: codex
supersedes: docs/implementation-plans/2026-04-18-codex-self-evolution-plugin-implementation-plan.md
related:
  - docs/specs/2026-04-18-codex-self-evolution-plugin-design.md
  - docs/2026-04-19-implementation-gap-audit.md
---

# Codex 自我进化插件设计 Phase 2（Runtime Abstractions）

## 1. 这份文档的目的

这不是重写整份 v1 design，而是在已确认的实现偏移基础上，补一份更贴近真实落地的 **Phase 2 运行时设计**。

它服务两个目标：

1. 把 **Phase 1 没做好的 8 个点** 全部补齐为明确的实现目标
2. 把这次新确认的 3 个架构分叉点正式写死：
   - reviewer 走 provider abstraction
   - scheduler 走 `launchd`
   - compiler 走 pluggable backends

一句话说：

> **第一版 design 定义“要做什么”；这份 Phase 2 design 定义“这些东西现在到底该怎么落成真的 runtime”。**

---

## 2. 当前状态重述

当前仓库不应继续被表述为“design-aligned v1 已完成”。

更准确的状态是：

> **self-evolution pipeline skeleton / v1-alpha 已完成，但 8 个关键 runtime gap 尚未补齐。**

这 8 个点分为两类。

### 2.1 三个主链路偏移
1. reviewer 还不是真 reviewer runtime
2. compiler 还没有真实 scheduler runtime
3. compiler 还不是可插拔的多实现 runtime

### 2.2 五个配套偏移
4. `SessionStart` 还没有真正注入 `USER.md` / `MEMORY.md`
5. suggestion/event store 还不是显式状态机
6. review snapshot reconstruction 还没有真正实现
7. recall 还没有 in-turn 条件触发 runtime
8. managed skill 的 ownership boundary 还比较薄

---

## 3. Phase 2 的核心原则

### 3.1 不推翻 v1 design，只把 runtime 补真
这次不是要重开产品方向，而是要把原 design 里的几个“默认可行”点变成真正的接口、边界、调度和状态模型。

### 3.2 前台提案、后台沉淀，写权边界不变
仍然保持：
- 前台回合只负责提案 / 触发
- 最终写入权只属于 `compiler -> writer`
- reviewer 可以更真，但不能越权写最终资产

### 3.3 运行时抽象优先于 provider / agent 绑定
这次新确认的核心原则是：
- reviewer 不绑定单一模型供应商
- compiler 不绑定单一实现方式
- scheduler 不直接等价于 compile runtime

也就是：
> **先抽象运行边界，再挂具体实现。**

### 3.4 空队列不拉重 runtime
既然 scheduler 选定 `launchd`，而 compile 又可能拉起 `opencode`，就必须增加一层 cheap preflight：

- 先判断有没有待处理 suggestion
- 没有则快速退出
- 有才进入真正 compile path

### 3.5 agent backend 是可插拔增强，不是默认唯一实现
compiler 即使支持 `opencode` agent backend，系统也必须保留：
- script backend 可独立运行
- agent backend 不可用时稳定回退
- 输出契约与写入边界一致

---

## 4. Phase 2 确认后的系统结构

Phase 2 之后，运行时结构应变成下面这样：

```text
SessionStart
  -> load USER.md + MEMORY.md + recall policy
  -> emit stable background payload

Turn completes
  -> Stop hook emits minimal trigger envelope

Review input builder
  -> reconstruct transcript/thread/memory/skills snapshot

Reviewer runtime
  -> choose provider adapter
  -> run one bounded review pass
  -> emit structured suggestions

Suggestion store
  -> persist suggestions with explicit states

Launchd wakeup
  -> preflight check queue
  -> if empty: skip fast
  -> if non-empty: launch compiler backend

Compiler backend
  -> script backend OR opencode agent backend
  -> normalize / merge / promote

Writer
  -> sole owner of final assets

Next session / turn
  -> background memory returns
  -> recall may trigger in-turn
  -> managed skill may be reused
```

---

## 5. 三个新架构分叉点的正式设计

## 5.1 Reviewer provider abstraction

### 设计目标
reviewer 需要是真实 runtime，但不能把 `review/runner.py` 绑死到单一 provider。

### 统一接口
reviewer runtime 应依赖一个窄接口，例如：

```text
ReviewProvider.run(snapshot, prompt, options) -> ProviderResult
```

其中：
- `snapshot`：标准化后的 review snapshot
- `prompt`：固定 reviewer prompt
- `options`：模型、timeout、temperature、max_tokens 等约束
- `ProviderResult`：原始文本、结构化文本、usage、provider metadata、错误信息

### 首批支持的格式
Phase 2 先要求支持两种 payload dialect：

1. **OpenAI-compatible / OpenAPI-style chat-completions**
2. **Anthropic-style messages**

注意，这里是“格式适配层”，不是必须绑定某一家平台。

### 非目标
Phase 2 不要求一次性支持：
- 所有 CLI 包装器
- 多轮 reviewer 对话
- reviewer 自主工具调用

reviewer 仍然是：
> **单次、受限、固定 schema 输出的 runtime。**

### 失败语义
reviewer provider 必须有明确失败语义：
- provider unavailable
- timeout
- malformed JSON
- schema validation failed
- empty output

这些都不能伪装成成功 suggestion。

---

## 5.2 Launchd + preflight scheduler split

### 设计目标
scheduler 不是“定时跑 compile”，而是：

1. `launchd` 定时唤醒
2. 先执行轻量 preflight
3. 只有在存在待处理工作时，才进入真正 compile runtime

### 推荐结构
拆成两个逻辑层：

#### A. preflight checker
职责：
- 检查是否存在 `pending` 或可重试 suggestion
- 判断 compile lock 是否健康
- 判断是否值得拉起 compiler backend
- 输出三种结论：
  - `skip_empty`
  - `skip_locked`
  - `run`

#### B. compile invoker
职责：
- 在 preflight 返回 `run` 时，启动指定 compiler backend
- backend 可以是：
  - `script`
  - `agent:opencode`

### 为什么这样拆
因为如果每次 launchd 唤醒都直接拉起 `opencode`，空队列场景会非常浪费，也会让调度层语义不清晰。

### 锁语义
Phase 2 需要补成显式行为：
- lock healthy -> skip 并记录原因
- lock stale -> 尝试恢复或安全失败
- compile 完成 -> 释放 lock

### 可观测性
scheduler / preflight / compile 应分别有清楚的 receipt 或 log 语义，至少能回答：
- 这次为什么没跑
- 这次为什么跑了
- 跑的是哪个 backend
- 处理了多少 suggestion

---

## 5.3 Compiler backend abstraction

### 设计目标
compiler 不再被视为单一实现，而是共享同一写入契约下的多个 backend。

### 统一 contract
可抽象为：

```text
CompilerBackend.compile(batch, context, options) -> CompileResult
```

其中：
- `batch`：待处理 suggestion 集合
- `context`：memory/recall/skills 当前状态、manifest、配置、repo/cwd 信息
- `options`：backend-specific 参数
- `CompileResult`：promotion decisions、merged artifacts、discard/failure info、receipts

### 首批 backend
#### 1. Script compiler
- 本地 Python 规则编译器
- 负责 deterministic fallback
- 是最基础、最稳的 backend

#### 2. Agent compiler (`opencode`)
- 启动一个 bounded `opencode` agent
- 只负责受限 compile 工作
- 不直接拥有最终写权

### agent backend 的边界
即使启用 `opencode` backend，也必须保持：
- 只能处理限定输入 batch
- 只能输出约定结构
- 最终落盘仍走 `writer.py`
- backend 异常时可回退到 script compiler

### 推荐职责划分
更稳妥的做法不是让 agent 全接管 compiler，而是让它参与局部环节，例如：
- recall candidate 归并
- memory update 去重 / 归一
- managed skill patch 建议草案

而：
- state transition
- final writer
- ownership check
- manifest mutation

这些仍应由本地脚本层保底。

---

## 6. 把 Phase 1 没做好的 8 个点全部补成明确设计

## 6.1 Gap 1：reviewer 不是 stub，而是真实 provider-backed reviewer runtime

### Phase 2 设计
- reviewer runner 必须调用 provider adapter
- 不能再依赖 payload 里的 `reviewer_output` 作为主路径
- fixture/stub 只能作为测试替身存在

### 完成定义
- 无预制 reviewer_output 时也能真实跑 review
- malformed output 会被结构化拒绝
- schema 校验与 provider 调用解耦

---

## 6.2 Gap 2：compiler 必须有真实 scheduler runtime

### Phase 2 设计
- 官方 scheduler 路线为 `launchd`
- `launchd` 不直接等价于 compile
- 必须存在 preflight check
- 只有有活时才启动 compile invoker

### 完成定义
- 空队列唤醒能快速退出
- lock 冲突不会只抛异常了事
- scheduler 行为可观测、可解释

---

## 6.3 Gap 3：compiler 必须支持多 backend，而不只是一份本地规则脚本

### Phase 2 设计
- compiler 抽象为 backend contract
- 至少支持 `script` 与 `agent:opencode`
- 默认有 deterministic fallback

### 完成定义
- backend 可通过配置或 CLI 选择
- agent backend 不可用时系统仍能工作
- 不同 backend 的输入输出契约一致

---

## 6.4 Gap 4：`SessionStart` 必须真实注入 `USER.md` / `MEMORY.md`

### Phase 2 设计
`SessionStart` 输出不再只是 recall policy，而应包含：
- 用户稳定偏好背景
- 长期环境 / 约定背景
- recall policy
- 最小运行时元信息

### 约束
- 不预加载大量 recall material
- 不在 session start 直接做 recall 查询
- 保持 deterministic template

### 完成定义
- `USER.md` / `MEMORY.md` 真被读取并进入 session start payload
- 回归测试可断言其结构与字段

---

## 6.5 Gap 5：suggestion store 必须升级成显式状态模型

### Phase 2 设计
即使底层仍是文件系统，也要先补显式状态语义：
- `pending`
- `processing`
- `done`
- `failed`
- `discarded`

并补：
- stable id / idempotency key
- failure reason
- retry metadata
- archival provenance

### 非目标
Phase 2 不强制要求上数据库。

### 完成定义
- state transition 有明确规则
- compiler receipt 能解释每条 suggestion 的结局
- 幂等重跑不重复 promotion

---

## 6.6 Gap 6：review snapshot reconstruction 必须真正实现

### Phase 2 设计
review input builder 要从 trigger envelope 主动补拉：
1. transcript / transcript path
2. `thread/read(includeTurns=true)`
3. `USER.md`
4. `MEMORY.md`
5. managed skills summary
6. repo/cwd/session metadata

并统一归一成标准 review snapshot。

### 设计关键点
- hook payload 只是 trigger，不是真相本体
- source authority 必须明确
- 应保存 normalized review-input artifact 便于测试与排障

### 完成定义
- review 输入不再依赖“外部先帮你准备好了全量 payload”
- snapshot 构建有稳定顺序与调试产物

---

## 6.7 Gap 7：recall 必须具备 in-turn 条件触发 workflow

### Phase 2 设计
recall 继续保留显式 CLI，但还要新增：
- policy-driven trigger
- in-turn bridge
- focused recall reconstruction

### 行为要求
- 默认 same-repo 优先
- 其次 same-cwd subtree
- 再 global fallback
- 输出 focused recall，而不是整段历史倾倒

### 完成定义
- recall 不再只是一条手工 CLI
- 但也不是 session start 大量预加载

---

## 6.8 Gap 8：managed skill ownership boundary 必须加固

### Phase 2 设计
managed skill 至少需要补齐以下边界：
- 独立存储路径或足够明确的路径隔离
- manifest 元数据：
  - `owner`
  - `managed`
  - `created_by`
  - `updated_at`
  - `retired_at`
- 自动 create / patch / edit / retire 仅限 plugin-owned managed skills

### 完成定义
- agent 或 compiler 不能误改用户 skill / 第三方 skill
- 生命周期操作有 manifest 级别的治理证据

---

## 7. 推荐的实现顺序（与 plan 对齐）

### Phase A：先把 review 输入链路补真
对应：
- Gap 4
- Gap 6
- Gap 1

原因：
如果 `SessionStart`、snapshot、reviewer 还都是半假的，后面的 compiler 再强也只是消费低质量 suggestion。

### Phase B：再把 compiler runtime 补真
对应：
- Gap 5
- Gap 2
- Gap 3

原因：
这一步解决“怎么稳定地沉淀”。

### Phase C：最后把 recall / skill 的运行边界补真
对应：
- Gap 7
- Gap 8

原因：
这一步解决“怎么稳定地回流与复用”。

---

## 8. Phase 2 之后，什么才算真正进入 design-aligned v1

只有当下面这些条件同时满足，才能不再叫 v1-alpha：

1. `SessionStart` 真正注入 `USER.md` / `MEMORY.md` / recall policy
2. `Stop` 只负责 trigger，review input 由 builder 真正重建
3. reviewer 能通过 provider abstraction 调真实模型格式
4. suggestion store 有显式状态模型
5. `launchd` + preflight + compile invoker 链路跑通
6. compiler 至少支持 `script` 与 `agent:opencode` 两种 backend 结构
7. recall 可以 in-turn 条件触发，而不是只靠 CLI
8. managed skill 的 ownership boundary 有明确治理
9. writer 仍然保持唯一最终写权
10. end-to-end 测试覆盖上述闭环

---

## 9. 一句话总结

> **Phase 2 不是重写产品设计，而是把 Phase 1 留下来的 8 个 runtime 缺口补成真的系统能力，并把 reviewer / scheduler / compiler 三个架构分叉点正式抽象成可扩展运行时。**
