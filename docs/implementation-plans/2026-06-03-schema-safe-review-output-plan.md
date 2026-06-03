# Schema-safe Review Output Plan

日期：2026-06-03

## 1. Goal

记录一个新的实现思路：

> 既然后台 Codex Spark / review 模型经常因为手写 schema JSON 失败而被脚本判定失败，那么是否应该由 CSEP CLI 提供一个专门命令，负责把模型产出的结果草稿转换为**符合 schema 的最终结果文件**；如果格式不符合预期，CLI 必须响亮报错并返回非零退出码。

本计划的目标不是立即改代码，而是评估这个思路是否值得做，以及如果做，应该如何收敛范围。

本次新增一个特别强调的原则：

> CLI 不只是最终 writer，还应该尽早检查输入对错，并把明确、可消费的错误及时反馈给 agent，避免 agent 在错误格式上继续盲写或盲试。

---

## 2. Problem Statement

当前 reflection / review 类后台任务里，模型经常直接负责写最终结构化结果，例如 `receipt.json`。

这种做法的核心问题是：

1. **模型在“内容判断”之外，还承担了“最终文件格式正确性”责任**
2. 结果一旦出现格式偏差，就会被 runner / validation 判成失败
3. 失败未必代表业务判断错了，很多时候只是：
   - JSON 结构不严格
   - placeholder 没替换干净
   - shell 风格字符串残留
   - timestamp 不合法
   - 顶层字段缺失
   - 列表字段类型不对
4. 这类失败会污染系统观感，也让后台 job 看起来比实际更不稳定
5. 现有失败很多是“事后失败”，不是“尽早失败”，agent 往往在错误格式上已经走了太远，反馈回路太慢

本质上，这是把“模型擅长的判断任务”和“程序更擅长的确定性格式生成”混在了一起。

---

## 3. 核心想法

### 3.1 思路概括

让模型不再直接手写最终 schema 文件，而是改成：

1. 模型产出**语义草稿**
2. CLI 命令负责：
   - 校验输入字段
   - 补齐可由父进程或 wrapper 负责的固定字段
   - 生成 canonical JSON
   - 原子写入目标路径
3. 如果输入不符合要求：
   - CLI 明确打印错误
   - 返回非零退出码
   - 不生成伪成功产物
   - 尽量把错误分类成 agent 能直接理解并重试修正的反馈

换句话说：

> 不让模型直接写最终 receipt/result，而是让模型调用一个“schema-safe writer”。

### 3.2 新增强调：writer 也是 feedback gate

这个命令不只是一个“最后落盘器”，还应该是一个**快速反馈闸门**。

理想行为是：

- agent 产出 draft
- CLI 立刻检查
- 如果错，马上告诉 agent “哪一项错了、为什么错、应该改哪一类字段”
- agent 再重试

而不是：

- agent 写出错误 receipt
- runner 晚一点才发现失败
- 整个 job 被记成一次模糊的失败

也就是说，这个命令既是 writer，也是一个 schema-level linter / validator。

---

## 4. 为什么这个方向是对的

我认为这个思路**很有价值，而且方向是对的**。

原因不是“它能修所有问题”，而是它抓住了一个非常本质的边界：

### 4.1 模型适合做判断，不适合做最终格式落盘

模型擅长的是：

- 判断候选内容值不值得沉淀
- 归类 fact / rule / preference / workflow
- 写 memory / skill 正文
- 给出 skipped / partial / failed 的语义解释

模型不擅长的是：

- 严格 JSON 语法
- 时间戳规范
- 字段名拼写零误差
- 原子写文件
- 路径合法性
- shell / placeholder 清理

这些本来就应该由程序承担。

### 4.2 把“业务失败”和“格式失败”分开

现在很多失败实际上是“格式失败”，不是“审查语义失败”。

如果引入 schema-safe writer，就能更清楚地区分：

- 模型确实没产出有效判断
- 模型有判断，但给 CLI 的输入不完整或无效
- durable output 本身越界或 hash 不匹配

这会让状态语义更干净。

### 4.3 更符合当前仓库的设计哲学

这个仓库本来就在强调：

- 父进程主导边界
- trust boundary 不信 child 口头声明
- 确定性工作交给程序而不是模型

所以从架构上看，这个想法和现有方向是一致的，不是逆着系统设计来的。

### 4.4 能显著缩短 agent 的错误反馈回路

这是这次新增强调的关键点。

如果 CLI 能在 draft 阶段就指出：

- 哪个字段缺了
- 哪个字段类型错了
- 哪个 status 不合法
- 哪个 JSON 根本不可解析

那么 agent 会更容易在**当前上下文**里修正，而不是等 runner/validation 在后置阶段统一记一次失败。

这比“最后才知道失败”要值钱得多。

---

## 5. 我建议的落地方向：先窄做，不要一上来做通用框架

这个想法是好的，但我不建议一开始就做成一个“什么 schema 都能写”的超通用系统。

### 建议的第一步

先只解决当前最痛的那条线：

- `session_reflection` 的 `receipt.json`

也就是说，优先做一个类似下面的专用命令：

```bash
csep session-reflection write-receipt --input <draft.json> --output <receipt.json>
```

或者：

```bash
codex-self-evolution session-reflection write-receipt ...
```

它只负责 receipt v1 这个 contract，不要一开始抽象成万能 schema writer。

### 为什么先做窄

因为当前真实痛点最明确、最频繁、最值钱的就是 receipt。

如果一开始就做成：

- 支持任意 schema
- 支持任意 artifact
- 支持任意 field mapping

很容易变成一个抽象过度的系统，反而把简单问题做复杂。

---

## 6. 建议的模型交互模式

### 当前模式（容易出错）

模型直接写最终 `receipt.json`：

- 要自己拼全部字段
- 要自己保证 JSON 严格合法
- 要自己写时间戳
- 要自己原子写

### 建议模式（更稳）

模型只写“草稿结果”，再调用 CLI：

#### 方式 A：模型写一个 draft JSON

例如：

```json
{
  "status": "skipped",
  "memory_changes": [],
  "skill_changes": [],
  "skipped_candidates": ["all duplicate"],
  "validation_notes": ["no reusable delta"],
  "errors": []
}
```

然后调用 CLI：

```bash
csep session-reflection write-receipt \
  --job-id <job> \
  --parent-session-id <session> \
  --child-thread-id <thread> \
  --draft /path/to/draft.json \
  --output /path/to/receipt.json
```

CLI 负责：

- 读取 draft
- 校验字段 shape
- 注入 canonical identity fields
- 生成 `started_at` / `finished_at` 或接收上层传入
- 原子写 output
- 在失败时把明确错误立刻反馈给 agent

#### 方式 B：模型直接调用命令并逐字段传参

例如：

```bash
csep session-reflection write-receipt \
  --status skipped \
  --memory-changes '[]' \
  --skill-changes '[]' \
  --skipped-candidates '["all duplicate"]' \
  --validation-notes '["no reusable delta"]' \
  --errors '[]' \
  ...
```

我不太推荐这种形式。

原因是：

- shell quoting 更脆弱
- 数组和字符串转义更容易炸
- 错误体验更差

### 我的建议

优先选 **方式 A：draft file -> CLI canonical writer**。

这样 shell 面更窄，也更容易做错误提示。

---

## 7. CLI 命令应该承担哪些责任

这个新命令的职责应该非常清楚，不要模糊。

### 7.1 应该做的事

1. 读取 draft
2. 校验 draft 顶层结构
3. 检查必需字段是否存在且类型正确
4. 限制 `status` 只能是允许值
5. 规范化输出顺序
6. 写入 canonical JSON
7. 使用原子写入
8. 遇到错误时打印明确错误并退出非零
9. 尽可能把错误做成 agent 易消费的反馈格式

### 7.2 不应该做的事

1. 不替模型“猜意思”
2. 不自动修复语义错误
3. 不把缺失字段默默补成看起来合法的成功结果
4. 不把无效输入自动吞掉然后回退成 `failed` receipt

这个命令应该是**严格的 writer**，不是宽松的补锅器。

---

## 8. 什么叫“响亮报错”

这里的“响亮”很重要。

如果 CLI 做了这个命令，但遇到非法输入时只是静默返回空文件，或者写一半失败，价值会大打折扣。

### 建议的报错特征

1. **stderr 明确说明哪一项错了**
2. **exit code 非零**
3. **不要写出伪成功 receipt**
4. **错误信息要可直接进入 runner status / validation 分类**
5. **错误内容要足够短、足够具体，让 agent 能据此立即修正**

例如：

```text
receipt draft invalid: field "status" must be one of [succeeded, partial, failed, skipped]
```

或：

```text
receipt draft invalid: field "memory_changes" must be a JSON array
```

### 更进一步

可以考虑让 CLI 在失败时输出一份 machine-readable error JSON 到 stderr 或 sidecar 文件，供 runner 直接分类，也供 agent 直接读取并修正。

例如可以包含：

- `code`
- `field`
- `message`
- `expected`
- `actual`

但这一步不是 MVP 必需。

---

## 9. 这个方案能解决什么，不能解决什么

### 能解决的

1. 非严格 JSON
2. 顶层字段缺失
3. 字段类型错
4. shell placeholder / quoted shell expression 残留
5. timestamp 格式问题（如果交给 CLI 生成）
6. 原子写入一致性问题
7. agent 在错误 schema 上反复盲试而没有及时反馈的问题

### 不能解决的

1. 模型本身做了错误的业务判断
2. memory / skill 内容虽然格式对，但语义低价值
3. 路径越界
4. hash mismatch
5. skill frontmatter / section 不合规

也就是说，它主要解决的是：

> child 产物里的**格式型失败**

而不是所有 reflection failure。

这点必须说清楚，不然容易高估收益。

---

## 10. 对现有代码结构的适配判断

从当前实现看，这个思路是贴合现状的。

### 现有相关位置

- `src/codex_self_evolution/session_reflection/prompt.py`
  - 当前 prompt 要求 child 直接写 `receipt.json`
- `src/codex_self_evolution/session_reflection/runner.py`
  - 负责等待 receipt、准备 canonicalization、再做 validation
- `src/codex_self_evolution/session_reflection/validation.py`
  - 负责 schema / timestamp / identity / boundary / hash 校验
- `src/codex_self_evolution/cli.py`
  - 已经是 retained runtime CLI 面
- `src/codex_self_evolution/csep.py`
  - 已经是用户向短命令面

### 说明

也就是说，这个新能力天然更适合落在：

- `cli.py` / `csep.py` 加一个专用子命令
- `runner.py` / `prompt.py` 改成“让 child 生成 draft，再调用 writer”

而不是硬塞进 `validation.py`。

`validation.py` 应该继续做**结果验收**，不该兼任“帮模型修格式”的职责。

---

## 11. 我对这个方案的总体判断

我的结论是：

> 这个方向值得做，而且很可能是减少后台 schema 格式失败最划算的一步。

但前提是：

### 11.1 要把范围收紧

先做 receipt，不要一上来做万能 schema writer。

### 11.2 要坚持“严格 writer”，不是“模糊修复器”

CLI 应该响亮失败，而不是悄悄帮模型猜测并修补语义。

### 11.3 要让 prompt 一起收敛

如果只是加了 CLI 命令，但 prompt 仍然要求模型自己直接写最终 receipt，那收益有限。

真正要改的是交互契约：

- 模型提供 draft
- CLI 生成 final canonical receipt

### 11.4 要把“及时反馈给 agent”当成一等目标

这个点值得单独强调。

这个命令的价值不只是提高最终 receipt 成功率，更在于：

- 让 agent 在当前上下文里立即知道自己哪里错了
- 缩短试错回路
- 减少一次错误被拖到整个 job 结束才显现的情况

如果做不到及时反馈，它就只是一个更严格的 writer；如果能做到及时反馈，它才真正成为 agent 的 schema guardrail。

---

## 12. 建议的 MVP 范围

如果真的要做，我建议 MVP 只做这些：

### 新增命令

一个专用命令，例如：

```text
csep session-reflection write-receipt
```

### 输入

- `--draft <path>`
- `--output <path>`
- `--job-id`
- `--parent-session-id`
- `--child-thread-id`
- `--started-at`（可选，若不传则由上层控制）
- `--finished-at`（可选）

### 校验

- 顶层必须是 object
- `status` 必须合法
- `memory_changes` / `skill_changes` / `skipped_candidates` / `validation_notes` / `errors` 必须是 list
- 禁止未知顶层字段（可选，MVP 可以先允许后续再收紧）

### 输出

- canonical JSON
- 固定字段顺序
- 原子写

### 失败行为

- stderr 打印人类可读错误
- exit 2 或其他明确非零码
- 不产生伪成功文件
- 错误足够清楚，agent 能据此立即修正

---

## 13. 建议的后续演进顺序

### Phase 1

先做 receipt writer，只服务 `session_reflection`。

### Phase 2

把 prompt 改成：

- 先写 draft
- 再调 writer
- 不再直接手写最终 receipt

### Phase 3

在 runner status / diagnostics 中单独暴露：

- `draft_invalid`
- `writer_failed`
- `validation_failed`

这样状态层能更清楚地区分：

- 模型没给出合格草稿
- CLI writer 执行失败
- receipt 写对了，但 durable artifacts 依然越界或不合法

### Phase 4（可选）

如果 receipt writer 证明很有价值，再考虑抽象成更广义的 schema-safe artifact writer。

但这是后话，不应成为 MVP 的前置目标。

---

## 14. 一句话结论

一句话总结这个想法：

> 很值得做，而且方向是对的；但应该先把它落成一个“专门负责写 reflection receipt 的严格 CLI writer”，而不是一开始就抽象成万能 schema 框架。

再补一句这次特别强调的点：

> 这个 CLI writer 不只是为了写对文件，更是为了尽早检查对错，并把明确错误及时反馈给 agent，缩短整个后台 review / reflection 链路的试错回路。
