---
title: Codex 自我进化插件设计 v1
date: 2026-04-18
status: draft
project: codex
---

# Codex 自我进化插件设计 v1

## 1. 目标

为 Codex 设计一个第一版“自我进化插件”，优先迁移 Hermes 的四层能力：

1. 稳定事实层：memory
2. 情境经验层：recall
3. 程序化做法层：skills
4. 事后反思层：background review

第一版**不做** compression / caching 这类更重的 runtime 优化层。

本设计的目标不是“给 Codex 加一个 memory 功能”，而是先做出一个最小闭环：

```text
当前回合结束
  -> review 判断什么值得留下
  -> 留成 memory / recall / skill
  -> 下一轮再把它们带回来
```

---

## 2. 设计边界

### 2.1 全局优先，但 recall 保留强作用域

第一版采用**简化的全局方案**，优先不做项目级切分：

- 全局 `USER.md`
- 全局 `MEMORY.md`
- 全局 managed skills
- 全局 background review

但 recall 不采用“无差别全局使用”的做法。

第一版对 recall 的要求是：

- 可以共用一套全局存储/索引
- 每条 entry 必须带强 provenance（至少包含 repo / cwd / thread / source）
- 查询时默认 same-repo / same-cwd 优先召回

也就是说：

> **memory 和 skill 的最终沉淀入口先做全局；recall 可以全局存，但不能全局无差别用。**

这样可以优先验证闭环本身，而不是把复杂度浪费在分片模型上；同时避免 recall 因为缺作用域而快速失真。

### 2.2 Codex 原生插件方向

第一版走 Codex 原生插件方向，但不是完全依赖 Codex 原生存储或原生 hooks 能力。

采取“平衡版”策略：

- 原始 thread / rollout 历史尽量复用 Codex
- Hermes 式 memory / review / skill 沉淀逻辑由插件自己实现
- recall 由插件自己维护轻量组织层

### 2.3 与 Hermes 的差异

Hermes 的 background review 可以直接在同一个 agent 内部拿到 `messages_snapshot`，然后在后台线程继续 review。

Codex 第一版**不具备**这种稳定公开的“原地 fork 当前 agent 内存快照”的能力。

因此第一版 background review 采用：

> **post-turn snapshot reconstruction review**

也就是：

- 回合结束后通过 hooks 触发
- 再通过 transcript + `thread/read(includeTurns=true)` 重建本轮快照
- 再做受限 review

能力目标与 Hermes 一致，但实现手法不同。

---

## 3. 系统结构

插件由四个主要子系统组成。

### 3.1 Memory Store

插件自己维护两本全局记忆：

- `USER.md`：用户偏好、协作方式、长期习惯
- `MEMORY.md`：环境事实、项目约定、工具 quirks、长期有效经验

设计原则：

- 本轮发现新事实时立即写盘
- 本轮中途不强制刷新当前会话前缀
- 下一次会话启动时整体带上

即：

> 边跑边落盘，下轮整体带上。

### 3.2 Recall Layer

Recall 采用三层结构：

#### 原始历史层
复用 Codex：

- `thread/list`
- `thread/read(includeTurns=true)`
- transcript / rollout

它提供完整历史真相，但不直接负责高效 recall。

#### 轻量 recall index
插件维护一份全局 recall index，用来做：

- 候选 thread 筛选
- 关键词 / 主题索引
- 经验摘要
- 相关性缩小范围
- 基于 provenance 做局部优先召回

这层**不是**原始历史仓库，只是“找哪段历史值得看”的组织层。

第一版要求每条 recall entry 至少记录：

- `repo`
- `cwd`
- `thread_id`
- `source_kind`
- `captured_at`

这样后续查询时才能先按 same-repo / same-cwd 缩小范围，再决定是否 global fallback。

#### 查询时重构层
真正 recall 发生时：

1. 先用 index 找候选
2. 再回读少量 thread 细节
3. 重构成 focused recall

Recall 的核心不是历史回放，而是：

> 候选筛选 + 经验重构。

### 3.3 Skill Layer

技能的定位不是普通文档，而是 procedural memory。

第一版：

- skill 落点复用 Codex 插件/skills 体系
- skill 的 create / patch / edit 逻辑由插件自己实现

边界原则：

- 允许自动 create/patch **系统自己生成并标记为 managed 的 skill**
- 不允许自动修改用户已有或第三方安装的 skill

建议通过两层边界实现：

1. 目录隔离
2. 元数据标记（owner / managed）

### 3.4 Background Review

Background review 是闭环的分流器。

它不负责重新完成任务，只负责在回合结束后判断：

1. 是否有新的稳定事实 -> memory
2. 是否有值得未来召回的情境经验 -> recall index
3. 是否有可复用做法 -> skill

---

## 4. 生命周期数据流

### 4.1 会话启动时

通过 `SessionStart` hook：

1. 读取 `USER.md`
2. 读取 `MEMORY.md`
3. 读取轻量 recall skill / recall policy skill
4. 组装成稳定前缀与 recall control layer
5. 注入当前 session/thread 起点

注意：

- `SessionStart` **只预加载 recall policy / recall skill**
- `SessionStart` **不直接执行 recall 查询**
- `SessionStart` **不预加载大量 recall material**

这层负责把 recall 变成 session 的基础能力，而不是在会话开始时就把历史内容一股脑塞进上下文。

### 4.2 当前回合执行时

主流程仍然由 Codex 主导：

- 用户输入
- Codex 推理
- 工具调用
- 输出结果

插件尽量不在执行中频繁打断，只做轻量观察与必要记录。

### 4.3 回合结束后

通过 `Stop` hook 触发 background review。

hook 自身只负责提供触发点和最小上下文：

- `session_id`
- `turn_id`
- `transcript_path`
- `last_assistant_message`

之后由 review runner 补拉：

- transcript
- `thread/read(includeTurns=true)`
- `USER.md`
- `MEMORY.md`
- skills 摘要

再重建出一份 review snapshot，执行受限 review，并将结构化结果分流到 memory / recall / skill。

### 4.4 下一轮再次回流

- memory 通过 `SessionStart` hook 重新进入背景层
- recall skill / recall policy 通过 `SessionStart` 预加载进入 session 基础能力层
- recall 只在命中条件时触发候选召回与 focused recall 重构
- skill 在命中任务类型时重新加载

最终形成：

- 事实回流成背景
- 过去会话回流成情境经验
- 做法回流成能力

---

## 5. Background Review 机制

### 5.1 触发方式

第一版不让 hook 直接跑一个完整独立 agent，会采用：

> hooks 负责触发，review runner 负责真正 review

但这个 review runner 不应被定义成“独立人格 agent”或“正式 subagent”，而应理解为：

> **一个基于当前回合快照派生出来的后台 review pass**

### 5.2 执行基座

第一版采用新的三段式路线：

> **前台实例只做轻 review 并把 suggestion/event 入库；后台定时批处理 agent 串行消费 suggestion，完成真正整理与最终落盘。**

这条路线的目标是：

- 不引入常驻服务/协调者
- 避免多个 Codex 实例直接并发写 memory / recall / skill
- 把高并发写问题转成低频串行消费问题

### 5.3 三段式结构（v1）

#### 1. 前台实例（capture pass）

每个 Codex 实例负责：

- 正常完成当前任务
- 在回合结束后执行轻 review pass
- 输出结构化 suggestion/event
- 将 suggestion/event 写入 DB

前台实例**不直接改最终状态**。

#### 2. 中间存储层（suggestion/event store）

中间层负责：

- 接收 suggestion/event
- 记录状态
- 提供可重试与幂等键
- 为后台批处理提供待消费队列

建议状态至少包括：

- `pending`
- `processing`
- `done`
- `failed`
- `discarded`

#### 3. 后台批处理 agent（artifact compiler）

后台通过**纯定时批处理**方式启动一个便宜的 coding agent，负责：

- 按固定 schedule 扫描 DB 中 `pending` suggestion
- 批量整理、归并、去重
- 将 suggestion 编译成正式 artifact
- 串行更新最终状态
- 处理完一批后退出

第一版明确：

- **不做** `Stop` 后 try-run compiler
- **不做**常驻 coordinator/service
- compiler 主驱动就是 cron / 定时任务

它可以使用较便宜模型（例如 minimax / opencode 路线），但职责必须受限。

### 5.4 Review 模式决议（已定）

第一版前台 review 明确选择：

> **方案一：轻 review pass**

也就是：

- 单次调用
- 无工具
- 固定 review prompt
- 固定 JSON 输出
- review 自己不直接写 memory / recall / skill
- 只把结果写成 suggestion/event

第一版**不采用**：

- 拉起一个完整 Codex agent 做前台 review
- 给前台 review 完整工具能力
- 让前台 review 自己直接修改 managed skills / memory 文件

这样做的原因：

1. **token 成本更可控**：适合 recall / review 尽量多触发
2. **前台边界更清楚**：capture 负责判断，compiler 负责整理，writer 负责提交
3. **并发风险更低**：多实例不直接改最终状态
4. **更符合当前偏好**：不引入常驻 coordinator/service

### 5.5 后台批处理 agent 的职责边界

后台批处理 agent 不是新的主 agent，也不是全局“总控大脑”。

它的职责仅限于：

- suggestion 合并
- 去重
- 状态转换
- 产出最终 artifact
- 驱动最终写入

它**不应该**：

- 无限扩张成一个复杂自治 agent
- 自己跑全库重搜索
- 代替主系统做复杂开放式思考

一句话说：

> **它是 artifact compiler，不是新的主执行 agent。**

### 5.6 输入 schema（v1）

第一版 background review 输入采用三层结构：

#### A. 回合基础信息

- `session_id`
- `thread_id`
- `turn_id`
- `transcript_path`
- `cwd`
- `model`
- `triggered_at`

这层负责提供 review 的最小执行上下文与追踪标识。

#### B. 当前回合快照

- `user_input_summary`
- `last_assistant_message`
- `thread_snapshot`
- `tool_events_summary`

其中：

- `thread_snapshot` 只保留与当前回合最相关的有限 turns，不追求全量 transcript
- `tool_events_summary` 只保留与沉淀判断有关的工具行为摘要，不保留原始大输出

这层的目标不是复盘全历史，而是让 review 能判断“这一轮有什么值得进入未来”。

#### C. 对照材料

- `current_user_md`
- `current_memory_md`
- `managed_skills_summary`
- `existing_recall_entries_for_thread`（可选）

这层负责让 review 能判断：

- 某条事实是不是已经记录过
- 某个做法是不是已有 managed skill
- 某个 thread 是否已有 recall 资产

### 5.5 输入 JSON 草案（v1）

```json
{
  "context": {
    "session_id": "thr_xxx",
    "thread_id": "thr_xxx",
    "turn_id": "turn_xxx",
    "transcript_path": "/path/to/transcript.jsonl",
    "cwd": "/workspace/project",
    "model": "gpt-5",
    "triggered_at": "2026-04-18T12:00:00Z"
  },
  "turn_snapshot": {
    "user_input_summary": "用户希望为 Codex 设计一个具备 Hermes 式自我进化闭环的插件。",
    "last_assistant_message": "已形成第一版 design，并确认 recall 主路走 skill。",
    "thread_snapshot": [
      {
        "role": "user",
        "summary": "提出要做 Codex 自我进化插件"
      },
      {
        "role": "assistant",
        "summary": "给出 memory / recall / skill / review 四层闭环设计"
      }
    ],
    "tool_events_summary": [
      "读取 Codex hooks/runtime 相关源码与文档",
      "创建新仓库并写入 brainstorm/design 文档"
    ]
  },
  "current_assets": {
    "current_user_md": "...",
    "current_memory_md": "...",
    "managed_skills_summary": [
      {
        "name": "example-skill",
        "status": "active",
        "summary": "..."
      }
    ],
    "existing_recall_entries_for_thread": []
  }
}
```

### 5.6 输出 schema（v1）

第一版 background review 输出必须是**结构化结果**，不能是长散文。

输出固定为三类：

#### A. `memory_updates`

用于沉淀稳定事实：

- `user`：进入 `USER.md`
- `global`：进入 `MEMORY.md`

#### B. `recall_candidate`

用于判断这轮是否值得形成 recall entry。

#### C. `skill_action`

用于判断 managed skill 的动作：

- `none`
- `create`
- `patch`
- `edit`
- `retire`

第一版不允许输出 `delete`。

### 5.7 输出 JSON 草案（v1）

```json
{
  "memory_updates": {
    "user": [
      "用户偏好少打扰、尽量自动沉淀。"
    ],
    "global": [
      "本项目第一版 background review 采用轻 review pass，不采用完整 agent review。"
    ]
  },
  "recall_candidate": {
    "should_save": true,
    "summary": "本轮明确了 Codex 自我进化插件的四层闭环、review 模式与 recall 主路策略。",
    "keywords": ["codex", "self-evolution", "review", "recall", "skill"],
    "topics": ["architecture", "plugin-design"],
    "source_kind": "design",
    "importance_score": 5,
    "why_relevant": "后续继续推进该插件设计与实现计划时会反复引用这组决策。",
    "confidence": "high"
  },
  "skill_action": {
    "type": "none",
    "target_skill": null,
    "reason": "本轮主要在形成系统设计决策，还未沉淀出足够稳定的系统内 managed skill。",
    "proposed_summary": null
  }
}
```

### 5.10 writer 分流原则

前台 review 只负责判断，不直接写最终文件。

真正的落盘由后台批处理链完成：

1. 前台 review -> 写 suggestion/event 入库
2. 后台 artifact compiler -> 消费 suggestion，整理成正式结果
3. 对应 writer -> 更新最终状态

最终 writer 的职责仍然是：

- memory writer：写 `USER.md` / `MEMORY.md`
- recall writer：写 SQLite recall index
- skill writer：create / patch / edit / retire managed skills

这样可以保证：

- capture 和提交分离
- 多实例不直接并发写最终状态
- 更容易 debug
- 更容易做幂等与并发控制

---

## 6. Recall 系统设计

### 6.1 Recall 的两条原则

#### 原则 1

> 原始真相在 Codex，召回效率在插件。

#### 原则 2

> Recall 的核心不是存档，而是“候选筛选 + 经验重构”。

### 6.2 第一版 recall 工作流

1. 回合结束后，background review 判断本轮是否值得进入 recall
2. 对值得保留的 thread 生成 recall entry
3. 新 query 到来时，先查 recall index 做 candidate retrieval
4. 对 top 候选做 `thread/read(includeTurns=true)` 回读
5. 输出 focused recall 给当前任务

### 6.3 技术路线

第一版 recall index 建议使用 SQLite。

定位必须压清：

> 这份 SQLite 不是用来替代 Codex thread store，而是用来做 recall index。

### 6.4 Recall index 最小字段集合

第一版建议定义一张 `recall_entries` 表，字段分成四组。

#### A. 身份字段

- `id`：插件自己的主键（UUID 或稳定 hash）
- `thread_id`：对应 Codex thread
- `turn_id`：对应这条 recall 主要来源的 turn
- `source_updated_at`：源 thread/turn 的最后更新时间

作用：

- `thread_id` 负责回读原始真相
- `turn_id` 负责定位经验锚点
- `source_updated_at` 负责判断 recall entry 是否 stale

#### B. 检索字段

- `summary`：1~3 句经验摘要
- `keywords`：关键词列表
- `topics`：主题标签列表
- `source_kind`：`design` / `debugging` / `implementation` / `decision` / `source-reading` 等
- `importance_score`：轻量重要性分值

作用：

- `summary` 支持全文检索
- `keywords/topics` 提升召回命中率
- `source_kind` 支持按经验类型过滤
- `importance_score` 提供排序依据

#### C. provenance 字段

- `repo_path`
- `cwd`
- `git_branch`
- `git_commit`（第一版可为空）
- `created_at`
- `updated_at`

作用：

- 支持 same-repo / same-workspace 优先召回
- 防止全局 recall 污染
- 为 stale 判断和未来清理提供依据

#### D. 展示/解释字段

- `why_relevant`
- `review_confidence`
- `status`

其中：

- `why_relevant`：解释为什么未来还值得想起
- `review_confidence`：低 / 中 / 高
- `status`：`active` / `retired`

#### 第一版最小必需字段

第一版最小集合建议为：

- `id`
- `thread_id`
- `turn_id`
- `summary`
- `keywords`
- `source_kind`
- `importance_score`
- `repo_path`
- `cwd`
- `git_branch`
- `why_relevant`
- `source_updated_at`
- `created_at`
- `updated_at`
- `status`

### 6.5 查询策略

第一版先不做 embedding / reranker / 重型混合召回。

先做：

- FTS
- 规则重排
- top-N 候选筛选
- focused recall 重构

### 6.6 默认召回优先级

虽然第一版存储采用全局方案，但默认召回必须局部优先。

推荐优先级：

1. same repo
2. same cwd / workspace
3. same branch（若可得）
4. recency + importance
5. global fallback

也就是说：

> **全局存储，局部优先召回。**

---

## 7. Recall 主路与 MCP 的关系

当前决定：

> **Recall 主路走 skill / 内建 workflow，不走 MCP-first。**

原因：

- recall 是内部认知动作
- recall 需要和 memory / review / skill 深度耦合
- MCP-first 会让 recall 退化成“外部搜索工具”
- skill / 内建 workflow 更利于高频自动触发

MCP 在未来可作为 recall 的底层增强件，比如：

- embedding backend
- rerank service
- 外部知识库检索

但不是第一版主路。

---

## 8. Skill 自动化边界

第一版明确采用：

> **全自动 create/patch skill，但仅限于系统自己生成并标记为 managed 的 skill。**

允许：

- create 新系统 skill
- patch 系统 skill
- edit 系统 skill

不允许：

- patch 用户自有 skill
- edit 用户自有 skill
- merge 到第三方 skill
- 猜测性修改用户现有 skill

这条边界是自动化安全规则，不是可选优化。

### 8.1 Skill review 的默认职责

对齐 Hermes 的做法，第一版 review 对 skill 的默认职责是：

- **create**：发现新的可复用做法时创建系统 skill
- **patch**：已有系统 skill 需要补坑、修正、更新时补丁式更新
- **edit**：已有系统 skill 结构变化较大，但仍然属于同一技能资产时整体改写

第一版**不自动 delete skill**。

原因：

- Hermes 的 review prompt 重点是 “saving or updating a skill”
- create / patch / edit 更符合 procedural memory 的持续沉淀逻辑
- delete 属于更重的不可逆操作，不适合作为第一版默认自动动作

### 8.2 Skill 生命周期建议

第一版建议采用一个轻量生命周期：

- `draft`
- `active`
- `retired`

含义：

- `draft`：刚被沉淀出来，尚未充分验证
- `active`：已足够稳定，可参与后续自动触发
- `retired`：不再自动触发，但保留历史资产

第一版不做自动 `delete`，而是优先通过 `retired` 退役。

---

## 9. 平台能力矩阵（当前版本）

| 能力 | 当前状态 | 说明 |
|---|---|---|
| `SessionStart` hook 注入 | verified | 已调研确认可用于 memory 前缀注入 |
| `Stop` hook 触发 | verified-ish | 已确认存在与输入结构；仍需真实插件环境验证 |
| `transcript_path` 可用 | verified-ish | 已在 hook 输入中确认；仍需真实环境验证 |
| `thread/read(includeTurns=true)` | verified | app-server 文档明确存在 |
| 后台 review runner 执行 | fallback-required | 需要插件自己提供本地 runner |
| Hermes 式原地 fork 当前 agent | unavailable for v1 | 当前不作为 Codex 第一版假设 |
| recall 自动触发 workflow | design-owned | 由插件自己实现 |
| managed skill 自动 patch | design-owned | 由插件自己实现 |

---

## 10. 本轮已定的关键设计决议

1. **v1 资产分层**：`USER.md` / `MEMORY.md` / managed skills 先走全局沉淀；recall 允许全局存储，但必须局部优先使用。
2. **compiler 运行模型**：第一版 compiler 只做纯定时批处理；不做 `Stop` 后 try-run，不做常驻 coordinator/service。
3. **写入契约**：前台实例只负责提案；`compiler -> writer` 拥有最终写权。原始事实以 `thread/read` / transcript 为准，沉淀判断以 reviewer suggestion 为主，再由 compiler 做最终归并。
4. **review 触发与执行模型**：第一版采用 `Stop` hook 触发 + 前台轻 review pass + suggestion 入库，不做 Hermes 式原地 fork。
5. **review schema**：第一版固定轻量 JSON schema；reviewer 无工具、不直接写盘、只做单次轻 review pass。
6. **recall 存储与召回策略**：第一版存储可全局共享，但每条 entry 必须带 provenance，默认检索优先 same-repo / same-workspace，再 global fallback。
7. **recall 触发策略**：`SessionStart` 预加载轻量 recall skill / recall policy skill，使 recall 成为 session 基础能力；实际 recall 不自动执行，只在回合内命中条件时触发。
8. **recall 主路**：主路走 skill / 内建 workflow，不走 MCP-first；MCP 未来只作为底层检索增强件。
9. **升格策略**：第一版对 memory / managed skill 采用宽松准入，只排除明显噪音、明显错误、明显一次性碎片；其余内容优先先沉淀，再通过 patch / retire / rewrite 逐步收紧。
10. **skill 自动化边界**：全自动 create/patch/edit 仅限系统自管的 managed skills，不能动用户或第三方 skill。
11. **skill 生命周期**：第一版默认支持 create / patch / edit / retired，不自动 delete。
12. **插件与 Codex 的职责边界**：Codex 提供 threads、hooks、skill 基础设施和 MCP 运行时；插件自己掌控 memory、review、compiler、recall policy/index、以及 managed skill 生命周期。

---

## 11. 当前仍开放但不阻塞主线的问题

以下问题仍未定，但当前不阻塞设计主线：

1. 第一版插件目录具体长什么样
2. recall index 的最小字段集合
3. `SessionStart` 注入格式最小模板
4. 是否需要单独 system prompt 模板约束 review 行为
5. compiler 的 batch size、schedule、锁实现细节
6. recall skill / recall policy skill 的具体文本与触发文案

---

## 11. 当前一句话总结

第一版要做的不是“给 Codex 加一个 memory 功能”，而是：

> **做一个带全局沉淀层、但 recall 局部优先的四层闭环 Hermes 式自我进化插件：会话启动时注入稳定记忆与轻量 recall policy，回合结束后做 snapshot reconstruction review，把结果分流成 memory / recall / skill，并在后续回合按需重新带回来。**
