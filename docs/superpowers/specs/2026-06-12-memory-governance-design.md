# Phase 5 Memory Governance Design

## 背景

Patch 1 到 Patch 4 已经修完 correctness、schema-safe receipt、runtime smoke 和 plugin bundle canonical source。Phase 5 不再是 bugfix，而是 CSEP 长期 memory / reflection 治理路线。它的目标不是把 CSEP 改成 Codex 原生 memory，而是在保留 Stop hook、session recall 和原生 skill promotion 优势的前提下，补上热冷分层、使用反馈、污染隔离、两阶段沉淀和生命周期治理。

当前主链路是：

```text
SessionStart -> 注入 MEMORY.md
Stop -> archive transcript -> deterministic trigger -> reflection child
reflection child -> 直接写 MEMORY.md / refs / csep-reflect-* skill / receipt.json
parent -> 校验 receipt 和 artifact 边界
```

这个模型简单可用，但长期会有三个风险：

- `MEMORY.md` 越来越大，SessionStart 上下文变重。
- 单 session 的局部经验可能过快进入长期热 memory。
- skill 直接 promotion 到 `~/.codex/skills/`，低价值 skill 会污染后续 skill selection。

## 设计原则

- 先治理 read path，再治理 write path。先降低启动上下文风险，再改变 reflection 沉淀模型。
- 先记录元数据，不自动删除。使用反馈和污染标签先用于可观测和 prompt 输入，后续再用于降权、遗忘和 promotion。
- 每一步都可回滚。任何新文件缺失或不可信时，必须回到当前 `MEMORY.md` / direct skill 行为。
- 不改变触发时机。CSEP 继续以 Stop hook 做归档和后台 reflection，不把写入触发点迁到 SessionStart。
- 不取消原生 skill 生成。Phase 5 只增加 staging 和 promotion 门槛。

## 总体分层

```text
memory/
├── MEMORY.md                    # 完整 registry，冷一些但仍可读
├── memory_summary.md            # SessionStart 优先注入的热摘要
├── memory_summary.meta.json     # summary 的来源 hash、生成时间和状态
├── refs/                        # 长资料和证据入口
├── usage.json                   # memory / refs / skills 使用反馈
├── candidates/                  # reflection child 的候选输出
│   └── <job-id>.json
├── skills/
│   └── staging/
│       └── <skill-name>/SKILL.md
└── baselines/
    └── latest.json              # consolidation 的上次稳定状态摘要
```

目录是设计目标，不要求第一步全部创建。Phase 5A 只需要 `memory_summary.md` 和 `memory_summary.meta.json`。

## Phase 5A：热摘要 fallback

### 目标

`SessionStart` 优先注入 `memory_summary.md`，但只有在 summary 可验证时才使用。summary 不存在、为空、meta 缺失或 meta 中的 `source_memory_sha256` 与当前 `MEMORY.md` 不一致时，回退到当前全文 `MEMORY.md` 行为。

### 数据合同

`memory_summary.meta.json`：

```json
{
  "schema_version": 1,
  "source": "MEMORY.md",
  "source_memory_sha256": "<sha256 of MEMORY.md text>",
  "summary_sha256": "<sha256 of memory_summary.md text>",
  "generated_at": "2026-06-12T00:00:00Z",
  "generator": "session_reflection_consolidation"
}
```

Phase 5A 不负责自动生成 summary，只负责安全读取。人工或后续 consolidation 生成的 summary 都必须满足这个 meta 合同。

### SessionStart 行为

- `load_stable_memory()` 改成返回结构化结果，而不是只返回字符串。
- 如果 summary 有效：
  - 注入标题仍用 `# Stable Background`。
  - 内容区标题改成 `## memory_summary.md`。
  - `stable_background` 中暴露 `memory_source = "memory_summary.md"`、`memory_fallback_used = false`。
- 如果 summary 无效：
  - 继续注入 `MEMORY.md`。
  - `stable_background` 中暴露 fallback reason，例如 `summary_missing`、`summary_meta_missing`、`source_hash_mismatch`。

### 成功标准

- 旧机器没有 summary 时行为完全兼容。
- summary 过期时不会注入错误摘要。
- SessionStart payload 能说明实际注入来源，方便 `csep status` 或调试读取。

## Phase 5B：使用反馈和污染标签

### 目标

记录“被注入、被引用、被打开、被触发”的低敏元数据，并为 session archive 增加来源标签。Phase 5B 不自动删除 memory，也不改变 reflection 写入权限。

### 使用反馈

`memory/usage.json`：

```json
{
  "schema_version": 1,
  "items": {
    "memory_summary.md": {
      "kind": "memory_summary",
      "injected_count": 3,
      "last_injected_at": "2026-06-12T00:00:00Z",
      "citation_count": 0,
      "last_cited_at": ""
    },
    "refs/2026-05-18-csep-system-refresh.md": {
      "kind": "ref",
      "open_count": 1,
      "last_opened_at": "2026-06-12T00:00:00Z"
    },
    "csep-reflect-session-receipt": {
      "kind": "skill",
      "trigger_count": 2,
      "last_triggered_at": "2026-06-12T00:00:00Z"
    }
  }
}
```

首版只稳定记录 SessionStart 注入次数。引用、打开和 skill trigger 可以先通过 transcript/archive 中的明确文本和 tool call 进行 best-effort 统计，统计不到时保持 0，不影响主链路。

### 污染标签

在 session archive 或 reflection job metadata 中增加 `context_labels`：

```json
{
  "context_labels": [
    "local_repo_code",
    "user_instruction",
    "agent_injected_context",
    "external_web",
    "third_party_document"
  ]
}
```

首版标签来自确定性规则：

- 出现 web/search/browser 抓取正文时标记 `external_web`。
- 出现 AGENTS / developer / system 注入时标记 `agent_injected_context`。
- 出现本地文件读写和 repo 路径时标记 `local_repo_code`。
- 出现用户明确偏好或纠正时标记 `user_instruction`。

### 成功标准

- 统计数据不保存 transcript 正文、密钥、cookie 或用户隐私 ID。
- reflection prompt 可以读取标签，并默认不把 `external_web` 或 `third_party_document` 当成长期用户偏好。
- 标签缺失时不阻断 archive、recall 或 reflection。

## Phase 5C：候选层和 consolidation job

### 目标

reflection child 先写候选，不直接修改热 `MEMORY.md`。consolidation job 合并多个候选，生成可审计 diff，再更新 `MEMORY.md`、`memory_summary.md` 或 refs。

### 候选文件

`memory/candidates/<job-id>.json`：

```json
{
  "schema_version": 1,
  "job_id": "20260612T000000Z-example",
  "parent_session_id": "session-id",
  "created_at": "2026-06-12T00:00:00Z",
  "context_labels": ["local_repo_code", "user_instruction"],
  "candidates": [
    {
      "id": "sha1:0123456789abcdef",
      "kind": "fact|rule|preference|workflow",
      "summary": "Run uv run pytest -q in this repo.",
      "evidence": [
        {
          "source": "transcript",
          "session_id": "session-id",
          "message_index": 12
        }
      ],
      "recommended_action": "promote_to_memory|promote_to_ref|stage_skill|discard",
      "sensitivity": "normal|sensitive|external"
    }
  ]
}
```

### Consolidation 行为

- 读取 pending candidates。
- 读取当前 `MEMORY.md`、`memory_summary.md` meta、refs index 和 usage metadata。
- 生成 `session_reflection/runs/<job-id>/consolidation.diff.md`。
- 只有通过 validation 后才写入 memory artifacts。
- 已处理 candidate 写入 receipt 或 state，避免重复合并。

### 成功标准

- 单 session 局部经验不会未经合并进入热上下文。
- consolidation diff 能解释新增、压缩、下沉或丢弃的原因。
- Phase 5C 仍保留 fallback：如果 consolidation 失败，旧 memory 不被破坏。

## Phase 5D：skill staging 和 promotion

### 目标

reflection 发现 workflow 时先写 memory-local staging skill，只有达到 promotion 条件才写入 `~/.codex/skills/csep-reflect-*`。

### Staging 路径

```text
memory/skills/staging/<skill-name>/SKILL.md
```

Staging skill 仍必须满足正式 skill 的结构要求：

- Skill Decision
- When to Use
- Inputs
- Workflow
- Verification
- Failure Handling

### Promotion 条件

首版只允许两种 promotion：

- 用户明确要求提升。
- 使用反馈显示同一 staging skill 被引用或触发至少 2 次，且最近一次 validation 通过。

Promotion 后写入正式目录：

```text
~/.codex/skills/csep-reflect-<name>/SKILL.md
```

### 成功标准

- 低价值 workflow 不直接污染正式 skill namespace。
- promotion 行为能在 receipt 中看到来源 staging path、原因和目标 path。
- 现有正式 `csep-reflect-*` skill 继续可用，不需要迁移。

## Phase 5E：生命周期治理

### 目标

在前几阶段有元数据和候选层后，再做自动遗忘、降权、冲突检测和 diff-driven prompt。

### 策略

- 自动遗忘只处理低风险内容：过期候选、未使用 staging skill、重复 refs。
- 用户偏好和 repo 规则默认不自动删除，只允许降权或要求人工确认。
- 冲突检测先产出 validation note，不自动选边。
- diff-driven prompt 基于 `baselines/latest.json` 和上次成功 consolidation hash，不重新扫全量 memory。

### 成功标准

- memory 不只增长，也能解释为什么某条内容被降权或移除。
- 所有删除或降权都能追溯到 receipt、usage metadata 或 candidate evidence。

## 实施顺序

1. 先做 Phase 5A：热摘要 fallback 和 freshness guard。
2. 再做 Phase 5B 的最小元数据：SessionStart 注入次数和 session context labels。
3. 等 5A/5B 稳定后，再设计 5C 的 candidate schema 和 consolidation receipt。
4. Skill staging 放在 candidate 层之后做，避免同时改 memory 和 skill 两条写路径。
5. 生命周期治理最后做，只消费前面阶段已经稳定产生的数据。

## 验证策略

- 每阶段都要有 focused pytest 覆盖新 schema、fallback、损坏文件和旧状态兼容。
- 每阶段完成后跑 `uv run pytest -q` 和 `git diff --check`。
- 涉及 SessionStart 的阶段要跑 hook payload 测试，确保 fresh machine 没有 memory 时仍返回合法 `additionalContext`。
- 涉及 reflection 写入的阶段要跑 runner / validation / status 组合测试，确保失败分类不退回到模糊 system error。
- 涉及 skill promotion 的阶段要跑真实路径边界测试，确保不能写出 `~/.codex/skills/csep-reflect-*` 之外。

## 非目标

- 不把 CSEP recall 替换成 memory MCP。
- 不把写入触发点迁移到 SessionStart。
- 不在 Phase 5A 自动生成 summary。
- 不在 Phase 5B 自动删除或降权 memory。
- 不在 Phase 5C 同时引入完整遗忘策略。
- 不迁移现有正式 `csep-reflect-*` skill。

## 需要用户确认的策略点

我建议默认执行 Phase 5A，且只实现安全读取，不实现 summary 自动生成。这样可以先获得热摘要入口和 fallback 保护，同时避免把 reflection 写路径、consolidation 和 skill staging 一起改掉。
