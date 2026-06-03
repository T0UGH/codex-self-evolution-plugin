# 深度 Repo Review Workflow 设计说明（2026-06-03）

## 背景

本说明记录 2026-06-03 这次 `codex-self-evolution-plugin` 全仓深审 workflow 的组织思路，以及对应脚本结构为什么这样设计。

这不是逐 token 的原始思考过程记录，而是面向后续复盘和复用的**设计决策说明**。

目标不是“把所有可疑点都列出来”，而是同时满足三件事：

1. **覆盖尽量完整**，不要只盯一个目录
2. **误报尽量低**，不要把听起来合理但实际上不成立的问题混进结论
3. **结论可执行**，最后能收敛成优先级清楚的修复建议

---

## 设计总览

这次 workflow 被拆成四个阶段：

1. `Map`
2. `Review`
3. `Verify`
4. `Synthesize`

这是一个刻意的结构，不是随便拆的。

### 为什么不是“一个 agent 从头读到尾”

单 agent 全读全审的常见问题有三个：

- 很容易只盯自己最容易理解的那部分代码
- 会把局部现象误判成全局问题
- 一旦先入为主，后面就会开始为自己的判断辩护，而不是继续找反例

所以这次 workflow 的思路是：

- 先让不同 agent 建立**仓库地图**
- 再让不同视角的 reviewer 独立挑刺
- 再让 verifier 专门负责**否决候选问题**
- 最后才做综合收敛

一句话概括：

> 先理解系统，再分视角找问题，再独立反驳问题，最后只留下站得住的结论。

---

## 第一阶段：Map

### 目的

`Map` 阶段不是为了找 bug，而是为了先回答：

- 仓库有哪些主链路
- 每条链路负责什么
- 关键文件在哪里
- 真正的高风险边界在哪里
- 测试大概覆盖到了哪些层

### 为什么要先建地图

这个仓库不是一个简单脚本，而是有明显的子系统：

- CLI / config / diagnostics / hooks
- `session_recall`
- `session_reflection`
- docs / tests / packaging

如果跳过地图阶段直接审，很容易出现两类偏差：

1. **盲审**：reviewer 只看自己顺手的部分
2. **误判**：把局部问题说成全局结构问题

比如插件 bundle 多副本这个问题，如果不知道 packaging、install script、docs、tests 之间怎么串起来，就只能得出“有重复文件”这种浅结论，很难说明它为什么真的会形成发布面风险。

### 为什么按子系统拆 mapper

我把 mapper 拆成了四路：

- `map:cli-config`
- `map:session-recall`
- `map:session-reflection`
- `map:docs-tests`

原因是它们天然互相独立，可以并行，而且每一路都能形成相对稳定的系统视图。

### 为什么 `Map` 用 structured schema

对应脚本里的：

- `MAP_SCHEMA`

要求字段：

- `subsystem`
- `purpose`
- `keyFiles`
- `keyBehaviors`
- `riskHotspots`
- `testCoverageView`

#### 设计意图

这几个字段不是随便选的。

它强制 mapper 产出的是一份“系统剖面”，而不是泛泛摘要。

- `purpose` 约束它先说职责，不要先说问题
- `keyFiles` 保证后面 reviewer 知道具体落点
- `keyBehaviors` 保证不是只报文件名
- `riskHotspots` 提前暴露危险边界
- `testCoverageView` 让后面的 testing reviewer 少走弯路

如果没有这个 schema，mapper 很容易输出一段漂亮但不耐用的 prose，后续难复用。

---

## 第二阶段：Review

### 目的

`Review` 阶段的目标不是“按目录扫一遍”，而是按**问题维度**做独立审查。

### 为什么按维度拆 reviewer，而不是按目录拆

同一段代码，往往同时涉及多种问题维度。

例如 `session_reflection/runner.py`：

- 从 architecture 视角看，是边界和职责问题
- 从 correctness 视角看，是状态机和 receipt 处理问题
- 从 security 视角看，是 trust boundary 问题
- 从 testing 视角看，是哪些 failure mode 被编码进测试的问题

如果按目录拆，review 结果容易变成混合印象，缺少锋利的问题意识。

所以这次 review 按 6 个维度拆：

- `architecture`
- `correctness`
- `security-boundaries`
- `testing`
- `maintainability`
- `docs-runtime-consistency`

这样每个 reviewer 都是带着一种明确的问题模型去读整仓相关部分。

### `Review` 的 schema 为什么这样设计

对应脚本里的：

- `FINDINGS_SCHEMA`

顶层字段：

- `focus`
- `strengths`
- `findings`

每条 finding 要求：

- `title`
- `severity`
- `confidence`
- `category`
- `file`
- `line`
- `problem`
- `impact`
- `evidence`
- `fix`

#### 设计意图

这里有几个关键点：

##### 1. 顶层保留 `focus`

这样 verifier 在后面复核时，知道该 finding 原本是在哪个问题维度下被提出的。

##### 2. 每条 finding 强制要有 `evidence`

这样 reviewer 不能只给观点，必须给依据。

##### 3. 每条 finding 强制要有 `fix`

这不是为了让 workflow 自动修，而是为了避免输出“问题意识强但没有落地方向”的空洞结论。

##### 4. `confidence` 放在 reviewer 阶段就出现

这是为了后面和 verifier 的 `confidence` 一起做加权，而不是只靠后验判断。

### 为什么每个 reviewer 限制“最多 6 条”

这是一种刻意的压缩。

如果不限制数量，多 agent review 很容易进入“每人都列十几条”，最后 synthesis 阶段反而难收敛。

这次要的是**高价值问题**，不是尽可能多的问题。

---

## 第三阶段：Verify

### 目的

`Verify` 是整套 workflow 里最关键的一层。

这里不是让 agent 继续扩写，而是专门做**反向核实**：

- 这条候选问题到底是不是真问题
- 证据够不够硬
- 有没有被初始 reviewer 误读
- 有没有现有测试已经证明它不成立

### 为什么必须单独设 `Verify`

repo review 最大的问题从来不是“找不到问题”，而是“很容易报出听起来很像真的问题”。

这次实际就出现了几条这种候选项，例如：

- heuristic transcript discovery 是否会用错 `session_id`
- `skipped_empty` 是否和 receipt contract 冲突

如果没有 verifier，这两类点很容易直接混进最终报告里。

### 为什么 verifier 要“默认否决”

verifier prompt 里有一句关键要求：

> 如果不确定，默认否决。只有在代码证据足够扎实时才通过。

这是刻意设计的，因为这轮 workflow 的目标不是“多找问题”，而是“高质量结论”。

宁可漏掉一些边角味道，也不要把不扎实的怀疑包装成 confirmed finding。

### `Verify` 的 schema 为什么很小

对应脚本里的：

- `VERDICT_SCHEMA`

字段只有：

- `isReal`
- `confidence`
- `reason`
- `bestEvidence`

#### 设计意图

这是故意做小的。

因为 verifier 的任务非常单纯：

1. 判真伪
2. 给证据
3. 说明理由

它不需要再给新的修复设计，也不需要重新写一遍完整 finding。

如果 schema 做太大，verifier 会倾向于重新审查一遍，而不是集中火力做真假判断。

---

## 第四阶段：Synthesize

### 目的

前面几个阶段的输出已经很多了：

- 系统地图
- 候选问题
- 复核 verdict
- 被否决但值得观察的项

`synthesis` 的任务是把这些结果收敛成对人真正有用的结论，而不是继续加信息量。

### 为什么需要单独 synthesis

如果直接把 review + verify 的原始产物给用户，会有两个问题：

1. 太散
2. 主次不清

而用户真正需要知道的是：

- 这个仓库整体是好还是坏
- 哪些问题优先级最高
- 哪些只是观察项
- 是否需要大重构，还是只要修几个边界问题

### `FINAL_SCHEMA` 为什么这样设计

字段包括：

- `summary`
- `strengths`
- `confirmedFindings`
- `watchItems`

#### 设计意图

##### 1. `strengths`

保留优点不是礼貌，而是决策需要。

如果一个仓库核心设计已经靠谱，那后续动作应该是“修边界”；如果核心设计已经烂掉，那才考虑大改。

##### 2. `confirmedFindings`

只保留最重要的问题，避免最终报告退化成流水账。

##### 3. `watchItems`

这类项有价值，但证据还不够硬，不应该混进 confirmed findings。

这一层可以把“现在不下结论，但未来值得再看”的观察保留下来。

---

## 脚本结构逐段拆解

下面按脚本实际结构逐段解释。

---

## 1. `meta`

脚本最开头：

```js
export const meta = {
  name: 'deep-repo-review',
  description: 'Deep multi-agent review of the repository across architecture, correctness, maintainability, tests, and risk surfaces',
  phases: [
    { title: 'Map', detail: 'Map subsystems, boundaries, and shipped surfaces' },
    { title: 'Review', detail: 'Run specialist review passes over the full repository' },
    { title: 'Verify', detail: 'Adversarially verify each candidate finding' },
    { title: 'Synthesize', detail: 'Merge confirmed findings into a final report' }
  ]
}
```

### 为什么这样写

这是为了让 workflow UI 层面就能清楚展示：

- 当前在哪个阶段
- 这个阶段在做什么

尤其对长跑 workflow 很重要，不然用户只会看到一堆 agent 名称，不知道整体推进到了哪里。

---

## 2. schema 设计

脚本里定义了四个 schema：

- `MAP_SCHEMA`
- `FINDINGS_SCHEMA`
- `VERDICT_SCHEMA`
- `FINAL_SCHEMA`

### 为什么要用 schema，而不是让 agent 自由输出文本

原因有四个：

1. **减少格式漂移**
2. **方便后续阶段直接消费前一阶段输出**
3. **避免 synthesis 时再做脆弱的文本解析**
4. **让 agent 被迫说清楚“证据、影响、修复方向”**

### 这次 schema 设计的代价

代价也很明确：

- schema 越严格，subagent 越容易不按要求返回
- 这次实际就出现了若干 `StructuredOutput` 失败

所以这是一个质量和鲁棒性的权衡。

当前这版偏向“质量优先”。

---

## 3. `keyOfFinding()` 与 `dedupeFindings()`

脚本里的 helper：

```js
function keyOfFinding(f) {
  return `${f.file}:${f.line}:${f.category}:${f.title}`
}

function dedupeFindings(items) {
  const seen = new Map()
  for (const item of items) {
    const key = keyOfFinding(item)
    const prev = seen.get(key)
    const score = (item.verdict?.confidence || 0) + (item.confidence || 0)
    const prevScore = prev ? ((prev.verdict?.confidence || 0) + (prev.confidence || 0)) : -1
    if (!prev || score > prevScore) seen.set(key, item)
  }
  return Array.from(seen.values())
}
```

### 为什么需要 dedupe

因为不同 reviewer 可能会从不同角度命中同一个问题。

例如：

- architecture reviewer 报“source of truth 不单一”
- maintainability reviewer 报“多份副本造成 drift burden”

它们不一定字面完全相同，但可能指向同一核心问题。

如果不做 dedupe，最终 synthesis 很容易把同一个问题讲两遍。

### 为什么 fingerprint 用 `file:line:category:title`

这是一个折中方案。

- 只用 `file:line` 太粗，容易把不同问题合并掉
- 加上 `category` 和 `title`，能区分同一位置的不同问题

它不是完美方案，但对这次 repo review 足够用。

### 为什么 score = reviewer confidence + verifier confidence

设计意图是：

- 初始 reviewer 的把握有价值
- verifier 的复核把握也有价值
- 两者一起高，说明问题更稳定

所以 dedupe 时保留的是“综合得分更高”的那条。

### 这个 dedupe 的局限

它有几个明显局限：

1. 只能去重“非常接近”的重复项
2. 对“语义重复但文件/标题不完全一致”的问题无能为力
3. 没有做 clustering，只是轻量级去重

所以它是一个**够用但不聪明**的 dedupe。

---

## 4. `Map` 阶段的 `parallel()`

脚本里：

```js
const maps = (await parallel([
  () => agent(... map:cli-config ...),
  () => agent(... map:session-recall ...),
  () => agent(... map:session-reflection ...),
  () => agent(... map:docs-tests ...)
])).filter(Boolean)
```

### 为什么这里用 `parallel`

因为四个 mapper 是天然独立的：

- 彼此不需要对方结果才能开始
- 共同目标只是构成仓库地图

这里不需要 barrier 以外的复杂控制，直接并行最省 wall-clock。

### 为什么 `filter(Boolean)`

因为 workflow 里任何一条并行支路失败都会返回 `null`，而不是整个 workflow 中断。

这使得局部失败不会拖垮整体。

---

## 5. `mapSummary`

脚本里把结构化 map 压缩成一个可读字符串：

```js
const mapSummary = maps.map((m, i) => `...`).join('\n\n')
```

### 为什么要做这一步

后面的 reviewer 和 synthesizer 需要共享地图上下文。

直接把原始 JSON 丢进 prompt 虽然也行，但可读性较差，而且对模型不够自然。

把它整理成“编号 + 关键文件 + 行为 + 风险 + 测试视角”的文字摘要，能更稳定地给后续 agent 提供全局视图。

这一步相当于把 `Map` 结果变成后续阶段的共享 briefing。

---

## 6. `reviewTasks`

脚本中定义了 6 个 review task，每个 task 只有两个字段：

- `key`
- `prompt`

### 为什么 review task 设计成静态数组

因为这次要审的是整个仓库，不是 diff 中动态发现的文件列表。

这里更适合用“固定维度面板”而不是“先发现文件，再动态组装任务”。

### 为什么 prompt 里都重复强调几件事

每个 prompt 都强调：

- 仓库路径
- 只审 tracked code/docs
- 忽略 `.codex/` 和 `tmp/`
- 只输出最有价值的问题
- 必须给文件和行号

这是为了防止 reviewer 任务漂移。

如果不反复约束，它们很容易：

- 去看本地未跟踪产物
- 报很泛的问题
- 给不出具体落点

---

## 7. `pipeline()` 为什么这样用

脚本最关键的执行结构：

```js
const reviewed = await pipeline(
  reviewTasks,
  task => agent(task.prompt, { ... FINDINGS_SCHEMA ... }).then(result => ({ task, result })),
  bundle => parallel((bundle.result.findings || []).map((finding, index) => () => agent(... VERDICT_SCHEMA ...)))
)
```

### 为什么这里用 `pipeline` 而不是两段 `parallel`

因为每个维度的 review 一出来，就可以立即进入 verify。

不需要等全部 6 个 reviewer 都跑完，再统一进入核实。

这样做有两个好处：

1. **更快**
   - architecture 的 findings 可以先开始 verify
   - 不必等 testing reviewer 也跑完

2. **上下文更清楚**
   - 每条 finding 的 verify 都明确知道它来自哪个 review focus

这就是典型适合 `pipeline` 的场景。

### Stage 1：先 review

第一个 stage 负责：

- 跑 reviewer
- 返回 `{ task, result }`

这样第二阶段就能同时拿到：

- 原始任务定义
- 对应 findings

### Stage 2：对每条 finding 再 parallel verify

第二个 stage 不是串行验证，而是对该 reviewer 产出的所有 findings 并行验证。

原因很简单：

- 同一 reviewer 的多条 finding 也互相独立
- 没必要串行等待

所以这里是“pipeline 里面再嵌一个 parallel”。

---

## 8. `verifiedFindings`、`confirmed`、`watchItems`

脚本里：

```js
const verifiedFindings = reviewed.flat().filter(Boolean)
const confirmed = dedupeFindings(verifiedFindings.filter(item => item.verdict && item.verdict.isReal && item.verdict.confidence >= 7))
const watchItems = dedupeFindings(verifiedFindings.filter(item => !item.verdict || !item.verdict.isReal || item.verdict.confidence < 7)).slice(0, 8)
```

### 为什么 confirmed 的门槛是 `isReal && confidence >= 7`

这是一个偏保守的 gate。

目的是让 confirmed findings 更接近“可以直接写进最终报告”的层级。

- `isReal` 保证 verifier 明确判真
- `confidence >= 7` 保证不是低把握通过

### 为什么 watch items 收集的是其余部分

watch items 不是失败品，而是“还不够硬，不该进 confirmed”的观察。

这类点对后续人工复查仍然有价值。

### 为什么 `watchItems` 要 `slice(0, 8)`

因为 watch list 的价值在于提示，不在于穷尽。

如果不做上限，它很容易再次变成长尾噪音，削弱最终摘要的可读性。

这里取 8 是一个经验型上限，足够保留主要观察点。

---

## 9. `Synthesize`

脚本里最后又起了一个 agent：

```js
const synthesis = await agent(... { schema: FINAL_SCHEMA })
```

### 为什么 synthesis 还要单独起 agent

因为前面几个阶段产出的是：

- 结构化地图
- 结构化 findings
- 结构化 verdict

但还没变成一份真正“写给人看”的审查结论。

`synthesis` 这一层做的是：

- 从 confirmed 中挑真正重要的问题
- 从 watchItems 中保留值得看的项
- 给出整体质量判断
- 抽出 strengths

### 为什么 synthesis 不直接在主脚本里手写拼装

当然也可以手写拼装，但那样会损失一个能力：

- 让独立 agent 重新站在“资深工程师总结”的角度做信息压缩

这次任务目标是深审报告，不只是数据结构。

所以 synthesis 还是值得独立存在。

---

## 10. `return` 对象为什么保留原始层和收敛层

最终返回结构里同时保留：

- `maps`
- `confirmed`
- `watchItems`
- `synthesis`
- `reviewCount`
- `candidateCount`
- `confirmedCount`

### 设计意图

这样结果有两层用途：

1. **给用户看**，主要看 `synthesis`
2. **给后续自动化或人工复盘看**，可以回到 `maps` 和 `confirmed` 原始层

如果只返回 `synthesis`，信息太少。
如果只返回原始 findings，信息又太散。

所以这里保留了中间层，方便后续继续加工。

---

## 为什么这次 workflow 用了 `parallel + pipeline + dedupe` 的组合

这其实是三种不同作用：

### `parallel`

解决“天然独立的工作不要排队”的问题。

用于：

- 多个 mapper 并行
- 同一个 reviewer 产出的多条 finding 并行 verify

### `pipeline`

解决“每个任务完成后应该立刻进入下一阶段，而不是全体等齐”的问题。

用于：

- `review task -> verify findings`

### `dedupe`

解决“不同视角可能命中同一核心问题”的问题。

用于：

- confirmed findings 去重
- watch items 去重

这三者组合起来，才能同时兼顾：

- 速度
- 独立视角
- 结果收敛

---

## 这次脚本的局限和下次可改进点

这版 workflow 是有效的，但也有明显局限。

### 1. structured output 太严格，导致部分 subagent 失败

这次实际出现了多次：

- `subagent completed without calling StructuredOutput`

说明 schema 驱动虽然提高了结构化程度，但也降低了鲁棒性。

#### 后续改进方向

- 降低 schema 严格度
- 或在 prompt 里更明确强调“最终必须走 structured output”
- 或对 mapper / reviewer 允许半结构化中间层，再统一整理

### 2. dedupe 还是偏浅

当前 fingerprint 是：

- `file:line:category:title`

这对“字面相近”的重复项有效，但对“语义重复、落点略不同”的问题还不够聪明。

#### 后续改进方向

- synthesis 前先做一次更高层的语义归并
- 或引入“canonical issue family”字段

### 3. verify 只有单 reviewer，没有多票机制

这次是“一条 finding -> 一个 verifier”。

这比不 verify 好很多，但仍然不是最保守做法。

#### 后续改进方向

如果要进一步压误报，可以升级成：

- 一条 finding -> 2~3 个 verifier
- 少数服从多数

代价就是 token 和时间更高。

### 4. synthesis 仍然是模型压缩，不是确定性排序

这意味着最终摘要的措辞和排序还是会带有模型判断。

#### 后续改进方向

如果要更稳定：

- 先在脚本里按 severity / confidence 做确定性排序
- 再把排序后的结构喂给 synthesis agent

---

## 结论

这次 workflow 的核心设计哲学不是“让更多 agent 同时干活”，而是：

1. **先建立系统地图**，避免盲审
2. **再按问题维度分审**，避免单一视角
3. **再用 verifier 专门压误报**，避免把怀疑当结论
4. **最后把结构化中间产物压缩成能指导行动的报告**

如果要再压缩成一句话，就是：

> 先理解，再挑刺，再反驳，再定论。

这套方式很适合：

- 审整个仓库
- 查架构与边界问题
- 需要低误报
- 最终要形成可执行整改建议

而不太适合：

- 只看一个很小的 diff
- 只想要快速 yes/no review
- 不在乎误报，只想多找线索

---

## 建议的复用方式

如果后续要把这套 workflow 复用到别的仓库，可以优先保留这些骨架：

- 四阶段结构：`Map -> Review -> Verify -> Synthesize`
- review 按维度拆，不按目录拆
- verify 默认否决
- confirmed 与 watch 分层
- synthesis 只做收敛，不再找新问题

真正需要按项目调整的部分只有三块：

1. `Map` 的子系统划分
2. `Review` 的维度 prompt
3. `schema` 的字段约束强度

这三块调好，整套方法可以迁移到大多数中型代码仓库。
