# CSEP 仓库深度 Review 记录（2026-06-03）

## 背景

本次 review 面向整个仓库 `codex-self-evolution-plugin`，不是单次 diff review。
目标是从架构、正确性、可维护性、测试与潜在风险几个维度，梳理当前主链路质量和后续优先修复项。

审查基线：

- 仓库：`/Users/bytedance/code/github/codex-self-evolution-plugin`
- 分支：`main`
- 审查时忽略未跟踪本地产物：`.codex/`、`tmp/`
- 采用 workflow 多 agent 交叉审查，再对候选问题做独立复核

## 总体结论

这个仓库整体质量偏高。

最难的两条链路，`session_recall` 和 `session_reflection`，都已经有比较成熟的结构和测试覆盖。问题的重心不在“代码很乱”或“到处都有明显 bug”，而在几个跨模块边界：

1. source of truth 不够单一
2. 少数跨模块标识与状态边界存在真实 correctness 问题
3. 文档、脚本、打包、安装路径之间存在维护漂移面

一句话判断：核心设计靠谱，但长期可靠性现在更依赖把边界收紧，而不是继续加功能。

## 主要优点

### 1. 运行时架构分层基本成立

当前主链路已经比较清晰地拆成了几块：

- hooks
- config / state resolution
- session_recall
- session_reflection
- diagnostics

这些模块的职责边界总体清楚，不是高度纠缠在一起。

### 2. 最危险的 trust boundary 有多层防护

`session_reflection` 不是直接信任 child 的口头输出，而是由父进程做二次校验：

- child thread registry
- recursion / lock guard
- receipt 校验
- durable output 路径边界校验
- hash / identity 校验
- skill namespace 限制

这部分设计是靠谱的，也是仓库最有工程味的部分之一。

### 3. 测试覆盖和真实风险点比较对齐

这套测试不是只测小函数。

尤其以下方面覆盖较强：

- `session_recall` 的 archive / parser / store / query / recent / backfill
- `session_reflection` 的 trigger / runner / validation / guard / state
- CLI / config / diagnostics / plugin bundle 对齐

对这种插件型、本地 runtime 型项目来说，这是很大的加分项。

### 4. 运行时策略务实

- 前台 hook 尽量轻
- 重活放后台
- diagnostics 只读
- worktree / repo scope 被当成一等问题处理

这说明项目是在朝“长期实际运行”而不是“演示可用”方向设计。

## 已确认问题

以下问题经过交叉审查和二次复核，属于值得排期处理的真实问题。

---

### 1. Published plugin assets 没有单一 canonical source

- 严重度：高
- 类别：packaging
- 关键位置：`docs/architecture.md:133`
- 关联文件：`pyproject.toml`、`AGENTS.md`、`scripts/install.sh`、`tests/test_plugin_bundle_hooks.py`

#### 问题

插件 bundle / manifest / skill 当前存在多份镜像，但真正打进 wheel 的只有 `src/codex_self_evolution/plugin_bundle/...` 那一份；本地安装路径又更多依赖 `plugins/...`。

也就是说：

- contributors 在仓库里看到的一份
- 本地安装脚本消费的一份
- 打包发布真正带走的一份

不是天然同一个 source of truth。

#### 影响

短期看，现有 parity tests 能在一定程度上兜底。

但长期风险很明显：

- 修了本地安装用的 bundle，发布包没带上
- 改了 `src` 的打包资产，但开发者还在盯另一份镜像
- 文档和真实发布行为可能逐步漂移

这属于典型“结构上允许漂移，测试只是事后报警”的问题。

#### 建议

- 最优：收敛成一个 canonical source
- 其他副本在 build / release 时生成
- 如果短期做不到，至少明确记录哪份是发布真相，哪份只是开发镜像

---

### 2. reflection child guard 对 mixed payload 会漏判

- 严重度：高
- 类别：correctness
- 关键位置：`src/codex_self_evolution/session_reflection/guard.py:31`

#### 问题

当前 guard 只检查第一个有值的标识字段。

当 payload 同时带：

- `session_id = parent session`
- `thread_id = child thread`

时，guard 会优先使用 `session_id`，从而错过真正登记在 child-thread registry 里的 `thread_id`。

#### 影响

这会导致 reflection child 的 Stop 事件被误当成普通 session：

- 被错误归档到 recall
- 产生重复 session 记录
- 在极端情况下把 child 链路重新卷回主链路，增加递归风险和运行噪音

#### 建议

- guard 同时检查 `session_id` 和 `thread_id`
- 优先基于真正的 child thread identifier 判定
- 补一个 mixed payload regression test

这是本次 review 里最值得优先修复的问题之一。

---

### 3. recall 在 `state_dir` override 时混用了两套 home

- 严重度：中
- 类别：correctness
- 关键位置：`src/codex_self_evolution/session_recall/workflow.py:32`

#### 问题

`build_focused_recall()` 在读数据库时会尊重 `state_dir`，但读 config enablement 时仍然走默认 home。

结果是：

- DB 来自 override 的 state dir
- recall enablement 却来自默认 home 的配置

#### 影响

`csep recall --state-dir <alt>` 可能出现静默错误：

- 目标 DB 里有数据
- 但默认 home 配置把 recall 判成 disabled
- 命令直接返回 `no_match`

这会让 CLI 行为和实际指定的 storage root 不一致。

#### 建议

- config 加载也要走同一个 state/home 语义
- 不要一边读 A 的 DB，一边拿 B 的 config 做 enablement 决策

---

### 4. `scripts/smoke-runtime.sh` 被当 release gate，但测试只做了文本检查

- 严重度：中
- 类别：testing gap
- 关键位置：`tests/test_install_script.py:173`

#### 问题

文档把 `scripts/smoke-runtime.sh` 描述成一个真实的 runtime / release smoke gate。

但当前测试实际只做了：

- 检查脚本可执行
- 检查脚本里包含若干命令字符串

没有真正执行脚本。

#### 影响

脚本自己如果坏掉，CI 仍然可能全绿。

而这个脚本覆盖的是关键链路：

- `csep status`
- `csep session-start`
- `csep session-archive`
- `csep recall`
- `csep recall sync-claude`

也就是说，仓库对外宣称有 smoke gate，但目前并没有把这条承诺编码成真实行为测试。

#### 建议

- 在 hermetic 临时环境中实际执行 `scripts/smoke-runtime.sh`
- 至少校验 exit code 和 sentinel 输出
- 保持无网络、临时 state dir、可在 CI 稳定运行

---

### 5. Makefile 硬编码了某位开发者机器的 Python 路径

- 严重度：中
- 类别：repository hygiene
- 关键位置：`Makefile:1`

#### 问题

当前 `Makefile` 使用了机器私有路径：

```make
PYTHON ?= /Users/haha/hermes-agent/venv/bin/python3.11
```

这会让 `make test` 在其他开发者机器上默认不可用。

#### 影响

- `make test` 看起来像官方入口，但实际上绑定某个本地环境
- 与 README 里 `.venv` / `uv run pytest -q` 的说明脱节
- 增加了本地开发体验的不确定性

#### 建议

改成可移植默认值，例如：

- `python3`
- `uv run python`

并与 README 保持一致。

---

### 6. fresh machine 上 `csep status` 会隐藏 PyPI 最新版本信息

- 严重度：低
- 类别：correctness
- 关键位置：`src/codex_self_evolution/diagnostics.py:378`

#### 问题

当前逻辑只有在本地 `csep` binary 已存在时，才去拉远端 PyPI latest version。

但“最需要知道最新版本”的场景，往往正是机器上还没装的时候。

#### 影响

- fresh machine 的 install / upgrade 诊断信息不完整
- `csep status` 在缺失本地 binary 时反而少给了关键上下文

#### 建议

把远端 PyPI 查询和本地 binary 探测解耦。

---

### 7. `SKILL.md` 三份镜像增加维护负担

- 严重度：中
- 类别：duplication
- 关键位置：`tests/test_plugin_bundle_hooks.py:61`

#### 问题

同一个 `SKILL.md` 现在在三处 tracked 路径中存在镜像副本。

虽然测试能检查 byte-identical，但这本质上还是人工同步负担。

#### 影响

- 文案更新需要同步改多处
- review 噪音增加
- drift 风险始终存在，只是由测试延后暴露

#### 建议

与 plugin bundle 的 canonical source 问题一起处理，尽量收敛到一个权威源。

---

### 8. `MANIFEST.in` 指向了不存在的 `tests/fixtures`

- 严重度：低
- 类别：repository hygiene
- 关键位置：`MANIFEST.in:1`

#### 问题

`MANIFEST.in` 当前包含：

```text
recursive-include tests/fixtures *.json
```

但仓库并没有 tracked 的 `tests/fixtures/` 目录。

#### 影响

这是低危问题，但说明打包元数据里有遗留配置。

这类残留不会立刻导致线上故障，但会降低可审计性，也让后续打包规则更难判断哪些还在生效。

#### 建议

- 如果不再需要，删除这条规则
- 如果未来确实要保留 fixtures，就补回真实目录和对应测试

## 被复核后否决的候选问题

这次 review 不是把所有可疑点都上报。以下候选问题经过复核后被否决：

### 1. “heuristic transcript discovery 会把 transcript 归到错误 session_id”

最终否决原因：

- parser 会优先使用 transcript 内部的 `session_meta.id`
- 相关针对性测试已经覆盖并通过

### 2. “receipt contract 不接受 skipped_empty，但 runner 会发 skipped_empty”

最终否决原因：

- `skipped_empty` 是父进程派生出来的 validation status
- 不是 child receipt contract 的合法输入值
- 这是分层语义，不是 contract 冲突

这两项被否决，说明当前 review 结果已经过一轮去误报处理。

## 建议修复优先级

### 第一优先级

1. 修 `session_reflection/guard.py` 的 mixed payload 漏判
2. 修 `session_recall/workflow.py` 的 state_dir / config root 混用

这是两项真实 correctness 问题，且都是静默错，优先级最高。

### 第二优先级

3. 收敛 plugin bundle / skill 的 canonical source

这更像中期结构治理项，不一定今天炸，但它是后续发布和维护风险的根源。

### 第三优先级

4. 把 `scripts/smoke-runtime.sh` 变成真实可执行测试

这是把“文档承诺”变成“CI 能验证”的高价值动作。

### 第四优先级

5. 清理 Makefile 绝对路径
6. 清理 `MANIFEST.in` 遗留项
7. 改进 diagnostics 中 PyPI latest version 的展示条件

这些属于 hygiene / DX 改善，适合顺手修。

## 建议的后续动作

如果要继续推进，建议拆成 3 组 patch：

### Patch 1：correctness 修复

- 修 mixed payload guard
- 修 recall state_dir / config root 一致性
- 补针对性 regression tests

### Patch 2：测试与开发体验

- 执行版 smoke-runtime test
- Makefile 便携化
- 清理 stale manifest

### Patch 3：结构治理

- plugin bundle / SKILL.md canonical source 收敛
- 明确发布路径、本地安装路径、开发镜像路径的权威关系
- 若短期不能合并物理副本，至少自动生成镜像而不是手改多份

## 总结

这个仓库最值得肯定的地方，不是“功能多”，而是最难的链路已经有了比较成熟的工程骨架和测试保护。

现在最该做的，不是大重构，而是把少数跨边界问题收紧：

- identifier 边界
- config / storage root 边界
- plugin source-of-truth 边界
- 文档承诺与真实 smoke gate 边界

把这些收紧之后，这个仓库的长期可维护性会明显更稳。
