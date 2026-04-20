# Codex Self-Evolution Plugin Handoff：compiler 仍缺少 existing assets 输入（2026-04-20）

## 这份 handoff 是干什么的

记录当前 `codex-self-evolution-plugin` 在 compiler 路径上还没补齐的关键缺口，避免后续继续把问题理解成：

- 还要不要单独 writer
- 要不要把 merge 规则继续写死到脚本里
- 是不是只要把 opencode 接上就算完成

当前已经确认的方向是：

> **memory / recall 的 merge 判断交给 compiler agent。**

所以这次真正还差的，不是再造一层 writer，也不是先补一堆复杂 heuristic，
而是：

> **让 compiler 在运行时真正看到 existing memory / existing recall，再基于“旧资产 + 新 batch”做编译。**

---

## 当前已经完成的部分

### 已完成 1：最终写入已收口到 compiler engine
当前最终资产写入已经不再由独立 `writer.py` 持有，而是收口到：

- `src/codex_self_evolution/compiler/engine.py`

包括：
- memory 写入
- recall 写入
- managed skill 写入
- receipt 写入

这部分已经完成，并已 push。

### 已完成 2：session recall 注入层已补上
已补：
- `SessionStart` 注入 `session_recall`
- recall policy / skill 被带入 session payload
- recall workflow 开始走 session-level bridge

这部分也已经完成，并已 push。

---

## 现在真正还差的是什么

## 核心缺口

### compiler 现在仍然只是在处理 “new batch”，没有把 “existing assets” 喂进去
当前 compile 主路径仍是：

```text
pending suggestion batch
  -> build_compile_context(...)
  -> backend.compile(batch, context, ...)
  -> apply_compiler_outputs(...)
```

问题不在输出侧，而在输入侧。

当前 `backend.compile(...)` 拿到的 context 主要只有：
- `cwd`
- `repo_fingerprint`
- `skills_dir`
- `existing_manifest`

这意味着：
- **skills 的旧状态**已经能看到
- **memory / recall 的旧状态**仍然看不到

所以即使未来 `agent:opencode` 变成真的 compiler agent，
如果 context 里还是没有 existing memory / existing recall，
它本质上仍然只能做：

> **基于本轮 batch 重写当前结果**

而不是：

> **在旧资产基础上增量编译**

---

## 这不是要改哪层

这次不是优先改：
- `session_start.py`
- `stop_review.py`
- `recall/workflow.py`
- `cli.py`

因为这次的核心不是 recall trigger，也不是 reviewer output 生成，
而是：

> **compile 时，compiler 到底能看到哪些输入。**

---

## 当前最该改的 3 个位置

## 1. `src/codex_self_evolution/compiler/backends.py`

### 这里是第一优先级
当前这里有两个关键职责：
- `build_compile_context(...)`
- `AgentCompilerBackend.compile(...)`

### 这里现在缺的不是“更多规则”，而是“更多输入”
`build_compile_context(...)` 应该从当前 state 里再读取并注入：

- `existing_user_memory`
- `existing_global_memory`
- `existing_memory_index`
- `existing_recall_records`
- 最好额外带上：
  - `memory_paths`
  - `recall_paths`

对应来源至少包括：
- `memory/USER.md`
- `memory/MEMORY.md`
- `memory/memory.json`
- `recall/index.json`
- `recall/compiled.md`

### 为什么这步最关键
因为你已经决定：

> **merge 判断交给 compiler agent 做。**

那系统必须先把“旧资产”喂给它。

如果这一步没做，后面无论 opencode prompt 写得多漂亮，
它都只能围着 new batch 打转。

---

## 2. `AgentCompilerBackend.compile(...)`

文件：
- `src/codex_self_evolution/compiler/backends.py`

### 当前状态
当前 `agent:opencode` 还是 scaffold：
- 不可用时 fallback 到 `script`
- 可用时本质上也还是 fallback 到 `script`
- 还没有真正把旧资产 + 新 batch 组织成 agent compile 输入

### 这里真正要补什么
不是把 merge 逻辑写死到 Python。

而是要把这层变成：

> **受约束的 compiler agent runtime**

输入应至少包含：
- 当前 batch suggestions
- existing memory
- existing recall
- existing managed skill manifest
- repo/cwd metadata
- compile contract / merge contract

输出仍然应该是受约束的 compiler artifacts，而不是自由写一堆文件。

### 这层补完后，agent 才真的有资格负责判断：
- 哪些旧条目保留
- 哪些新条目追加
- 哪些内容合并
- 哪些旧条目修订
- 哪些 recall 应该淘汰或降级

---

## 3. `compiler/memory.py` 与 `compiler/recall.py`

文件：
- `src/codex_self_evolution/compiler/memory.py`
- `src/codex_self_evolution/compiler/recall.py`

### 为什么还要改这两个
虽然主方向已经定成“判断交给 compiler agent”，
但当前 fallback backend 还是 `script`。

如果这里仍然只处理 new batch：
- 一旦 opencode unavailable
- 或 agent backend 继续 fallback

系统就还是会退回覆盖式 compile。

### 所以这两层至少要做最小增量 fallback
不是让它们变聪明，
而是让它们别明显错误。

最小要求：
- 读 existing memory / existing recall
- 默认保留旧条目
- 新条目 append / dedupe
- 不要因为本轮没提到某条旧记忆就把它刷掉

也就是说：

> **script backend 不必成为主方案，但不能成为“退回就洗掉旧资产”的破坏性 fallback。**

---

## 现在的准确问题定义

不是：
- “writer 还在不在”
- “要不要继续加 merge 规则”
- “是不是只差接 opencode CLI”

而是：

> **compiler 的输入模型还停留在 `new batch -> compile -> write`，没有升级到 `existing assets + new batch -> compile -> write`。**

---

## 推荐的下一步实施顺序

### P1. 先扩 compile context
先改：
- `build_compile_context(...)`

目标：
- 让 compiler context 真带上 existing memory / recall

这是最小但最关键的一步。

### P2. 再改 agent backend
再改：
- `AgentCompilerBackend.compile(...)`

目标：
- 让 opencode 的 compile 输入不再只是 batch + metadata
- 而是 existing assets + batch + contract

### P3. 最后兜底改 script fallback
再改：
- `compiler/memory.py`
- `compiler/recall.py`

目标：
- 即使 fallback，也至少是“保守增量 merge”
- 而不是覆盖式 compile

---

## 一句话总结

当前代码已经完成了：

> **“compiler 负责最终写入”**

但还没有完成：

> **“compiler 在旧资产基础上做真正的增量编译”**

下一步真正该补的核心不是 writer，不是 UI，不是 trigger，
而是：

> **把 existing memory / existing recall 正式纳入 compiler 的运行时输入。**
