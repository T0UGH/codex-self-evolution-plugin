# Legacy Review / Compile / Skill Synthesis / Recall 移除设计

日期：2026-05-15

## 状态

已确认的清理设计，用于后续进入实现计划。

本设计独立于 `2026-05-15-session-reflection-trigger-policy-design.md`。trigger policy 只回答“什么时候触发新的 session reflection”；本文回答“旧 reviewer、旧 compile/scan、旧 skill synthesis、旧 recall 和它们暴露出来的命令面如何退出”。

## 背景

CSEP 现在同时存在两套系统：

- 旧系统：Stop reviewer 生成 pending suggestions，再由 `compile` / `scan` / scheduler 晋升为 memory、`recall/index.json` 或 managed skill；独立 `skill-synthesize` 定时任务再从历史资产里合成 `csep-synth-*` skills。
- 新系统：Stop hook 归档 session，deterministic trigger policy 决定是否 fork 当前 session，session reflection worker 直接生成 session 级 memory 和 `csep-reflect-*` skills。
- 新 recall：Stop hook / backfill 把 Codex transcript 归档到 `session_recall` SQLite/FTS，`csep recall` 按 repo 或显式 global scope 检索 session 窗口。

继续保留双轨会造成几个问题：

- 命令面混乱：`stop-review --from-stdin` 实际已经偏向新 worker，但名字仍指向旧 reviewer。
- 调度面混乱：两个 launchd 定时任务仍会暗示系统需要后台轮询。
- 数据面混乱：pending suggestions、`recall/index.json`、managed skills、`csep-synth-*`、`csep-reflect-*` 和 `session_recall` SQLite 同时存在，难以判断事实来源。
- review 面混乱：旧 reviewer provider / prompt / snapshot 还在，容易被误用成新链路 fallback。

用户已确认：这个系统只给当前用户自己使用，不需要保留兼容层、不需要 tombstone 命令、不需要旧链路 fallback。

## 目标

- 删除旧 Stop reviewer 主链路和手动调试入口。
- 删除 pending suggestions 到 compiler 的整条链路。
- 删除旧 compiler recall：`recall/index.json`、`RecallRecord`、`search_recall()` 和所有 fallback。
- 删除 `scan` / `compile` / `compile-preflight` 命令。
- 删除 `skill-synthesize` 命令、`skill_synthesis` 配置和 `csep-synth-*` 生成链路。
- 删除两个 launchd 定时任务安装脚本。
- 更新插件 manifest / hook 配置，让 Stop hook 使用新的 session stop 入口，而不是 `stop-review`。
- 更新当前用户会读到的 README、getting-started、status 文档和架构图，避免继续引导旧系统。
- 保留 session reflection、session recall、`csep recall` 手动查询和 trigger policy。

## 非目标

- 不做旧命令 tombstone。
- 不提供旧命令到新命令的 alias。
- 不迁移 pending suggestions。
- 不迁移旧 `recall/index.json`。
- 不把 `csep-synth-*` skill 自动改写成 `csep-reflect-*` skill。
- 不在安装或运行时自动删除用户 home 下的历史数据文件。
- 不要求一次性清空所有历史设计文档；历史 docs 可以作为项目记录存在，但当前入口文档不能继续推荐旧系统。

## 目标架构

清理后的主链路：

```text
Codex SessionStart
  -> codex-self-evolution session-start --from-stdin
  -> 注入稳定背景 / recall context

Codex Stop
  -> codex-self-evolution session-stop --from-stdin
  -> recursion guard
  -> session archive
  -> trigger policy
  -> archive_only: 快速返回
  -> queued: 创建 session reflection job 并 spawn worker

Session Reflection Worker
  -> fork 当前 session
  -> child Codex 评估 memory / skill
  -> 直接写 memory asset 和 csep-reflect-* skill
  -> parent 校验 receipt 和产物

csep recall
  -> 读取 session_recall SQLite/FTS
  -> 默认 repo scope 检索
  -> 显式 --global 才跨 repo
  -> 返回有预算控制的 session 消息窗口
  -> 无命中时返回 no_match，不 fallback 到旧 recall/index.json
```

新的命令面只保留：

- `session-start`
- `session-stop`
- `session-reflect`
- `session-reflect-status` 或等价 status 子视图
- `csep recall`
- `csep session-archive`
- `csep session-ingest`
- `status`
- `config`
- 仍有明确用途的迁移命令，但必须删除 pending suggestions 相关行为

`stop-review` 不再存在。Stop hook 是 Codex 生命周期事件，命令名不应该继续叫 review。

## 删除范围

### CLI

删除这些 subcommands：

- `stop-review`
- `compile`
- `compile-preflight`
- `scan`
- `skill-synthesize`
- `eval-compiler`
- `recall`
- `recall-trigger`

新增或保留这些入口：

- 新增 `session-stop --from-stdin`，承接当前 `stop-review --from-stdin` 的新系统职责。
- 保留 `session-reflect --job` / `session-reflect --status`，作为后台 worker 和观测入口。
- 保留短命令 `csep recall` / `csep session-archive` / `csep session-ingest`，但它们只能走 `session_recall`。
- 保留 `status`，但删除 legacy scheduler、reviewer、compiler、skill synthesis 观测字段。

`session-stop` 的职责只包括 Stop payload 解析、recursion guard、archive spawn、trigger policy 和 enqueue reflection。它不能调用旧 reviewer，也不能写 pending suggestions。

`codex-self-evolution recall` 和 `recall-trigger` 不再保留。模型按 SessionStart 注入的 recall contract 自己决定是否调用 `csep recall`；系统不再维护另一套 wrapper / trigger command。

### Plugin Manifest 和 Hooks

同步更新两份插件包：

- `plugins/codex-self-evolution/.codex-plugin/`
- `src/codex_self_evolution/plugin_bundle/.codex-plugin/`

需要调整：

- Stop hook command 从 `codex-self-evolution stop-review --from-stdin` 改成 `codex-self-evolution session-stop --from-stdin`。
- command 列表删除 legacy commands。
- scheduler 配置块删除 `scan_command`、`compile_preflight_command`、`compile_command`、`skill_synthesis_command`。
- plugin 描述删除 background reviewer、scheduled compiler、independent skill synthesis。

### Python 包

优先物理删除这些旧包或模块：

- `codex_self_evolution.review`
- `codex_self_evolution.compiler`
- `codex_self_evolution.skill_synthesis`
- `codex_self_evolution.hooks.stop_review`
- `codex_self_evolution.hooks.codex_bridge`
- `codex_self_evolution.recall.search`

如果某个通用 helper 被新系统复用，应先移动到新系统所属模块，再删除旧包，避免为了复用而保留旧命名空间。

`codex_self_evolution.recall.workflow` 需要收口：

- 删除对 `search_recall()` 的 fallback。
- 删除 `evaluate_session_recall()` / `recall-trigger` 专用逻辑。
- 如果 `build_focused_recall()` 仍被 `csep recall` 使用，应迁移到 `session_recall.workflow`，让 `recall/` 目录只保留 SessionStart 注入用的 policy 文档，或直接把 policy 文档迁到 `session_recall/`。

`managed_skills` 需要重新审计：

- 如果只服务旧 compiler manifest 和 `csep-synth-*`，删除。
- 如果新 session reflection 只需要 `codex_skills_dir` 这类路径 helper，把 helper 移到 `session_reflection` 或 `config`，不要保留 `managed_skills` 包名。

`schemas.py` 需要删除只服务旧系统的数据结构：

- `ReviewerOutput`
- `SuggestionEnvelope`
- `CompilerReceipt`
- `SkillManifestEntry`
- `RecallRecord`

新 session recall 已有自己的 `session_recall.models` / SQLite schema，不复用旧 `RecallRecord`。

### Config

删除 config schema、TOML template、validation 和 `config show` 输出中的旧段落：

- `[reviewer]`
- `[compile]`
- `[scheduler]`
- `[skill_synthesis]`
- legacy reviewer / compile env override 逻辑

保留：

- `[session_reflection]`
- `[session_reflection.trigger]`
- `[session_recall]`
- 仍被新系统使用的 profiles / provider 配置

`replace_stop_reviewer` 这类过渡字段必须删除。新系统不是替换旧 reviewer 的开关，而是唯一 Stop 主链路。

### Scheduler Scripts

删除这些脚本和对应测试：

- `scripts/install-scheduler.sh`
- `scripts/uninstall-scheduler.sh`
- `scripts/install-skill-synthesis-scheduler.sh`
- `scripts/uninstall-skill-synthesis-scheduler.sh`

`scripts/install.sh` 只负责安装 CLI 和刷新插件缓存，不安装任何后台轮询任务。

### Diagnostics / Status

`status` 不再报告：

- launchd scheduler loaded / plist exists
- pending / processing / failed suggestions
- compile receipt
- skill synthesis receipt
- `csep-synth-*` inventory
- reviewer provider health

`status` 只报告新系统需要的内容：

- plugin hooks 当前是否指向 `session-start` / `session-stop`
- session recall archive / SQLite 状态
- session reflection job / run / trigger 状态
- `csep-reflect-*` skill 校验摘要
- 必要的 provider / app-server 配置可用性，且不暴露 secret value

### Runtime Data

代码不再读写这些旧目录：

- project bucket 下的 `pending/`、`processing/`、`done/`、`failed/`
- project bucket 下的 `recall/index.json` 和 `recall/compiled.md`
- compiler receipts
- review snapshots
- skill synthesis receipts / evidence
- managed skill manifest

历史数据不在本次自动删除。它们会变成孤立历史文件，不再参与 runtime。需要物理清理时，可以另做本机一次性清理脚本。

### Tests

删除旧系统测试，而不是改成 tombstone 断言：

- reviewer provider / prompt / snapshot 测试
- codex bridge 映射测试
- compiler backend / compile / scan 测试
- scheduler integration 测试
- skill synthesis agent / runner / scheduler / validation 测试
- pending suggestion storage state machine 测试
- compiler recall / old `search_recall()` / `recall-trigger` 测试

新增或调整新系统测试：

- `session-stop --from-stdin` 快速返回 `{"continue": true}`。
- Stop hook manifest 指向 `session-stop --from-stdin`。
- `session-stop` 不产生 pending suggestions。
- `session-stop` 命中 trigger 时只创建 session reflection job。
- `status` 不含 legacy scheduler / skill synthesis 字段。
- `config show` 不含 legacy config sections。
- `csep recall` 只查询 `session_recall` SQLite/FTS；数据库不存在或无命中时返回 `no_match`。
- `codex-self-evolution recall` 和 `recall-trigger` 不再是合法命令。

## 实施顺序

1. 先改命令面和 plugin manifest。
   目标是让当前入口明确只暴露新系统。

2. 再删除旧 Python 包和旧 imports。
   每删一个包后用 `rg` 确认没有活跃 import 残留。

3. 再收 config / diagnostics / docs。
   目标是当前用户文档和 status 输出不再推荐旧系统。

4. 最后清测试。
   删除旧测试，补齐新 `session-stop`、manifest、status、config 测试。

## 验收标准

代码层面：

- `codex-self-evolution stop-review`、`compile`、`compile-preflight`、`scan`、`skill-synthesize`、`eval-compiler`、`recall`、`recall-trigger` 不再是合法命令。
- 插件 Stop hook 只调用 `codex-self-evolution session-stop --from-stdin`。
- `src/codex_self_evolution/review/`、`compiler/`、`skill_synthesis/` 不再存在。
- 新 Stop 流程不会写 `SuggestionEnvelope` 或 pending suggestion 文件。
- `csep recall` 不再读取 `recall/index.json`。
- `config show` 不再输出 `[reviewer]`、`[compile]`、`[scheduler]`、`[skill_synthesis]`。
- `status` 不再输出 legacy scheduler / skill synthesis / compile receipt 字段。

文档层面：

- README 和 getting-started 不再出现安装旧 scheduler、运行 `skill-synthesize`、运行 `compile-preflight`、运行旧 `codex-self-evolution recall` / `recall-trigger` 的当前使用说明。
- 当前架构图只展示 memory、session reflection skill、session recall 三条新线。
- 历史 docs 可以保留，但不能作为当前入口文档被引用。

验证命令：

```bash
uv run pytest -q
rg -n "stop-review|compile-preflight|skill-synthesize|install-scheduler|install-skill-synthesis|SuggestionEnvelope|pending suggestions|recall/index.json|search_recall|recall-trigger|RecallRecord" README.md pyproject.toml scripts src tests plugins
```

第二个命令只扫描当前系统面，不扫描历史 docs；在 README、pyproject、scripts、src、tests、plugins 中不应再有 legacy 命中。

## 风险和处理

### Stop Hook 命令重命名风险

旧插件缓存可能仍指向 `stop-review --from-stdin`。因为不保留 tombstone，旧缓存会直接失败。

处理方式：

- `scripts/install.sh` 必须刷新插件缓存。
- README 的安装步骤必须明确重新运行安装脚本。
- 不在 CLI 中保留兼容命令。

### 历史本地数据风险

旧 pending / synthesis / compiler recall 数据不再被读取。这里可能导致历史 `csep-synth-*` skill 仍留在 `~/.codex/skills`，旧 `recall/index.json` 仍留在 project bucket 下。

处理方式：

- runtime 不主动删除用户 home 文件。
- status 可以只报告 `csep-reflect-*`，不再把 `csep-synth-*` 当作系统资产。
- `csep recall` 只报告 `session_recall`，不再把旧 `recall/index.json` 当作 fallback。
- 如需清理 `csep-synth-*` 或旧 project bucket recall 文件，另写一次性本机清理脚本，由用户显式运行。

### 文档历史噪音

历史 docs 中会继续出现旧命令名。

处理方式：

- 当前入口文档必须清理。
- 历史 docs 不作为系统功能保留，不要求本次物理删除。

## 最终判断

这次不是“禁用旧系统”，而是“删除旧系统”。

新系统的唯一事实来源是 session archive、trigger policy、session reflection receipt、memory asset、`csep-reflect-*` skill 和 `session_recall` SQLite/FTS。旧 reviewer、pending suggestions、compiler、compiler recall、scan scheduler、`skill-synthesize` scheduler 都不再参与运行时，也不作为 fallback 保留。
