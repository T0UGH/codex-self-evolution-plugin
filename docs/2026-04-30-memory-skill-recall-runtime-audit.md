# 2026-04-30 Memory / Skill / Recall Runtime Audit

> 目的: 记录本地 `~/.codex-self-evolution` 真实运行产物的审计结论, 作为后续
> brainstorm "记忆质量、skill 质量、recall_candidate 质量、Codex 不触发 recall"
> 的基线。本文只记录现状和待讨论问题, 不做代码修改方案定稿。

---

## 1. 当前确认的运行状态

本次审计以本机 runtime 目录为主证据:

- runtime home: `~/.codex-self-evolution`
- project buckets: `~/.codex-self-evolution/projects/*`
- structured logs: `~/.codex-self-evolution/logs/plugin.log*`
- 当前仓库: `/Users/bytedance/code/github/codex-self-evolution-plugin`

只读核查结果:

- suggestion envelope 总数: `670`
- suggestion item 分布:
  - `memory_updates`: `704`
  - `recall_candidate`: `259`
  - `skill_action`: `17`
- 最终资产数量:
  - memory records: `79` (`user=8`, `global=71`)
  - recall records: `45`
  - managed skill 文件: `10`
  - manifest 中有效 managed skill entries: `6`
- 当前 pending/failed suggestions: 基本为 `0`; scheduler/compile 主链路在跑。

日志侧统计:

- `plugin.log*` 中 `stop-review` 记录约 `1345` 条。
- 有 suggestion family 的 stop-review 成功样本约 `594` 条。
- 这些样本中 suggestion 总数约 `825`, family 分布:
  - `memory_updates`: `625`
  - `recall_candidate`: `199`
  - `skill_action`: `1`
- `scan` 记录约 `660` 条, 说明 scheduler 定时扫描在持续运行。
- `recall` 命令日志只看到 `1` 次, 说明 live turn 中 recall 基本没有被实际触发。

结论: `memory` 主链路已经实际运行; `recall_candidate` 有沉淀但调用很少;
`managed skills` 有落盘产物, 但运行时接入不足。

---

## 2. Managed Skills 的真实差距

### 2.1 skill_action 产量极低

从所有历史 suggestion 看, `skill_action=17`, 占比明显低于 memory 和 recall。
从结构化日志看, 近期 stop-review 里 `skill_action` 只有 `1` 条。

这说明当前 "自动归纳 skill" 主要依赖单轮 reviewer 偶发判断, 不是一个真正
跨历史分析的 skill synthesis 机制。

### 2.2 skill 文件没有进入 SessionStart 上下文

`SessionStart` 当前注入到 Codex `additionalContext` 的内容只有:

- `USER.md`
- `MEMORY.md`
- `session_recall` skill
- recall policy

managed skill manifest path 虽然在 `runtime.managed_skills_manifest_path` 返回,
但 Codex 不会自动读取这个路径。也就是说, 即使
`skills/managed/*.md` 文件已经生成, 它们默认也不会进入模型上下文。

实测示例: `luna_inner_bot` bucket 下 manifest 有 active skills, 但
`format_session_start_for_codex(...).hookSpecificOutput.additionalContext`
里不包含这些 skill 标题或内容。

结论: managed skills 当前更像 "落盘资产", 还不是实际会被自动使用的运行时能力。

### 2.3 skill 文件和 manifest 已经出现不一致

发现的现象:

- 有 skill 文件但 manifest 无对应 entry, 例如:
  - `cursor-agent-fresh-chat.md`
  - `code-review-request-prep.md`
  - `.codex-memories` bucket 下的两个 skill
- 有 reviewer 提议过但没有最终文件/manifest 的 skill, 例如:
  - `bytedance-grafana`
  - `safe-file-creation`
- 有空壳 skill 文件, 内容基本只有标题, 例如:
  - `conservative-local-cleanup.md`
  - `lunacli-bnpm-release.md`

可能原因:

- agent compiler 路径信任 agent 返回的 `compiled_skills` 和 `manifest_entries`,
  没有强制从 `compiled_skills` 生成/修正 manifest entries。
- `_parse_compiled_skills` 对 content 没有强制非空/高信号校验。
- agent backend 返回空 manifest 时, writer 可能把已有 manifest 写空。

待 brainstorm: skill writer 是否应该改成 "manifest 由本地 deterministic 层
根据 compiled_skills 统一生成", 而不是信任 agent 产出的 manifest。

---

## 3. Memory 质量待评估

当前 memory records 总数 `79`, 说明链路确实在沉淀。但接下来需要看质量,
不是只看数量。

建议评估维度:

1. **稳定性**
   - 是否保存了短期任务进度、已完成 commit、MR 状态、临时 TODO?
   - 是否两周后会失效?

2. **去重质量**
   - 是否存在语义重复但 exact content 不同的条目?
   - `replace/remove` 是否真的在收敛旧条目, 还是仍然不断 append?

3. **scope 分流**
   - 用户偏好是否进入 `USER.md`?
   - 项目事实是否进入 `MEMORY.md`?
   - 是否有 repo-local 事实被错误写进 user scope?

4. **可用性**
   - SessionStart 注入后, 模型是否真的会遵守这些 memory?
   - 条目是否太抽象, 导致对后续任务帮助有限?

5. **容量**
   - 当前 soft budget 是 `MEMORY.md ~2200 chars`, `USER.md ~1375 chars`。
   - 是否已有 bucket 超预算?
   - 超预算后是否应该自动压缩, 还是只允许 replace/remove?

待 brainstorm: 需要一个 memory quality audit 命令, 还是先用一次性脚本输出
"重复/短期/低信号/疑似错 scope" 报告。

---

## 4. Recall Candidate 质量待评估

当前 recall records 总数 `45`, suggestion 中 `recall_candidate=259`。这说明很多
candidate 在 compile 时被丢弃、去重、或未进入最终 recall index。

建议评估维度:

1. **可检索性**
   - summary 是否包含未来查询会用到的关键词?
   - content 是否足够具体, 还是只是泛泛总结?

2. **粒度**
   - 是否过大, 像一次会话摘要 dump?
   - 是否过小, 只有一句无法复用的事实?

3. **生命周期**
   - 是否应该过期?
   - 是否需要 last_used / hit_count / created_at 等元数据来管理?

4. **和 memory 的边界**
   - durable fact 是否应该进 memory, 而不是 recall?
   - 长过程、证据链、一次性上下文是否应该进 recall?

5. **实际命中**
   - 当前 `recall` 日志只看到 1 次, 缺少 hit-rate 数据。
   - 在没有触发的情况下, recall_candidate 的质量很难通过使用反馈优化。

待 brainstorm: recall index 是否需要更强的 query router 和日志, 至少记录
何时应该触发但没有触发。

---

## 5. Codex 几乎不触发 Recall 的问题

当前机制:

- SessionStart 注入 `session_recall` skill 和 recall policy。
- 这个 skill 告诉模型: 当用户提到 previous / remember / again / before 等信号时,
  运行 `codex_self_evolution.cli recall --query ... --cwd ...`。

实际问题:

- `~/.codex/hooks.json` 中 codex-self-evolution 只安装了 `SessionStart` 和 `Stop`。
- 没有 codex-self-evolution 的 `UserPromptSubmit` recall-trigger hook。
- `recall` 调用依赖模型在对话中主动决定跑命令; 从日志看几乎没有发生。
- 也就是说, recall 现在是 "提示模型自己记得查", 不是 "系统层自动触发"。

可能方向:

1. **UserPromptSubmit hook 自动触发**
   - 在用户输入进入模型前, 用轻量规则判断是否需要 recall。
   - 命中时把 recall 输出注入 additionalContext 或 hook output。
   - 优点: 不依赖模型自觉。
   - 风险: Codex hook 协议是否支持该事件的上下文注入, 需要实测。

2. **SessionStart 注入更强的 recall 使用规则**
   - 把 trigger cues 写得更强, 明确遇到历史/之前/继续/同样方法时必须调用 recall。
   - 优点: 改动小。
   - 风险: 仍依赖模型服从, 可能改善有限。

3. **在 Stop 阶段生成下一轮 recall hints**
   - reviewer 不只写 recall_candidate, 还写 "下次遇到哪些 query 应触发 recall"。
   - SessionStart 注入这些 hints。
   - 优点: 能把 recall 从被动搜索变成有路标。
   - 风险: 可能增加上下文噪声。

4. **显式用户命令化**
   - 提供 `/recall` 或固定提示词约定, 用户需要时显式触发。
   - 优点: 可控、误触发少。
   - 风险: 违背自动化目标, 用户负担高。

待 brainstorm: 如果目标是 "Codex 自动用上之前积累的上下文", 优先级应高于
继续增加 recall_candidate 数量。

---

## 6. 下一步 Brainstorm 议题

建议分四轮讨论, 每轮先定目标再定实现:

1. **Memory 质量**
   - 什么样的 memory 算高质量?
   - 是否要清理历史存量?
   - 是否需要质量评分和自动拒写?

2. **Skill 质量**
   - skill 应该是短规则、完整 SKILL.md, 还是只做 runtime hints?
   - 自动生成 skill 是否应该先进入 draft 状态, 人确认后 active?
   - manifest 和文件应该以谁为权威?

3. **Recall Candidate 质量**
   - recall 应保存什么, 不保存什么?
   - 是否要引入命中率、过期、压缩?
   - 是否要把 recall_candidate 按 repo / cwd / global 分层?

4. **Recall 触发机制**
   - 继续依赖模型主动调用, 还是加 hook/router?
   - 触发应该是规则、LLM 判定, 还是混合?
   - recall 输出应该注入多少, 如何避免污染当前任务?

---

## 7. 暂不做的事

- 暂不删除或改写本地 runtime 里的 memory / recall / skill 文件。
- 暂不修改 hook、compiler 或 reviewer 代码。
- 暂不把上述方向定为最终方案。
- 暂不把空壳 skill 自动补全, 先确认 skill 的目标形态。

---

## 8. 第二轮质量审计结论

> 时间: 2026-04-30  
> 范围: 只读检查 `~/.codex-self-evolution/projects/*` 下现有
> memory / recall / managed skill 资产, 以及 `plugin.log*` 运行日志。

本轮不是继续看链路是否跑通, 而是看已经沉淀出来的内容质量。

### 8.1 总体结论

当前不是 "完全没价值", 而是:

- `memory`: 有信号, 但已经混入明显短期状态和任务过程信息。
- `recall_candidate` / final recall: 有材料, 但 metadata、可达性、触发和检索都弱。
- `managed skills`: 质量最差。部分文件可用, 但空壳、缺 manifest、未注入运行时的问题同时存在。

粗略分级:

| 类型 | 当前质量 | 判断 |
| --- | --- | --- |
| `memory` | C+ / B- | 有价值条目不少, 但污染和超预算明显 |
| `recall` | C | 有历史上下文, 但多为短期状态, 缺来源和命中反馈 |
| `managed skills` | D | 文件存在不等于能力可用, lifecycle 和 runtime 接入都没闭环 |

---

## 9. Memory 质量审计

### 9.1 统计结果

本轮实时看到 memory record 共 `82` 条:

- `user`: `7`
- `global`: `75`
- exact content duplicate: `0`
- exact summary duplicate: `0`

精确去重有效, 但语义质量问题依然明显。

高风险 bucket:

| bucket | user_n | global_n | global chars | volatile-like entries |
| --- | ---: | ---: | ---: | ---: |
| `-Users-bytedance-go-src-code.byted.org-luna-luna_inner_bot` | 0 | 37 | 12478 | 9 |
| `-Users-bytedance-go-src-code.byted.org-luna-commerce_offer_feature.archived.20260422T151809` | 0 | 19 | 6245 | 2 |
| `-Users-bytedance-go-src-code.byted.org-luna-commerce_bot_faas` | 0 | 3 | 1644 | 0 |

原 soft budget 是:

- `MEMORY.md`: ~2200 chars
- `USER.md`: ~1375 chars

`luna_inner_bot` 和 archived `commerce_offer_feature` 已经明显超预算。

### 9.2 好的 memory 样例

这些条目基本符合 durable memory 标准:

- `CLI tool preference hierarchy for Bytedance projects`  
  工具路由规则稳定, 后续任务会反复用到。

- `Dolphin API: strategy-workflow endpoints do not exist (all 404)`  
  来自真实验证, 对后续 Dolphin Lite 设计有长期影响。

- `Classic Dolphin vs Lite data structure difference`  
  描述结构差异和真实文件位置, 对类似排查有复用价值。

- `Known RPC error pattern: invalid header length[66564]`  
  具体错误模式 + 调用路径, 后续报警排查可直接复用。

### 9.3 主要问题

#### 1. 短期状态进入 durable memory

典型条目:

- `Second round review approved; build/test commands were actually run`
- `MR !14 missing review docs (review.md, review-round2.md, docs/)`
- `Review process: round1 complete, documentation artifacts pending inclusion in MR`
- `Review round2 code pushed, docs excluded from commit`
- `本轮 plan 已落地，包含 6 个任务覆盖 C/H/M/L 四类`

这些内容更像 session state / handoff / recall, 不应该进入长期 memory。
它们会在 MR 合入、分支删除、任务结束后快速失效。

#### 2. 一些条目应该是 recall, 不是 memory

例如:

- `review.md workflow: conclusions documented, final checklist pending`
- `dolphin_sync timeout architecture issue H2 identified`

这些条目有临时过程价值, 但不适合在每个 SessionStart 都稳定注入。

#### 3. scope 分流仍不准

可疑 user-scope 条目:

- `User works in common_task_rpc subdirectory`
- `User works in Chinese, uses go-test-clean for validation`
- `User prefers lunacli-first for Dolphin work`

其中 "用户讲中文" 是 user preference; 但具体 repo 目录、某个 repo 的测试命令、
Dolphin toolpath 更像 project/global memory。现在 user scope 会把这些事实带到
过宽的上下文里。

### 9.4 Memory 质量判断

`memory` 的问题不是无效, 而是边界不严:

- "长期事实" 和 "任务过程" 混在一起。
- "用户偏好" 和 "项目约定" 混在一起。
- soft budget 只在 prompt 中提示, 没有硬性执行或自动压缩。

后续 brainstorm 需要重点决定:

1. 是否允许 task-state 进入 memory? 如果允许, 生命周期如何管理?
2. 是否需要 memory quality audit / lint 命令?
3. 超预算时是拒写、压缩, 还是强制 reviewer 走 replace/remove?

---

## 10. Recall / Recall Candidate 质量审计

### 10.1 统计结果

final recall records 共 `45` 条。

质量 flags:

- `volatile_terms`: `19`
- `maybe_memory_not_recall`: `9`
- `no_source_paths`: `20`
- `no_source_updated_at`: `40`

candidate 到 final recall 的转化差异很大:

| bucket | recall_candidate suggestions | final recall |
| --- | ---: | ---: |
| `-Users-bytedance-go-src-code.byted.org-luna-luna_inner_bot` | 29 | 29 |
| `-Users-bytedance-go-src-code.byted.org-luna-commerce_offer_feature.archived.20260422T151809` | 10 | 10 |
| `-Users-bytedance-go-src-code.byted.org-luna-commerce_bot_faas` | 58 | 0 |
| `-Users-bytedance-go-src-code.byted.org-luna-commerce_membership_api` | 35 | 0 |
| `-Users-bytedance-go-src-code.byted.org-luna-treasure_business` | 67 | 1 |
| `-Users-bytedance-code-github` | 26 | 1 |

这说明 recall compile 行为受 backend 输出影响很大, 不是一个稳定可解释的
candidate -> index 规则。

### 10.2 好的 recall 样例

- `Dolphin strategy workflow API is unconfirmed — do not assume it exists in v1`  
  查询 "Dolphin strategy workflow API history_only SignalWorkflow" 时命中第一条。

- `Lite v1 三步改法：detect_mode 改 history_only、detector 支持、SignalWorkflow 删除下沉 v2`  
  对继续处理 Lite v1 设计时有直接上下文价值。

- `Task 601836 incentive ad configuration pattern`  
  有明确 task id 和字段模板, 属于可检索的业务上下文。

### 10.3 主要问题

#### 1. 很多 recall 是短期任务状态

典型条目:

- `MR !14 commit 9e2aa43 — source branch feature/luna_dolphin_repo, target master`
- `pending deployment steps for dolphin_sync`
- `Dolphin sync snapshot.go review in progress`
- `MR creation pending, docs not in commit`
- `review.md TODO item H2 pending update`

这些更像 "session continuation state"。如果 recall 支持过期/last_used 还可以;
但现在没有生命周期字段, 会长期留在 index 里。

#### 2. metadata 不完整

很多 recall 缺:

- `source_paths`
- `source_updated_at`
- 可用于过期判断的 `created_at`
- 命中统计 `last_used` / `hit_count`

没有这些字段, 后续无法判断它是否仍然可信、是否被用过、是否该压缩或删除。

#### 3. 一些 recall 应该升格为 memory

例如:

- `Sorting key pattern: Version desc + UpdatedAt tie-breaker`
- `Three-layer timeout architecture in commerce_bot_faas`
- `bot.go 中 classifyCollectErr 只看 CollectErrors[0] 的设计前提`
- `新建 vs 覆盖的文件操作歧义在 superpowers plan 场景中出现过`

这些更像稳定规则或项目经验, 不只是按需召回材料。

#### 4. archived bucket 让 recall 不可达

实测:

```bash
codex-self-evolution recall \
  --query "coin widget ColdLaunchText commerce_info box sign_in" \
  --cwd /Users/bytedance/go/src/code.byted.org/luna/commerce_offer_feature
```

结果为空。

但相关 recall 实际存在于 archived bucket:

- `-Users-bytedance-go-src-code.byted.org-luna-commerce_offer_feature.archived.20260422T151809`

这说明 worktree 迁移/归档后, recall 没有一并迁入 canonical bucket, 导致历史上下文
从正常 cwd 查不到。

#### 5. `recall` CLI 输出过宽

实测:

```bash
codex-self-evolution recall \
  --query "Dolphin strategy workflow API history_only SignalWorkflow" \
  --cwd /Users/bytedance/go/src/code.byted.org/luna/luna_inner_bot
```

返回了非常长的结果列表, 包含不少低相关内容。

相比之下:

```bash
codex-self-evolution recall-trigger \
  --query "Dolphin strategy workflow API history_only SignalWorkflow" \
  --cwd /Users/bytedance/go/src/code.byted.org/luna/luna_inner_bot
```

只返回 top 3, 质量更可控。

### 10.4 Recall 质量判断

`recall` 的素材质量是 C, 不是因为完全没内容, 而是:

- 存了太多 session continuation state。
- 缺少生命周期元数据。
- archived bucket 破坏可达性。
- `recall` 和 `recall-trigger` 行为不一致, 一个全量输出, 一个 top_k。
- 实际触发次数太少, 没有 hit-rate 反馈闭环。

---

## 11. Managed Skill 质量审计

### 11.1 统计结果

当前 managed skill 文件共 `10` 个。

质量 flags:

- `missing_manifest`: `4`
- `thin_body`: `2`
- `no_trigger_language`: `2`
- `no_operational_anchor`: `5`

存在 skill_action suggestion 的 bucket:

| bucket | skill suggestions | files | manifest |
| --- | --- | --- | --- |
| `-Users-bytedance` | `cursor-agent-fresh-chat` create + 4 patch | 1 | 0 |
| `-Users-bytedance-.codex-memories` | `conservative-local-cleanup`, `lunacli-bnpm-release` | 2 | 0 |
| `-Users-bytedance-go-src-code.byted.org-luna-commerce_bot_faas` | `code-review-request-prep` | 1 | 0 |
| `-Users-bytedance-go-src-code.byted.org-luna-luna_inner_bot` | 6 suggestions | 4 | 4 |
| `-Users-bytedance-go-src-code.byted.org-luna-server_cc_marketplace` | `image-to-html-recreation` | 1 | 1 |
| `-Users-bytedance-go-src-code.byted.org-luna-treasure_business` | `bytedance-grafana` | 0 | 0 |

### 11.2 好的 skill 样例

这些比较接近可用:

- `lunacli-trace-debug`
  - 有触发条件: given share link or logid
  - 有具体命令: `lunacli anywhere share`, `lunacli log trace`
  - 有输出要求: HTTP/business status, WARN/ERROR, timeout/noise 判断

- `session-log-document-restore`
  - 有明确触发条件: document corrupted/truncated
  - 有步骤: parse JSONL Write/Edit, replay, backup, byte verify

- `code-review-iterative`
  - 有工作流步骤: maintain review.md, ask agreement, update conclusions

### 11.3 主要问题

#### 1. 文件存在但 manifest 缺失

例如:

- `cursor-agent-fresh-chat.md`
- `code-review-request-prep.md`
- `.codex-memories/skills/managed/conservative-local-cleanup.md`
- `.codex-memories/skills/managed/lunacli-bnpm-release.md`

这会导致后续 `managed_skills_summary` 看不到它们, patch/edit 的 ownership 校验也
无法稳定工作。

#### 2. 空壳 skill

例如:

```text
# Conservative Local Cleanup
# Lunacli bnpm Release
```

这类文件没有触发条件、执行步骤、工具命令、输出标准, 不应该进入 active skill。

#### 3. 有 suggestion 但没有最终资产

例如:

- `safe-file-creation`
- `bytedance-grafana`

`safe-file-creation` 的 suggestion content 只有 2 个词, 被低信号过滤合理;
但 `bytedance-grafana` content 有 136 words, 却没有最终 file/manifest, 说明 agent
compiler 或 manifest reconcile 仍有不稳定路径。

#### 4. skill 不进入 SessionStart

即使 manifest 正常, 当前 SessionStart 也不会注入 active managed skill 内容。
因此 skill 的实际运行价值接近 0。

### 11.4 Skill 质量判断

managed skills 当前是 D:

- 少数文件本身可读。
- lifecycle 不完整。
- manifest 和文件不同步。
- active/draft 概念缺失。
- 空壳 skill 能落盘。
- 运行时完全没接入。

讨论 skill 质量前, 需要先决定 skill 的产品形态:

1. 是完整 `SKILL.md` 风格能力?
2. 还是短 runtime hints?
3. 是否需要 draft -> active 的人工确认?
4. 是否所有 skill 都必须有 trigger / steps / verification?

---

## 12. Recall 触发质量审计

### 12.1 统计结果

`plugin.log*` 中:

- `session-start`: `122`
- `recall`: `1`
- `recall-trigger`: `0`

这说明 recall 几乎没有真实使用。

当前 hook 安装情况:

- codex-self-evolution managed `SessionStart`
- codex-self-evolution managed `Stop`
- 没有 codex-self-evolution managed `UserPromptSubmit`

### 12.2 当前机制的根本问题

SessionStart 注入的 `session_recall` skill 只是提示模型:

> 有历史上下文需求时, 自己运行 recall 命令。

实践上模型几乎不会主动调用。

`recall-trigger` 这个 CLI 本身可用:

- multi-term query 会触发。
- top_k=3 输出比裸 `recall` 更可控。

但它没有被 hook 接入, 所以不会自动跑。

### 12.3 触发规则也需要重新设计

当前 `evaluate_recall_trigger` 规则:

- `explicit`
- query >= 2 terms
- 英文 marker: `remember`, `previous`, `again`, `recall`, `before`

问题:

- `multi_term_query` 太宽, 真装进 hook 可能大量误触发。
- 没有中文 marker, 例如 "之前", "继续", "上次", "同样方法", "还按那个".
- 没有 repo/task 类型判断。
- 没有输出 token budget 控制策略。

### 12.4 Recall 触发判断

不是 recall_candidate 数量不够, 而是没有可用的触发闭环。

如果目标是 "Codex 自动用上历史上下文", 那么优先级应该是:

1. 先做触发和注入机制。
2. 再治理 recall index 质量。
3. 最后增加 recall_candidate 产量。

---

## 13. 建议的讨论顺序

建议下一轮 brainstorm 不从实现开始, 先定边界:

1. **Memory 边界**
   - durable memory 是否允许任务状态?
   - user/global scope 如何判定?
   - 超预算如何处理?

2. **Recall 边界**
   - recall 是 "历史证据库" 还是 "session continuation state"?
   - 是否需要 TTL / hit_count / last_used?
   - archived bucket 的 recall 是否必须迁移?

3. **Skill 边界**
   - skill 是完整能力还是短提示?
   - 是否需要 draft/active 生命周期?
   - manifest 和文件谁是权威?

4. **Recall 触发机制**
   - UserPromptSubmit hook 是否可行?
   - 触发规则是 keyword、LLM judge, 还是 hybrid?
   - 输出注入多少, 如何避免污染当前任务?

---

## 14. Brainstorm 后的第一轮实现决议

本轮讨论先不采用 `UserPromptSubmit` 规则系统替 Codex 做判断, 而是让 Codex
自己触发 recall。

### 14.1 Recall 自触发

已定:

- 覆盖面: repo/workspace 相关、非平凡、可能依赖历史上下文的任务, Codex 必须先轻量 recall。
- hard-skip: 简单计算、短翻译/改写、纯格式化、用户已给全量上下文的当前文件小改。
- 触发主体: Codex 自己判断, 不是 hook 规则替它判断。
- 命令形态: 提供短命令 `csep recall "<focused query>"`。
- query 生成: Codex 先把自己要找的历史上下文压缩成一句 focused query。
- 输出形态: 默认 Markdown, 面向模型直接阅读; JSON 只作为 `--format json` 调试/测试入口。
- 默认条数: top 3。
- 空结果/失败: 软失败继续, 不阻塞任务, 不编造历史。
- 用户可见性: 有命中且影响回答时自然提; 空结果不额外解释。
- telemetry: 记录 cwd、query hash、命中数、耗时、退出状态, 不记录召回正文。

### 14.2 Managed skill 发布

已定:

- 自动生成 skill 不直接把 draft 写进全局 skill 根目录。
- 内部源文件仍由插件管理, 保留 owner / managed / manifest 约束。
- 通过质量门禁并进入 active 的 skill, 第一版默认全局生效。
- 全局投影路径采用:

```text
~/.codex/skills/csep-managed/<csep-prefixed-skill-id>/SKILL.md
```

- 目录 namespace 只负责归档和批量回滚; skill id 自身必须带 `csep-` 前缀来避免命名冲突。

### 14.3 第一轮代码落点

第一轮先做最小闭环:

- `csep recall` 短命令。
- `recall-trigger` 默认 Markdown, `--format json` 保留机器接口。
- `SessionStart` 注入明确 `Recall Contract`。
- active managed skill 发布到 `csep-managed/csep-*` 全局投影。
- 发布时只处理 plugin-owned managed skill, 并对低信号内容跳过。

### 14.4 第一轮 smoke 暴露的新质量问题

`csep recall "managed skills runtime gap"` 能正常返回 Markdown, 但命中的旧 recall
内容仍记录了更早的作用域偏好: "默认项目内生效, 显式 promote 才进全局"。
这已经被后续讨论改为 "active/promoted skill 第一版默认全局生效"。

这说明 recall 质量治理还需要补:

- 新决议应能覆盖或 retire 旧 recall。
- recall 记录需要 `source_updated_at` / `superseded_by` / `last_used` 之类的治理字段。
- hit 后如果发现和当前会话明确决议冲突, 应优先当前用户输入, 并把旧 recall 标为待清理。
