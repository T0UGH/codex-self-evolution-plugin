# Codex 自我进化插件 Brainstorm

## 目标背景

想给 Codex 做一个插件，方向不是泛化增强，而是优先把 Hermes 的“自我进化”能力迁过去。

参考仓库与材料：

- `~/workspace/codex`
- `~/workspace/codex-source-reading`
- `~/workspace/hermes-source-reading`
- `/Users/haha/hermes-agent`

当前结论：第一版不求把 Hermes 全量搬过去，而是先做一个 **Hermes 式自我进化最小闭环**。

---

## Hermes 自我进化能力分层（本次讨论抽取）

原始分层里，重点有 5 层：

1. 稳定事实层：memory
2. 情境经验层：recall
3. 程序化做法层：skills
4. 历史整理层：compression / caching
5. 事后反思层：background review

本次已明确：

- **第一版先做 1 / 2 / 3 / 5**
- **第一版不做 4（compression / caching）**

原因：

- 先把“过去如何进入未来”的闭环跑起来
- 暂时不碰更重的 runtime 优化层

---

## 第一版范围

### 要做的四层

#### 1. Memory

目标：沉淀稳定事实。

分成两本：

- `USER.md`：用户偏好、协作风格、长期习惯
- `MEMORY.md`：环境事实、项目约定、工具 quirks、长期有效经验

设计原则：

- 本轮发现新事实时立即写盘
- 当前会话中途不强制回灌
- 下一次会话启动时整体带上

也就是沿用 Hermes 的原则：

> 边跑边落盘，下轮整体带上。

---

#### 2. Recall

目标：把过去会话重构成当前可用经验。

第一版结论：

- **尽量复用 Codex 原生 thread / rollout 存储**
- 不重复造完整 session archive
- 插件自己只维护一层轻量 recall index

Recall index 角色：

- 不是原始历史仓库
- 而是“哪些 thread 值得以后再想起”的二次组织层

---

#### 3. Skills

目标：把“这次做成了”的做法沉淀成以后可复用的 procedural memory。

第一版结论：

- skill 的落点优先复用 Codex 插件/skills 体系
- 但“什么时候 create / patch skill”的判断逻辑由插件自己负责

即：

- 存储/组织尽量贴 Codex
- 沉淀逻辑按 Hermes 的 procedural memory 思路来

---

#### 5. Background Review

目标：每轮结束后做一次事后反思，把本轮结果分流进未来能力。

第一版 review 只做三类判断：

1. 有没有新的稳定事实 -> 进 memory
2. 有没有值得未来召回的情境经验 -> 进 recall index
3. 有没有值得复用的做法 -> 进 skill

也就是说，review 是闭环的分流器。

---

## 第一版全局策略

已明确：

- **第一版不要太复杂，直接做全局的**

也就是：

- 全局 `USER.md`
- 全局 `MEMORY.md`
- 全局 recall index
- 全局 skills
- 全局 background review

先不做：

- 项目级切分
- workspace 级切分
- 多租户式 memory/review 隔离

---

## 插件形态选择

已明确：

- 倾向 **A1：Codex 原生插件方向**
- 但第一版采用的是 **平衡版（方案 2）**

### 平衡版定义

- 原始历史尽量借 Codex 的 `thread/list` / `thread/read`
- Hermes 式 memory / review / skill 逻辑自己建
- recall 额外做一层自己的轻量组织层

不是：

- 全都依赖 Codex 原生存储
- 也不是全套完全 sidecar 自建

---

## 关于 Codex 原生 session/thread 存储的判断

本次讨论确认：

### 可以直接复用的

- Codex 原生 `thread/list`
- Codex 原生 `thread/read`
- Codex 原生 thread / rollout 持久化历史

适合拿来做：

- recall 的原始材料底座

### 不适合全靠它的

- Hermes 式 `USER.md` / `MEMORY.md`
- Hermes 式 background review 分流
- Hermes 式 procedural skill 沉淀判断

所以当前判断是：

> 可以直接用 Codex 本身的 session/thread 存储作为 recall 原始底座，但 memory、review、以及 skill 沉淀逻辑仍需要插件自己建。

---

## Memory 注入方式

用户追问：

> `USER.md` 和 `MEMORY.md` 如何确保一定能每次会话启动的时候加载进入 Codex？通过 hooks 机制吗？

当前回答结论：

- **对，第一版优先通过 Codex 的 `SessionStart` hooks 来实现。**

设计判断：

### SessionStart hook 负责

- 会话启动时读取全局 `USER.md`
- 会话启动时读取全局 `MEMORY.md`
- 组装成稳定前缀
- 注入当前 session/thread 起点

### 不做的事

- 本轮中途每次 memory 更新都强制改当前 prompt

也就是：

- 启动加载：靠 SessionStart hook
- 中途更新：只写盘，不回灌
- 下轮启动：重新整体加载

---

## 当前版本的整体数据流

### 1. 会话启动时

- `SessionStart` hook 触发
- 读取 `USER.md` / `MEMORY.md`
- 组装成稳定背景
- 注入当前会话前缀

### 2. 当前回合执行时

- Codex 正常执行任务
- 插件尽量不打断主流程
- 只做轻量观察与必要记录

### 3. 回合结束后

- 触发 background review
- review 判断：
  - 稳定事实 -> 写 `USER.md` / `MEMORY.md`
  - 情境经验 -> 写 recall index
  - 可复用做法 -> create/patch skill

### 4. 下一轮开始时

- memory 通过 SessionStart 重新进入背景层
- recall 在需要时命中 index，再回读 thread 历史做重构
- skill 在需要时重新加载，作为当前任务的 procedural memory

---

## Background Review 实现边界（当前结论）

### Hermes 的实现方式

Hermes 的 background review 更接近：

- 在同一个 agent 内部拿到 `messages_snapshot`
- 在响应交付后起一个后台线程
- 用专门的 review prompt 再跑一次受限 review

也就是说，Hermes 更像：

> fork 一份当前 agent 的对话快照，在后台继续做 memory/skill review。

### Codex 的能力边界

当前基于已确认的稳定能力，Codex **不支持像 Hermes 那样原地 fork 当前 agent 的内存消息态**。

原因：

- 对插件/外部稳定暴露出来的是：
  - hooks
  - `thread/read`
  - `thread/list`
  - `transcript_path`
  - app-server API
- 没有稳定公开的“直接拿当前 agent 内存消息数组并后台 fork review”的接口

### 第一版的替代实现

因此，第一版 background review 不做 Hermes 式原地 fork，而采用：

> **post-turn snapshot reconstruction review**

即：

1. 当前 turn 结束
2. `Stop` hook 触发
3. hook 提供：
   - `session_id`
   - `turn_id`
   - `transcript_path`
   - `last_assistant_message`
4. review runner 再去：
   - 读取 transcript
   - 必要时调用 `thread/read(includeTurns=true)`
   - 读取 `USER.md` / `MEMORY.md` / skills 摘要
5. 重建出一份“当前回合快照”
6. 用专门的 review prompt 跑一次受限 review
7. 输出结构化结果，再分流到 memory / recall / skill

### 这意味着什么

这条路在实现手法上与 Hermes 不同：

- Hermes：原地 fork 内存快照
- Codex 第一版：事后重建快照再 review

但能力目标仍然一致：

- 都是在当前回合结束后
- 都是基于当前回合快照
- 都是在后台做受限 review
- 都是为了把结果分流进 memory / recall / skill

### 当前结论

因此后续设计文档中不要表述为：

- “Codex 支持像 Hermes 一样 fork 当前 agent”

而应该表述为：

- “Codex 第一版 background review 采用 post-turn snapshot reconstruction，而不是 Hermes 式原地 fork。”

---

## 当前已经明确的设计判断

1. 第一版不做 compression / caching。
2. 第一版只做全局，不做项目级切分。
3. 第一版走 Codex 原生插件方向。
4. 原始 session/thread 历史尽量复用 Codex。
5. `USER.md` / `MEMORY.md` 由插件自己维护。
6. `USER.md` / `MEMORY.md` 在会话启动时通过 `SessionStart` hook 注入。
7. 中途 memory 更新只写盘，不强制刷新当前会话。
8. review 是第一版闭环的核心分流器。
9. recall 不是原文回放，而是“index 命中 + thread 回读 + 经验重构”。
10. skill 不是普通文档，而是 procedural memory 的落点。
11. 第一版 background review 不采用 Hermes 式原地 fork，而采用 post-turn snapshot reconstruction。

---

## Session Recall 设计原则（当前结论）

### 为什么 recall 是系统成败关键点

如果 recall 做不好，系统会退化成两种坏形态：

1. **太弱**：想不起来，memory / skill 之外的大量历史经验白存
2. **太重**：每次都翻很多 thread，成本高、慢、噪音大，最后没人愿意开

因此 recall 不能理解成“把 Codex 历史读出来”，而必须做成一套分层召回系统。

---

### 第一版 recall 总体路线

第一版 recall 采用：

> **Codex thread 历史做原始语料层 + 插件自建轻量 recall index + 查询时二段式召回**

即分三层：

#### 1. 原始历史层
直接复用 Codex：

- `thread/list`
- `thread/read(includeTurns=true)`
- transcript / rollout

这一层负责：

- 提供原材料
- 提供完整上下文
- 提供可回读的历史真相

但它**不直接负责高效 recall**。

#### 2. 轻量 recall index
插件自己维护一个全局 recall index。

它不重复存整份 thread，只存足够让未来知道“该看哪段历史”的信息。

这一层负责：

- 候选 thread 筛选
- 主题 / 关键词索引
- 经验摘要
- 相关性缩小范围

#### 3. 查询时重构层
真正 recall 发生时：

1. 先用 recall index 找候选
2. 再回读少量 thread 细节
3. 把结果重构成当前可用经验

这一层的目标不是回放历史，而是：

> **把过去重构成当前能用的经验。**

---

### 为什么必须分层

#### 只用 Codex 原生 thread/read 的问题

- 查得慢
- 每次都要从大量 thread 里找
- 很难形成“经验级 recall”
- 更像聊天记录检索，而不是情境经验召回

#### 只做插件自建 recall DB 的问题

- 要重复存大量原始历史
- 数据一致性麻烦
- 和 Codex 原生 thread 体系脱节
- 第一版复杂度过高

所以当前结论是：

> **Codex 负责存全量真相，插件负责做高效找回。**

---

### 第一版 recall 的 4 个动作

#### 1. 回合结束后判断是否值得进入 recall
不是每轮都进 recall index。

只有以下这类内容才值得写 recall：

- 有边界判断
- 有明确设计结论
- 有排障路径
- 有可复用经验
- 后续大概率还会再问

这一步由 background review 来完成。

#### 2. 为值得保留的 thread 生成 recall entry
Recall entry 不存整份 thread，只存帮助未来命中的轻量信息。

作用是：

> 先让系统知道，这个 thread 将来在什么问题下值得再看。

#### 3. 新 query 到来时先做 candidate retrieval
当前 query 来了之后，先查 recall index，而不是先做 `thread/read`。

这一步要尽量便宜。

作用是：

- 从 query 里抽关键词
- 命中 recall index
- 选出 top 候选 thread

#### 4. 对候选做 deep recall
只有候选 thread 入围后，才做：

- `thread/read(includeTurns=true)`
- transcript 片段读取
- focused recall 重构

给主任务的不是整段历史，而是：

- 之前讨论过什么
- 哪个结论最相关
- 当时是怎么做通的
- 哪些坑已经踩过
- 这次为什么值得参考

---

### 第一版 recall index 的技术路线

第一版建议使用：

- **SQLite**

原因：

- 单机全局插件足够用
- 检索便宜
- 好做 FTS
- 后续容易演进
- 与 Hermes 的 `state.db + FTS5` 精神接近

但要压清边界：

> **这份 SQLite 不是用来替代 Codex thread store，而是用来做 recall index。**

---

### 第一版 recall 查询策略

第一版先不做：

- embedding
- reranker
- 重型混合召回

先做：

> **规则 + FTS + 轻量重排**

大致流程：

1. query 抽关键词
2. recall index 对 `summary / keywords / topics` 做 FTS 命中
3. 用简单规则重排：
   - 最近性
   - importance score
   - cwd 是否接近
   - source kind 是否匹配
4. 选 top N
5. 回读 thread 细节
6. 输出 focused recall

---

### recall 与 memory / skill 的边界

#### recall 不是 memory

- memory 存稳定事实
- recall 存情境经验入口

#### recall 不是 skill

- skill 是做法资产
- recall 是过去案例入口

可以这样压缩理解：

- memory 回答：以后一直要带着什么事实
- recall 回答：这次要不要把某段过去重新想起来
- skill 回答：以后再遇到这种事，直接怎么做

---

### 当前 recall 的两条核心原则

#### 原则 1

> **原始真相在 Codex，召回效率在插件。**

#### 原则 2

> **Recall 的核心不是存档，而是“候选筛选 + 经验重构”。**

---

## Recall 主路与 Skill 自动化边界（当前结论）

### Recall 主路选择

当前结论：

> **Recall 主路走 skill / 内建 workflow，不走 MCP-first。**

原因：

- recall 本质上是 agent 内部认知动作，而不是外部能力协议接入
- recall 需要和 memory / review / skill 紧耦合
- 如果 MCP-first，recall 很容易退化成“外部搜索工具”
- 如果 recall 作为内建 workflow / system skill，更容易高频、自动地触发

因此第一版建议：

- recall 的触发与编排走系统内建 workflow / skill
- 底层数据获取由插件自己的本地能力完成
- MCP 未来只作为底层检索增强件，而不是第一版主路

### Skill 自动化边界

当前结论：

> **系统允许全自动 create/patch skill，但仅限于这套体系自己生成并标记为 managed 的 skill。**

这意味着：

#### 允许自动做的

- create 新的系统 skill
- patch 已有系统 skill
- edit 已有系统 skill
- （未来如有需要）删除系统 skill

#### 不允许自动做的

- patch 用户自有 skill
- edit 用户自有 skill
- merge 到第三方 skill
- 基于相似度猜测性修改用户已有 skill

### 所有权边界建议

第一版建议同时使用两层边界：

1. **目录隔离**：系统自己生成的 skill 放在插件专属 skill 根下
2. **元数据标记**：skill 内显式声明 owner / managed 标识

这样可以保证：

- 自动化范围可解释
- 用户资产不被污染
- 后续版本管理与 patch 逻辑更稳定

---

## 还没定的事（下一轮继续聊）

1. 第一版插件目录具体长什么样
2. recall index 的最小字段集合
3. background review 的输入/输出格式
4. skill create / patch 的触发阈值
5. `SessionStart` hook 注入格式长什么样最稳
6. recall 在什么时机触发：自动 prefetch 还是显式调用
7. 插件与 Codex 原生 hooks / skills / app-server 的职责边界
8. 第一版是否需要一个专门的系统 prompt 模板来约束 review 行为

---

## 当前一句话总结

第一版要做的不是“给 Codex 加个 memory”，而是：

> **先做一个全局的、四层闭环的 Hermes 式自我进化插件：用 SessionStart 带入稳定记忆，用 Codex thread 历史承接 recall 原材料，用 background review 把本轮结果分流成 memory / recall / skill，并让这些东西在后续回合重新回来。**
