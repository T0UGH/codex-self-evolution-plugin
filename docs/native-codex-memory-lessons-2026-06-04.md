# Codex 原生 Memory 对 CSEP 的借鉴点

日期：2026-06-04

本文记录对 Codex 原生 memory 机制的研究结论，以及 CSEP 可以借鉴的设计方向。结论基于当前本地 Codex 源码和 CSEP 现有架构，不代表长期稳定 API。

## 背景判断

Codex 原生 memory 和 CSEP 的目标接近，都是把历史 session 中可复用的信息沉淀到后续 session 可用的上下文里。但两者的产品取向不同：

- Codex 原生 memory 更像产品内置的保守长期记忆系统，重点是低扰动、可治理、可清洗。
- CSEP 更像本机增强插件，重点是可见、可查、可直接生成原生 skill，并且能快速迭代。

因此 CSEP 不应该完整照搬原生 memory，而应该吸收其中对长期质量、污染隔离和使用反馈更成熟的部分。

## 原生 Memory 的关键设计

### 热摘要和冷资料分层

原生 memory 不在启动时直接注入完整历史，而是默认注入 `memory_summary.md` 的有限摘要。完整资料继续保存在 `MEMORY.md`、`rollout_summaries/` 和 `skills/` 等冷区，只有模型判断需要时再读取。

这个设计的核心价值是：启动上下文稳定、短小、低噪声，同时仍保留可追溯的长资料入口。

### 两阶段沉淀

原生写路径分成两个阶段：

- Phase 1：从单个历史 rollout 中抽取 `raw_memory` 和 `rollout_summary`。
- Phase 2：在全局视角下合并多个候选，更新 `MEMORY.md`、`memory_summary.md`，并整理冷资料。

这避免了单个 session 的偶然结论直接进入长期热上下文。长期记忆必须经过“先成为候选，再全局合并”的沉淀过程。

### 写入当前 turn 之外的历史

原生 memory 的写任务在有用户输入的 turn 启动后被 opportunistic 地触发，但实际处理的是已经 idle 的历史 thread，并且显式排除当前 thread。

这个时机选择不是为了马上总结当前 turn，而是为了借 app-server 已经活跃、用户又开始工作的窗口，后台处理旧资料，同时避免把尚未收束的当前上下文写入长期记忆。

### 使用反馈回流

原生 read path 要求模型在使用 memory 后通过 citation 标记来源。系统会把 citation 回流到 `usage_count` 和 `last_usage`，Phase 2 再用这些信号选择更值得保留或提升的候选。

这让 memory 不只是“写进去”，而是有机会根据真实使用情况保鲜和淘汰。

### 污染隔离

原生 memory 会标记可能被外部上下文污染的 thread，例如 web/tool-search 输出参与后的上下文，并在写入或遗忘流程里区别处理。

这个设计承认一个现实：不是所有 session 内容都适合进入长期记忆。外部资料、一次性网页、临时搜索结果很容易把本地偏好和项目经验污染掉。

### Consolidation 工作区和 git baseline

原生 Phase 2 会在 memory root 下维护 git baseline，并生成类似 `phase2_workspace_diff.md` 的差异视图，让 consolidation agent 基于“上次稳定状态到本次候选”的增量来整理。

这个设计把长期记忆维护变成一个可审查的变更过程，而不是每次都盲读全部历史。

### Memory-local skills

原生 memory 可以整理 `skills/<skill-name>/SKILL.md`，但这些并不是 Codex 原生 skills loader 会自动注册的技能。它们更像 memory 内的 SOP 文档，只有当 `MEMORY.md` 指向它们时，模型才会按需打开。

这是一种更保守的 skill 形态：先沉淀为冷资料，再视使用价值决定是否提升为真正的运行时 skill。

## CSEP 可以借鉴的方向

### 1. 引入热摘要文件

当前 CSEP 的 Stable Memory 默认全文注入 `MEMORY.md`。这个模型简单透明，但随着 `MEMORY.md` 变大，启动上下文会越来越重。

建议引入可选的 `memory_summary.md`：

- `SessionStart` 默认注入 `memory_summary.md`。
- `MEMORY.md` 作为完整 registry 和二级入口保留。
- 如果 `memory_summary.md` 不存在，再 fallback 到当前 `MEMORY.md` 行为。

这样可以保持兼容，同时给长期体积治理留出口。

### 2. 引入使用反馈

CSEP 可以借鉴 citation 回流，但不一定照搬原生格式。更现实的做法是让 `SessionStart` 注入的 memory 或 recall 片段带稳定 ID，后续在 Stop hook 或 transcript 归档时统计：

- 哪些 memory 条目被 assistant 显式引用。
- 哪些 refs 被打开过。
- 哪些 `csep-reflect-*` skill 被触发过。

这些信号可以进入 reflection prompt，帮助 child 判断哪些内容该保留、压缩、下沉或删除。

### 3. 把单 session 写入改成候选沉淀

当前 CSEP reflection child 可以直接修改 `MEMORY.md`、`refs/` 和 `csep-reflect-*` skill。这个设计有效，但容易让单次 session 的局部经验过快进入热上下文。

建议增加一个轻量候选层，例如：

```text
memory/candidates/
  <job-id>.md
```

reflection child 先写候选摘要、证据来源和建议动作。后续 consolidation job 再把多个候选合并到 `MEMORY.md`、`memory_summary.md` 或 `refs/`。

### 4. 复制污染标记，而不是复制复杂沙箱

CSEP 不需要马上复制原生 memory 的完整污染治理，但可以先给 session archive 增加来源标签：

- 是否包含 web 搜索。
- 是否包含外部页面正文。
- 是否包含大段第三方文档。
- 是否是纯本地代码和用户偏好。

reflection prompt 读取这些标签后，可以默认避免把外部资料写进长期热 memory，只允许写入“如何处理这类资料”的方法论。

### 5. 用 diff 驱动 reflection

CSEP 可以为每次 reflection 保存一个 memory snapshot 或 hash baseline，并在下一次 review prompt 中提供：

- 本次 session 候选。
- 上次 memory 到当前 memory 的 diff。
- 当前 refs / skills 的新增、删除和变更列表。

这样 child 不需要每次重新理解全部 memory，也更容易发现重复、漂移和互相冲突的条目。

### 6. Skill 先 staging，再 promotion

CSEP 的优势是真正生成 `~/.codex/skills/csep-reflect-*`，这比原生 memory-local skills 更直接。但直接生成原生 skill 的成本也更高：一旦出现在 skills loader 里，就会影响后续所有 session 的 skill selection。

建议把 skill 生成分成两层：

- staging：先写 `memory/skills/<name>/SKILL.md`，作为按需 SOP。
- promotion：只有当相关 SOP 被多次引用，或用户明确认可，再提升为 `~/.codex/skills/csep-reflect-*`。

这能减少低价值或过窄 skill 长期污染 skill 列表。

### 7. 引入成本和频率门控

原生 memory 会考虑 rate limit 和候选数量上限。CSEP 当前 trigger 已经有 deterministic policy，但可以进一步把成本信号纳入策略：

- 最近失败率高时降低 reflection 频率。
- 最近连续 `skipped_empty` 时提高短期阈值。
- provider token 或 rate limit 紧张时只做 archive，不跑 reflection。

这可以让后台沉淀更像低优先级维护任务，而不是每次高信号都立即消耗模型预算。

## 不建议照搬的点

### 不建议把写入触发点改到 SessionStart

原生 memory 在用户 turn 启动后触发，是因为它运行在 Codex app-server 内部，能自然复用当前服务生命周期，并且处理的是旧 thread。

CSEP 作为 hook/plugin，更适合继续使用 Stop hook：

- Stop hook 已经能看到完整 session。
- 前台只归档和判定，后台 worker 再处理，退出路径可控。
- 不需要在 SessionStart 时额外抢启动时延。

因此 CSEP 可以借鉴“处理 idle 历史，不写当前未收束上下文”的原则，但不需要照搬原生的触发时机。

### 不建议放弃原生 skill 生成

原生 memory-local skills 更保守，但 CSEP 的核心差异化之一就是能把稳定工作流提升成真正的 Codex skill。

更好的方向不是取消 skill 生成，而是增加 staging 和 promotion 阶段，降低误生成成本。

### 不建议把 recall 替换成 memory MCP

原生 memory MCP 是只读访问 memory 文件的入口，而 CSEP 的 `csep recall` 是 transcript 级证据搜索。两者定位不同。

CSEP 应继续保留 recall 作为历史证据层，memory 只保存压缩后的长期上下文。

## 建议优先级

短期优先做：

1. `memory_summary.md` 热摘要 fallback 机制。
2. session / memory / skill 使用反馈统计。
3. 外部上下文污染标签。

中期再做：

1. 候选层和 consolidation job。
2. diff 驱动 reflection prompt。
3. skill staging 与 promotion。

长期再考虑：

1. 更完整的 memory 生命周期管理。
2. 自动遗忘、降权和冲突检测。
3. 更强的 receipt 与 artifact 审计。

## 当前结论

CSEP 当前比原生 memory 更强的地方，是 recall 可查证、Stop hook 能看到完整 session、以及可以生成真正的原生 skill。

原生 memory 更值得借鉴的地方，是长期治理能力：热冷分层、两阶段沉淀、使用反馈、污染隔离、diff 驱动和成本门控。

最合理的演进路径不是“CSEP 变成原生 memory”，而是让 CSEP 保留本机增强和 skill promotion 的优势，同时把长期 memory 的质量治理补上。
