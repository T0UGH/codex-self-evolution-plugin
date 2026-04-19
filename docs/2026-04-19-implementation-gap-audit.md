# Codex Self-Evolution Plugin 实现偏移审计（2026-04-19）

## 目的

记录当前代码实现相对于已确认 design 的主要偏移，避免把当前仓库误判为“已对齐 design 的 v1 成品”。

当前更准确的定性是：

> **pipeline skeleton / scaffold 已完成，但 3 条核心 runtime 仍未真正落地。**

---

## 已确认的 3 个核心偏移

### 1. reviewer 还是 mock / stub，不是真实 reviewer runtime

#### design 期望
- `Stop` 后执行一次轻量 reviewer pass
- 真正完成沉淀判断
- 输出固定 JSON suggestion
- reviewer 是受限单次模型调用，而不只是 schema 校验器

#### 当前实现
- `src/codex_self_evolution/review/runner.py`
- `run_reviewer()` 只会：
  - 读取 `review_input["reviewer_output"]`
  - 或回退为空 JSON
  - `json.loads(...)`
  - `ReviewerOutput.from_dict(...)`
- 没有真实模型调用
- 没有新 Codex 实例
- 没有 minimax / mmc / opencode / subprocess runtime

#### 结论
> **review interface 是真的，但 review intelligence 还是假的。**

---

### 2. compiler 没有真实定时调度 runtime

#### design 期望
- compiler 由纯定时批处理拉起
- 外部 scheduler 明确存在（cron / launchd / task runner）
- 后台按固定频率扫描 `pending`
- 锁冲突时应有可解释的跳过行为

#### 当前实现
- `src/codex_self_evolution/compiler/engine.py`
- 只有 `run_compile(..., batch_size=DEFAULT_BATCH_SIZE)`
- 只能单次执行后退出
- 没有内置调度
- 没有默认 schedule
- 没有 launchd / cron 配置或安装产物
- 有文件锁：`compile.lock`
- 但锁冲突没有 graceful skip 语义，只会抛异常退出

#### 结论
> **compiler command 是真的，但 background scheduler runtime 还没落地。**

---

### 3. compiler 不是“便宜 agent compiler”，而是本地规则编译器

#### design 期望
- 后台 compiler 可以是受限的便宜 coding agent
- 做 suggestion 归并 / 去重 / 编译成 artifact
- 能承担一定整理智能，而不只是硬编码规则

#### 当前实现
- `src/codex_self_evolution/compiler/engine.py`
- `compiler/memory.py`
- `compiler/recall.py`
- `compiler/skills.py`
- 全部为本地 Python 规则逻辑
- 没有外部 agent runtime
- 没有 minimax / opencode / codex worker

#### 结论
> **compile pipeline 是真的，但 compile intelligence 仍是弱规则版。**

---

## 额外发现的其他偏移

### 4. SessionStart 没有把 `USER.md` / `MEMORY.md` 真正注入，只返回 recall policy

#### design 期望
- `SessionStart` 读取 `USER.md`
- `SessionStart` 读取 `MEMORY.md`
- `SessionStart` 读取轻量 recall skill / recall policy
- 组装成稳定前缀与 recall control layer
- 注入当前 session/thread 起点

#### 当前实现
- `src/codex_self_evolution/hooks/session_start.py`
- 只做：
  - `ensure_runtime_dirs(...)`
  - 读取 `recall/policy.md`
  - 返回 `recall_policy`
- 没有读取 `USER.md`
- 没有读取 `MEMORY.md`
- 没有真正组装 frozen background / stable prefix

#### 结论
> **SessionStart 当前只实现了 recall policy preload，memory 背景注入尚未实现。**

---

### 5. suggestion/event store 不是 design 里的 DB/状态机，而是 append-only JSON 文件队列

#### design 期望
- suggestion/event store 负责状态跟踪
- 至少有：`pending / processing / done / failed / discarded`
- 提供可重试、幂等键、待消费队列

#### 当前实现
- `src/codex_self_evolution/storage.py`
- `append_pending_suggestion()` 直接写 JSON 文件到 `data/suggestions/pending/`
- `archive_processed()` 直接 move 到 `processed/`
- 没有 `processing` / `failed` / `discarded` 显式状态
- 没有 DB
- 没有显式幂等键索引
- 没有重试状态模型

#### 结论
> **当前是文件队列，不是完整 suggestion/event store 状态机。**

---

### 6. review snapshot reconstruction 还没真正实现

#### design 期望
- `Stop` hook 提供最小上下文
- review runner 再补拉：
  - transcript
  - `thread/read(includeTurns=true)`
  - `USER.md`
  - `MEMORY.md`
  - skills 摘要
- 再重建 review snapshot 执行受限 review

#### 当前实现
- `src/codex_self_evolution/hooks/stop_review.py`
- `_build_review_input()` 只从 hook payload 里取：
  - `thread_read_output`
  - `transcript`
  - `hook_payload`
  - `reviewer_output`
- 没有主动补拉 transcript
- 没有主动调用 `thread/read`
- 没有读取 `USER.md` / `MEMORY.md`
- 没有 skills summary

#### 结论
> **当前只是在消费“已经准备好的 payload”，不是在做真正的 post-turn snapshot reconstruction。**

---

### 7. recall 还没有“条件触发 workflow”，只有显式 CLI 查询

#### design 期望
- `SessionStart` 预加载 recall policy/skill
- 回合内在命中条件时自动触发 recall workflow
- same-repo / same-cwd 优先

#### 当前实现
- `src/codex_self_evolution/recall/search.py`
- 只有显式 `recall` CLI 命令
- 没有条件触发器
- 没有 in-turn automatic workflow
- 没有“模型命中条件后自动调用”的桥接层

#### 结论
> **retrieval logic 已有，但 recall trigger runtime 还没实现。**

---

### 8. managed skill 边界实现还比较薄

#### design 期望
- 系统 skill 应有目录隔离
- 元数据里体现 owner / managed
- lifecycle 由系统治理

#### 当前实现
- `src/codex_self_evolution/managed_skills/manifest.py`
- 只有简单 manifest
- `writer.py` 直接写 `skills/<skill_id>.md`
- manifest 里没有明确 `owner` / `managed` 字段
- 没有更强的目录隔离策略

#### 结论
> **managed skill lifecycle 已有雏形，但 ownership boundary 仍然偏薄。**

---

## 当前实现的准确定位

### 不应表述为
- “已完整实现 design v1”
- “reviewer / compiler 后台智能链路已落地”

### 更准确的表述
- **已完成 self-evolution pipeline skeleton**
- **已完成 state / schema / writer / queue / recall-read 的基础设施**
- **尚未完成 3 条核心 runtime：reviewer runtime、scheduler runtime、compiler intelligence runtime**

---

## 建议的后续收口顺序

### P1. 真实 reviewer runtime
- 优先接 `mmc` / minimax CLI（若可用）
- 或抽象可插拔 reviewer provider
- 目标：把 `run_reviewer()` 从 stub 变成真实单次模型调用

### P2. 真实 scheduler runtime
- 选定 `cron` / `launchd`
- 固化默认频率
- 加锁冲突 graceful skip
- 补 stale lock 恢复策略

### P3. compiler intelligence runtime
- 决定保留规则版还是升级成便宜 agent compiler
- 若升级，需明确 minimax / opencode / codex worker 路线

### P4. memory/session start 真注入
- 真正读取并注入 `USER.md` / `MEMORY.md`
- 而不只是返回 recall policy

---

## 一句话总结

> **当前仓库已经把 self-evolution 插件的“结构骨架”搭起来了，但离 design 中真正的 v1 行为实现，还差 reviewer runtime、scheduler runtime、compiler intelligence runtime 这三条主链路，以及若干配套能力。**
