# 提炼代码规范 Skill V1 Design

## 背景

`commerce_membership_api` 等 Luna Go 服务里，人工 review 和用户纠正常能发现 agent 的编码规范问题，例如范围越界、过度抽象、handler/service/domain 分层混乱、测试 mock 方式错误、临时 debug 日志进入 MR 等。这些问题现在可能散落在 session transcript、CSEP suggestion、review.md 或 MR 评论处理过程中，但缺少一个轻量机制把它们转成接近 `AGENTS.md` 的可执行规则。

V1 目标不是全量自动治理，也不是自动修改 `AGENTS.md`，而是提供一个 skill：用户在当前或历史上下文中说“提炼代码规范”时，agent 能按固定流程筛选人机摩擦或人工 review 驱动的 session，resume 复盘，最后输出一份 Markdown 规则候选文档。

## 目标

1. 创建一个名为“提炼代码规范”的 skill 设计，稳定目录名建议为 `extract-coding-rules`。
2. 支持从多个 Codex session 中采样，默认分析本周、Top 15 个符合条件的 session。
3. 只采样两类 session：
   - 用户和 agent 发生明确摩擦、纠正、返工、恢复、回退、范围收窄。
   - 用户明确要求 agent 使用 `bytedcli` 拉 MR 评论并修复，或明确处理人工 review comments。
4. 对入样 session 执行受控 resume 复盘，要求复盘 agent 不继续原任务、不改文件、不运行工具，只输出结构化分析。
5. 将最终结果写成 Markdown 到用户指定目录；未指定时写入 `~/.codex/reports/coding-rules/`。
6. 产出 AGENTS.md 风格的候选规则：分类、规则正文、正例、反例、原因、证据。

## 非目标

- 不自动修改任何 `AGENTS.md`。
- 不要求每个 resume 的 session 都产出规则。
- 不把普通 bug 修复、普通功能开发、agent 自行重构作为采样依据。
- 不扫描全历史作为默认行为；V1 默认本周，可由用户指定时间窗。
- 不把提炼结果直接写入 CSEP memory、recall 或 skill synthesis。

## 触发方式

Skill frontmatter 建议：

```yaml
name: 提炼代码规范
description: 当用户要求从当前会话、多个历史 session、人机摩擦、用户纠正、人工 review/MR comments 修复过程中提炼可沉淀到 AGENTS.md 的编码规范，并输出 Markdown 文档时使用。
```

典型用户说法：

- “提炼代码规范”
- “分析 membership_api 最近两周的 session，提炼代码规范”
- “把这次人和 agent 的冲突沉淀成 AGENTS.md 规则”
- “resume 最近一个月处理 MR 评论的 session，提炼规范”
- “从当前 session 的返工里总结编码规范”

## 默认参数

- `repo_filter`: 从用户输入提取，例如 `membership_api`；未提供时使用当前 cwd 名称。
- `time_window`: 默认本周。
- `limit`: 默认 Top 15 eligible sessions。
- `out_dir`: 默认 `~/.codex/reports/coding-rules/`。
- `output_file`: `coding-rules-<YYYY-MM-DD>-<repo-or-current>-<window>.md`。

用户可覆盖：

```text
提炼代码规范：分析 membership_api 最近一个月，resume top 20 sessions，输出到 /tmp/rules
```

## 采样契约

采样必须先过硬 gate，再排序。没有过 gate 的 session 不能用于提炼规范。

### 入样条件

满足任一条件即可入样：

1. 用户和 agent 有明确摩擦：
   - “不是这个意思”
   - “不要改代码”
   - “恢复”
   - “回退”
   - “过度抽象”
   - “你理解错了”
   - “只处理 review.md”
   - “新写一份，原文保留”
   - 用户中断后改变方向
2. 人工 review 驱动：
   - 用户明确要求用 `bytedcli` 拉 MR 评论。
   - 用户要求处理 MR comments、review comments、人工 reviewer 意见。
   - session 内容包含基于 reviewer 评论的修复或讨论。

### 排除条件

以下 session 即使代码修改很多，也不能入样：

- agent 自己发现 bug 并修复。
- 普通功能开发。
- 普通测试失败修复。
- 普通日志排查。
- agent 自己做的重构。
- 没有人类纠正或人工 review 输入的代码修改。

### 排序信号

只在 eligible sessions 内排序，建议分值信号：

- 用户纠正/回退/恢复类关键词数量。
- 用户中断次数。
- 是否出现 `bytedcli` + MR comments。
- 是否存在多轮“review 观点 -> agent 修改 -> 用户确认/否定”。
- 是否命中代码规范关键词：handler、service、domain、mockey、go.mod、IDL、debug log、AGENTS.md、review.md、scope。

## Resume 复盘流程

对 Top-K session 使用 `codex resume <session_id> <prompt>`，prompt 必须让 agent 进入复盘模式。

复盘 prompt 草案：

```text
你正在复盘这个历史 session。不要继续完成原任务，不要修改任何文件，不要运行工具。

只分析本 session 中人和 agent 的摩擦点、用户纠正、返工点、人工 review/MR comment 修复点。
目标是提炼可写入 AGENTS.md 的编码规范，而不是总结任务进度。

如果本 session 没有可复用编码规范，请返回 status=no_rule，不要硬凑规则。

请只输出 JSON：
{
  "session_id": "<current session id>",
  "status": "rules_found|no_rule|unclear",
  "friction_points": [
    {
      "kind": "user_correction|scope_violation|review_comment|rollback|over_abstraction|test_pattern",
      "summary": "...",
      "evidence": "引用本 session 中的具体用户纠正或 review 触发点"
    }
  ],
  "coding_rule_candidates": [
    {
      "category": "分层架构|结构设计|代码风格|测试相关|AI工作流|代码清理|安全相关|业务逻辑",
      "rule_title": "...",
      "rule_text": "...",
      "positive_example": "...",
      "negative_example": "...",
      "reason": "...",
      "evidence": "...",
      "confidence": 0.0
    }
  ],
  "discarded": [
    {
      "summary": "...",
      "reason": "task_state_only|not_coding_rule|not_human_friction|too_specific|duplicate"
    }
  ]
}
```

## 汇总规则

汇总阶段读取每个 resume 的 JSON 输出：

1. `status=no_rule` 是正常结果，记录到统计中，不视为失败。
2. 规则按 `category + normalized rule_title + rule_text` 去重。
3. 相似规则合并证据，提升置信度。
4. 没有 evidence 的规则不能进入最终“可沉淀规则”。
5. 过于具体的 session 状态进入“不建议沉淀”。

## Markdown 输出结构

文件写入指定目录，内容结构：

```md
# 提炼代码规范报告

> 统计范围：<time window>；repo 过滤：<repo_filter>；扫描 <N> 个 session，入样 <M> 个，resume <K> 个，产出规则 <R> 条。

## 1. 采样摘要

- scanned_sessions:
- eligible_sessions:
- skipped_no_human_friction_or_review:
- resumed_sessions:
- sessions_with_rules:
- sessions_without_rules:
- final_rule_candidates:

## 2. 可沉淀到 AGENTS.md 的候选规则

##### A?. <规则标题>

<规则正文>

```go
// 正例
...

// 反例
...
```

**原因**：...
**证据**：session_id、用户纠正或 MR review 摘要
**置信度**：...

## 3. Session 复盘摘要

按 session 列出 friction points 和是否产出规则。

## 4. 不建议沉淀的内容

列出被丢弃的候选及原因。

## 5. 建议人工确认的问题

列出仍需人判断是否写入 AGENTS.md 的规则。
```

## Skill 文件形态

建议使用低到中自由度：

```text
extract-coding-rules/
├── SKILL.md
└── scripts/
    ├── select-sessions.py
    ├── run-resume-review.sh
    └── merge-rule-candidates.py
```

`SKILL.md` 保持简洁，只描述触发条件、默认参数、执行顺序和输出路径。具体 session 选择、resume 调用、JSON 合并由脚本负责，减少 agent 每次重写逻辑的波动。

## 错误处理

- 找不到 session：输出 Markdown，说明 scanned_sessions=0。
- 没有 eligible session：输出 Markdown，说明没有满足采样契约的 session。
- resume 某个 session 失败：记录为 `status=resume_failed`，继续处理其他 session。
- 某个 resume 输出非 JSON：记录失败原文摘要，继续处理其他 session。
- 最终无规则：仍输出报告，明确说明没有高质量候选，不硬凑。

## 成功标准

V1 完成后，用户可以执行类似请求：

```text
提炼代码规范：分析 membership_api 本周 session
```

并得到一份 Markdown 文件，至少包含：

- 采样统计。
- 被 resume 的 session 列表。
- 每个 session 是否产出规则。
- 0 到多条 AGENTS.md 风格候选规则。
- 对未产出规则 session 的原因说明。

如果没有高质量规则，报告必须诚实输出“无可沉淀规则”，而不是编造。

## 后续扩展

- 增加 HTML 报告，复用 `codex-insights` 的视觉模板。
- 增加 `--apply` 模式，在用户确认后把候选规则追加到指定 `AGENTS.md`。
- 增加跨 repo 规则聚类，把多仓重复摩擦沉淀成 Luna 根规则。
- 增加 CSEP evidence workspace 支持，让该 skill 可以复用 materialized evidence，避免直接扫描全局状态。
