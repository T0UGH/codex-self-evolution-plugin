# CSEP 明确修复计划基准文档

日期：2026-06-12

## 目标

本文把现有方案和 review 文档中已经明确提出修复计划的点收敛成一个后续执行基准。后续推进修复时，以本文的任务边界和优先级为准；原始文档作为背景和证据来源，不再直接作为执行清单。

## 来源与优先级规则

纳入本文的来源：

- `docs/review-2026-06-03-deep-repo-review.md`
- `docs/implementation-plans/2026-06-03-schema-safe-review-output-plan.md`
- `docs/native-codex-memory-lessons-2026-06-04.md`

不直接纳入本文的内容：

- `docs/review-2026-06-03-workflow-design-notes.md`：这是 review workflow 方法论复盘，不是 CSEP 产品代码修复清单。
- 已在 deep review 中明确否决的候选问题：
  - heuristic transcript discovery 会把 transcript 归到错误 session_id。
  - receipt contract 不接受 `skipped_empty` 但 runner 会发 `skipped_empty`。

执行优先级规则：

1. 真实 correctness 问题优先。
2. 已有明确 MVP 范围的方案优先。
3. 测试承诺和发布路径风险其次。
4. hygiene / DX 项可以顺手修，但不要打断 correctness 修复。
5. memory 长期治理项只作为后续路线，不和当前 bugfix 混在同一个 patch 里。

## 当前执行状态

截至 2026-06-12，Patch 1 到 Patch 4 已完成实现和回归验证；Patch 5 仍是后续路线，不属于本轮 bugfix。

发布记录：

- `csep 1.3.10` 已上传 PyPI：https://pypi.org/project/csep/1.3.10/

已验证命令：

- `uv run pytest -q`
- `git diff --check`
- `python3 scripts/sync-plugin-bundle.py --check`
- `uv run python -m build`
- `scripts/smoke-runtime.sh`，通过临时 `csep` shim 指向当前源码执行

## Patch 1：Correctness 修复

### 1. 修 mixed payload child guard 漏判

来源：`docs/review-2026-06-03-deep-repo-review.md`

问题：

- `session_reflection` 的 child guard 只使用第一个有值的标识字段。
- payload 同时包含 `session_id = parent session` 和 `thread_id = child thread` 时，会优先检查 parent session，从而漏掉真正的 child thread registry。

目标文件：

- `src/codex_self_evolution/session_reflection/guard.py`
- `tests/test_session_reflection_guard.py`

修复要求：

- 同时检查 `session_id` 和 `thread_id`。
- 当 `thread_id` 命中 child registry 时，必须跳过 archive / reflection queue。
- 补 mixed payload regression test：payload 同时带 parent `session_id` 和 child `thread_id` 时必须返回 skip。

完成标准：

- 新 regression test 先能证明旧行为会漏判。
- 修复后相关 guard 测试通过。
- 不改变 transcript marker guard 和 global lock guard 的现有语义。

### 2. 修 recall `state_dir` / config home 混用

来源：`docs/review-2026-06-03-deep-repo-review.md`

问题：

- `build_focused_recall()` 读取 DB 时尊重 `state_dir`。
- 但读取 recall enablement config 时仍走默认 home。
- 这会导致 `csep recall --state-dir <alt>` 使用 A 的 DB，却用 B 的 config 判断 enablement。

目标文件：

- `src/codex_self_evolution/session_recall/workflow.py`
- `tests/test_session_recall_cli.py`
- `tests/test_session_recall_store.py` 或新的 focused workflow 测试文件

修复要求：

- config 加载和 DB 解析必须使用同一套 `state_dir` / home 语义。
- `--state-dir` 指向的 home 内配置启用 recall 时，应能正常读取该 home 下的 DB。
- 默认 home 禁用 recall 不应影响显式 `--state-dir` 的 recall 结果。

完成标准：

- 新测试覆盖“默认 home disabled、alt state_dir enabled 且 DB 有命中”的场景。
- `csep recall --state-dir <alt>` 不再静默 `no_match`。
- 默认不传 `--state-dir` 的行为保持不变。

## Patch 2：Schema-safe Receipt Writer

### 3. 新增专用 receipt writer 命令

来源：`docs/implementation-plans/2026-06-03-schema-safe-review-output-plan.md`

问题：

- reflection child 直接手写最终 `receipt.json`，容易出现 JSON 格式、字段类型、timestamp、placeholder 等格式型失败。
- 这类失败会污染 runner / validation 状态，也让 agent 反馈回路太慢。

目标文件：

- `src/codex_self_evolution/cli.py`
- `src/codex_self_evolution/csep.py`
- `src/codex_self_evolution/session_reflection/runner.py`
- `src/codex_self_evolution/session_reflection/prompt.py`
- `src/codex_self_evolution/session_reflection/validation.py`
- `tests/test_session_reflection_cli.py`
- `tests/test_session_reflection_runner.py`

MVP 命令：

```bash
csep session-reflection write-receipt \
  --draft <path> \
  --output <path> \
  --job-id <job> \
  --parent-session-id <session> \
  --child-thread-id <thread> \
  [--started-at <iso8601>] \
  [--finished-at <iso8601>]
```

修复要求：

- CLI 读取 draft JSON。
- draft 顶层必须是 object。
- `status` 必须是合法 child receipt status。
- `memory_changes`、`skill_changes`、`skipped_candidates`、`validation_notes`、`errors` 必须是 list。
- writer 注入 canonical identity fields 和时间字段。
- writer 输出 canonical JSON，字段顺序稳定。
- writer 使用原子写入。
- 输入非法时 stderr 明确说明错误，返回非零码，不写伪成功 receipt。
- MVP 阶段先只服务 `session_reflection` receipt，不抽象成通用 schema writer。

完成标准：

- CLI 单测覆盖合法 draft、非法 status、list 字段类型错误、顶层非 object、输出文件不被伪成功覆盖。
- writer 失败信息足够短且可被 agent 直接修正。
- 不把语义错误自动猜成成功 receipt。

### 4. 改 reflection child 交互契约

来源：`docs/implementation-plans/2026-06-03-schema-safe-review-output-plan.md`

问题：

- 只新增 writer 命令但 prompt 仍要求 child 手写最终 receipt，收益有限。

目标文件：

- `src/codex_self_evolution/session_reflection/prompt.py`
- `src/codex_self_evolution/session_reflection/runner.py`
- `tests/test_session_reflection_runner.py`
- `tests/test_session_reflection_validation.py`

修复要求：

- prompt 改成要求 child 先写 draft，再调用 writer 生成最终 `receipt.json`。
- runner 仍然等待最终 receipt 并执行现有 validation。
- `validation.py` 继续做结果验收，不承担“帮模型修格式”的职责。

完成标准：

- prompt 测试能证明不再要求 child 直接手写最终 receipt。
- runner 测试覆盖 writer 产物进入原有 validation 流程。
- 原有 receipt validation 边界不被弱化。

### 5. 在 status / diagnostics 中区分 writer 相关失败

来源：`docs/implementation-plans/2026-06-03-schema-safe-review-output-plan.md`

问题：

- 当前失败容易混成一次笼统 reflection failure，无法区分 draft 不合格、writer 执行失败、最终 validation 失败。

目标文件：

- `src/codex_self_evolution/session_reflection/runner.py`
- `src/codex_self_evolution/diagnostics.py`
- `tests/test_session_reflection_runner.py`
- `tests/test_session_reflection_diagnostics.py`

修复要求：

- status / diagnostics 至少能区分：
  - `draft_invalid`
  - `writer_failed`
  - `validation_failed`
- writer 失败不应被包装成模糊的 child validation failure。

完成标准：

- 失败分类测试覆盖三种状态。
- `csep status` 输出能让用户判断失败发生在 draft、writer 还是 validation 阶段。

## Patch 3：测试与开发体验

### 6. 把 `scripts/smoke-runtime.sh` 变成真实可执行测试

来源：`docs/review-2026-06-03-deep-repo-review.md`

问题：

- 当前测试只检查脚本可执行和包含若干命令字符串，没有真正执行 runtime smoke。
- 文档把该脚本当 release gate，但 CI 没验证脚本是否真的可跑。

目标文件：

- `scripts/smoke-runtime.sh`
- `tests/test_install_script.py`
- 必要时新增 hermetic fixture/helper

修复要求：

- 在 hermetic 临时环境中实际执行 `scripts/smoke-runtime.sh`。
- 校验 exit code 和 sentinel 输出。
- 保持无网络、临时 state dir、CI 稳定。

完成标准：

- 脚本自身坏掉时测试会失败。
- smoke 覆盖 `csep status`、`session-start`、`session-archive`、`recall` 的基本链路。
- 不依赖本机真实 provider credential。

### 7. Makefile 默认 Python 路径便携化

来源：`docs/review-2026-06-03-deep-repo-review.md`

问题：

- `Makefile` 默认 `PYTHON` 指向某个开发者机器的绝对路径。

目标文件：

- `Makefile`
- 必要时更新 `README.md` 或相关开发文档

修复要求：

- 默认值改成可移植入口，例如 `python3` 或与 README 一致的 `uv run python`。
- 保持 `make test PYTHON=...` 覆盖能力。

完成标准：

- `make test` 在没有私有路径的机器上可用。
- README / Makefile 推荐入口不互相矛盾。

### 8. 清理 `MANIFEST.in` stale fixtures 规则

来源：`docs/review-2026-06-03-deep-repo-review.md`

问题：

- `MANIFEST.in` 包含 `recursive-include tests/fixtures *.json`，但仓库没有 tracked `tests/fixtures/`。

目标文件：

- `MANIFEST.in`

修复要求：

- 如果不需要 fixtures，删除该规则。
- 如果要保留 fixtures，则补真实目录和对应测试；当前默认选择删除 stale rule。

完成标准：

- 打包元数据不再引用不存在的 tracked 路径。
- `python -m build` 或 `uv run python -m build` 不受影响。

### 9. 改进 fresh machine 的 PyPI latest 展示

来源：`docs/review-2026-06-03-deep-repo-review.md`

问题：

- `csep status` 只有在本地 `csep` binary 存在时才查询 PyPI latest。
- fresh machine 缺少 binary 时，反而看不到最需要的远端最新版本信息。

目标文件：

- `src/codex_self_evolution/diagnostics.py`
- `tests/test_diagnostics.py`

修复要求：

- 远端 PyPI 查询和本地 binary 探测解耦。
- include remote 时，即使 `csep` binary 不存在，也能展示 PyPI latest 或明确 remote error。

完成标准：

- 测试覆盖 `csep` binary unavailable 但 remote latest available 的场景。
- 原有 installed/runtime/source version 比对逻辑不被破坏。

## Patch 4：Plugin Bundle 结构治理

### 10. 收敛 plugin bundle / `SKILL.md` canonical source

来源：`docs/review-2026-06-03-deep-repo-review.md`

问题：

- plugin bundle / manifest / skill 存在多份镜像。
- wheel 发布真正带走的是 `src/codex_self_evolution/plugin_bundle/...`。
- 本地安装脚本更多依赖 `plugins/...`。
- 当前主要靠 parity tests 兜底，但结构本身允许 drift。

目标文件：

- `src/codex_self_evolution/plugin_bundle/`
- `plugins/codex-self-evolution/`
- `scripts/install.sh`
- `pyproject.toml`
- `tests/test_plugin_bundle_hooks.py`
- 必要时更新 `docs/architecture.md`

修复要求：

- 明确发布路径、本地安装路径、开发镜像路径的权威关系。
- 最优方案：收敛成一个 canonical source，其他副本在 build / release / install 时生成。
- 如果短期不能物理合并，至少把权威源写入文档，并让镜像生成自动化，避免手改多份。

完成标准：

- 修改 skill / manifest 时只有一个权威编辑入口。
- tests 能证明发布 bundle 和本地 plugin bundle 不漂移。
- 文档明确说明哪个路径是 source of truth。

## Patch 5：Memory / Reflection 长期治理路线

以下条目来自 `docs/native-codex-memory-lessons-2026-06-04.md`。它们是明确建议，但不是当前 bugfix patch；实施前应单独拆计划。

### 11. 引入 `memory_summary.md` 热摘要 fallback

目标：

- 为 stable memory 增加更短的热摘要层。
- 当 `MEMORY.md` 过大或上下文预算紧张时，优先注入 `memory_summary.md`。

完成标准：

- SessionStart 有明确 fallback 策略。
- 长文档不再直接挤占所有上下文预算。

### 12. 引入 session / memory / skill 使用反馈统计

目标：

- 记录 memory / refs / skills 被引用或使用的次数与最近使用时间。
- 让后续 consolidation 能根据真实使用信号提升、降权或清理内容。

完成标准：

- 使用反馈不会泄露敏感内容。
- 统计数据能被后续 consolidation job 消费。

### 13. 增加外部上下文污染标签

目标：

- 标记来自 AGENTS、developer/system 注入、外部文档或运行时 transcript 的内容来源。
- 避免把 transient / injected context 当成用户长期偏好沉淀。

完成标准：

- reflection 写入 memory 或 refs 时能保留来源类型。
- validation 或 consolidation 能基于污染标签降权或拒绝写入。

### 14. 候选层与 consolidation job

目标：

- reflection child 不再直接把所有结论写进热 memory。
- 先写候选摘要、证据来源和建议动作，再由 consolidation job 合并到 `MEMORY.md`、`memory_summary.md` 或 refs。

完成标准：

- 单 session 的局部经验不会未经合并直接进入长期热上下文。
- consolidation 有可审计 diff。

### 15. diff 驱动 reflection prompt

目标：

- reflection prompt 基于上次稳定状态到当前候选/记忆的增量来整理，而不是每次重新扫全量。

完成标准：

- prompt 输入更小。
- agent 能看到明确的 changed context。

### 16. skill staging 与 promotion

目标：

- 新生成 skill 先进入 staging。
- 通过质量检查或使用反馈后再 promotion 到正式技能目录。

完成标准：

- 低质量 skill 不会直接污染正式 skill namespace。
- promotion 条件明确可审计。

### 17. 更完整的 memory 生命周期管理

目标：

- 支持自动遗忘、降权和冲突检测。
- 增强 receipt 与 artifact 审计。

完成标准：

- memory 不只会增长，也能基于证据过期、冲突或低使用率被降权。
- receipt / artifact 审计能解释某条记忆为何保留或移除。

## 推荐执行顺序

1. Patch 1：先修两个静默 correctness 问题。
2. Patch 2：做 schema-safe receipt writer，收敛 reflection 格式型失败。
3. Patch 3：补真实 smoke gate 和低风险 hygiene。
4. Patch 4：做 plugin bundle canonical source 结构治理。
5. Patch 5：另开长期 memory governance 计划，不和当前 bugfix 混做。

## 每轮通用验证

每个 patch 完成后至少执行：

```bash
uv run pytest -q
git diff --check
git status --short
```

涉及打包或 plugin bundle 的 patch 还要执行：

```bash
uv run python -m build
```

涉及 runtime smoke 的 patch 还要执行：

```bash
scripts/smoke-runtime.sh
```

## 当前不做的事

- 不基于 `docs/review-2026-06-03-workflow-design-notes.md` 改 review workflow，除非后续明确要做 review workflow 产品化。
- 不把 schema-safe writer 扩展成通用 schema 框架。
- 不在同一个 patch 里同时做 trigger 策略、memory governance 和 correctness bugfix。
- 不重新打开 deep review 中已经否决的两个候选问题。
