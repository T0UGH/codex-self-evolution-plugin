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

### 2.1 全局优先

第一版采用**全局方案**，不做项目级切分：

- 全局 `USER.md`
- 全局 `MEMORY.md`
- 全局 recall index
- 全局 skills
- 全局 background review

这样可以优先验证闭环本身，而不是把复杂度浪费在分片模型上。

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

这层**不是**原始历史仓库，只是“找哪段历史值得看”的组织层。

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
3. 组装成稳定前缀
4. 注入当前 session/thread 起点

这层语义上等价于 Hermes 的 frozen snapshot。

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
- recall 在命中条件下触发候选召回与 focused recall 重构
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

### 5.2 输入来源

review 输入来源分层：

- hooks：负责触发与最小上下文
- transcript：提供原始回合材料
- `thread/read`：补全 thread / turns
- plugin assets：提供 memory / skill 对照材料

### 5.3 输出形式

第一版 review 输出必须是**结构化结果**，不能是长散文。

至少输出三类候选：

- `memory_updates`
- `recall_entry`
- `skill_action`

review 只判断，不直接写最终文件；真正落盘由对应 writer 完成。

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

### 6.4 查询策略

第一版先不做 embedding / reranker / 重型混合召回。

先做：

- FTS
- 规则重排
- top-N 候选筛选
- focused recall 重构

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

---

## 9. 当前不阻塞设计的开放问题

以下问题仍未定，但当前不阻塞设计主线：

1. 第一版插件目录具体长什么样
2. recall index 的最小字段集合
3. background review 的输入/输出 JSON 草案
4. skill create / patch 的触发阈值
5. `SessionStart` 注入格式最小模板
6. recall 自动触发与显式触发的边界
7. 插件与 Codex 原生 hooks / skills / app-server 的职责边界
8. 是否需要单独 system prompt 模板约束 review 行为

---

## 10. 当前一句话总结

第一版要做的不是“给 Codex 加一个 memory 功能”，而是：

> **做一个全局的、四层闭环的 Hermes 式自我进化插件：会话启动时注入稳定记忆，回合结束后做 snapshot reconstruction review，把结果分流成 memory / recall / skill，并在后续回合重新带回来。**
