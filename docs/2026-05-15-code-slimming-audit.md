# Code Slimming Audit - 2026-05-15

## 结论

这两天的大改造已经把最重的旧系统主体删掉了：当前 `src/codex_self_evolution/` 只剩 session recall、session reflection、配置、诊断、安装入口和少量迁移工具。真正需要继续瘦身的不是整条 compiler/reviewer 主链路，而是三类残留：

1. 旧系统留下的测试专用 helper / 兼容诊断函数。
2. 只在历史文档或未引用资产中存在的旧 reviewer/compiler/scheduler 心智。
3. 一些现在还能工作但职责变窄的维护命令，可以后续判断是保留、迁移到 admin 命令，还是删除。

建议先做 P0/P1，小步提交；P2 以后再决定。

## 执行记录

### 2026-05-15 P0 小删

已执行：

- 删除 `src/codex_self_evolution/schemas.py`。
- 删除 `diagnostics._check_hooks()` 和 `HOOK_MARKER`，同步删除只覆盖旧 `~/.codex/hooks.json` marker probe 的测试。
- 删除未引用的 `session_recall.models.RecallQuery`。
- 删除未引用的 `session_reflection.state.write_global_lock()`。

验证：

- `rg -n "SchemaError|RecallQuery|write_global_lock|_check_hooks|HOOK_MARKER" src tests` 无命中。
- `uv run pytest -q tests/test_diagnostics.py tests/test_session_recall_store.py tests/test_session_reflection_state.py` -> `34 passed`。
- `uv run pytest -q` -> `225 passed`。
- `git diff --check` 通过。

## 历史脉络

- 旧系统：`stop-review -> suggestion -> compile/scan/scheduler -> memory/recall/managed skills`。
- 5 月 14 日到 15 日的新系统：`session-start` 注入稳定背景，`session-stop` 归档 session 并按 trigger policy 决定是否创建 `session-reflect` job，reflection child 直接写 memory 和 `csep-reflect-*` skill。
- 已经执行过一次 legacy removal：源码中的 `review/`、`compiler/`、`skill_synthesis/`、`managed_skills/`、旧 scheduler 脚本和大部分旧测试已经不在 tracked tree 里。

## 当前保留主链路

- `csep session-start --from-stdin`
- `csep session-stop --from-stdin`
- `csep session-reflect --job/--status/--hook-payload`
- `csep recall`
- `csep session-archive`
- `csep session-ingest`
- `csep status`
- `csep config ...`
- `csep migrate-worktrees`（维护命令，是否继续保留待定）

## P0：可以优先删除的小残留

### 1. `src/codex_self_evolution/schemas.py`

现状：
- 只剩 `SchemaError` 一个类。
- `rg` 显示源码和测试没有任何 import。
- 历史 plan 曾说 session reflection validation 还会用它，但当前代码已经没有用。

建议：
- 删除 `src/codex_self_evolution/schemas.py`。
- 跑 `uv run pytest -q`。

风险：
- 低。主要风险是外部用户 import 这个内部类，但当前项目已明确不保留旧兼容层。

### 2. `diagnostics._check_hooks()` 和 `HOOK_MARKER`

现状：
- `collect_status()` 已经走 plugin bundle 检查 `_check_plugin_hook_bundle()`。
- `_check_hooks()` 只检查旧 `~/.codex/hooks.json` marker-protected entries。
- 生产代码不调用，只剩 `tests/test_diagnostics.py` 直接测这个私有函数。

建议：
- 删除 `HOOK_MARKER` 和 `_check_hooks()`。
- 删除/改写 `tests/test_diagnostics.py` 中旧 hooks.json marker 测试。

风险：
- 低。当前安装路径是 Codex plugin cache，不再是旧 hooks.json marker 管理。

### 3. `session_recall.models.RecallQuery`

现状：
- `RecallQuery` 没有任何引用。
- 当前 `csep recall` 直接用函数参数调用 `build_focused_recall()`。

建议：
- 删除 `RecallQuery` dataclass。

风险：
- 低。

### 4. `session_reflection.state.write_global_lock()`

现状：
- 这是“for tests”的 helper，但当前测试也没有引用。

建议：
- 删除。

风险：
- 低。

## P1：删之前要同步测试/语义的残留

### 1. `session_reflection.state.find_existing_parent_job()`

现状：
- 只被 `tests/test_session_reflection_state.py` 覆盖，没有生产调用。
- 语义是“找某 parent 最新 job”，但现在 status 走 latest/global state，不需要这个 helper。

建议：
- 删除函数和对应测试。

风险：
- 中低。确认后续诊断不打算按 parent_session_id 查询历史 job 再删。

### 2. `session_reflection.app_server.StdioAppServerTransport`

现状：
- 生产默认走 `AutoAppServerTransport`。
- `AutoAppServerTransport` 只会选择：
  - 已存在 control socket -> `UnixWebSocketAppServerTransport`
  - 无 control socket 但有 codex binary -> `ManagedAppServerTransport`
- `StdioAppServerTransport` 只在测试里使用，代表旧的 `codex app-server proxy` stdio 边界。

建议：
- 如果确认不再支持 stdio proxy fallback，删除 `StdioAppServerTransport` 和对应测试。
- 保留 `_result_from_stdout()` / `_matching_json_response()` 之前要确认它们是否只服务 stdio transport；如果只服务它，也一起删除。

风险：
- 中。它不是当前生产路径，但可能是调试 fallback。建议单独一批提交，跑 session reflection app-server 测试。

### 3. `review_memory` / `review_skills` 字段

现状：
- 最新语义已经是 review both。
- 但 job/decision 里仍保留 `review_memory=true`、`review_skills=true`，trigger reset 也仍读取这两个字段。

建议：
- 不在第一轮删。
- 后续如要删，需要改 job schema、trigger decision、counter reset 和旧 job 兼容读取。

风险：
- 中高。它们现在虽然语义变窄，但仍是状态文件 schema 的一部分。

## P2：命令面和维护工具取舍

### `migrate-worktrees`

现状：
- 它不是旧 reviewer/compiler 主链路，但属于一次性维护工具。
- 牵连 `config.py` 里的 bucket archive / canonical cwd marker / unmangle helper。

建议：
- 暂时保留。
- 如果想进一步缩小 CLI，可把它移动成更明确的 admin/maintenance 命令，或者删掉并保留一份历史迁移脚本。

风险：
- 中。删除后会影响已有多 worktree state 合并场景。

## P3：文档和资产瘦身

### 1. `README_zh.md`

现状：
- 仍完整描述旧 reviewer/compiler/scheduler/suggestion/managed-skill 系统。
- 包含 `stop-review`、`compile-preflight`、`compile`、`scan`、`install-scheduler.sh` 等已移除入口。

建议：
- 要么重写成当前系统中文版。
- 要么删除，避免用户读到完全错误的主路径。

风险：
- 低到中。主要是文档可读性，不影响运行。

### 2. `docs/status.md`

现状：
- 仍记录旧 launchd scheduler、stop-review、suggestions、compiler receipt 等旧运行状态。

建议：
- 改成当前 `0.7.11` / session reflection / session recall 的 status。
- 或移动到历史状态归档，当前入口文档不要再引用。

风险：
- 低。

### 3. `docs/assets/readme-*.png/svg`

现状：
- `rg` 没发现 README/docs 正文引用这些图片。
- 多张图仍描述 reviewer/compiler/suggestion/managed skills。
- 目录体积约 5.4M。

建议：
- 如果 README 新版不再使用，删除旧 assets。
- 如还需要图，重画一张当前 session-start/session-stop/session-reflect/session-recall 架构图。

风险：
- 低。删除前用 `rg "readme-runtime-architecture|readme-compiler-promotion|readme-skill-projection"` 再确认引用。

### 4. 历史设计文档

现状：
- `docs/` 里大量 4 月 reviewer/compiler/skill synthesis 设计文档仍在。

建议：
- 不建议第一轮直接删光。它们是历史记录。
- 更稳妥做法：新增 `docs/archive/legacy-system/`，把明显旧系统文档移进去，并在当前 README 只保留“历史设计归档”一行。

风险：
- 中。删除历史上下文会让后续追溯困难。

## 建议执行顺序

1. P0 小删：
   - `schemas.py`
   - `diagnostics._check_hooks()` / `HOOK_MARKER`
   - `RecallQuery`
   - `write_global_lock()`
   - 对应测试清理
   - 验证：`uv run pytest -q`

2. P1 单独删 app-server stdio fallback：
   - 删除 `StdioAppServerTransport`
   - 删除只覆盖它的 tests
   - 验证：`uv run pytest -q tests/test_session_reflection_app_server.py tests/test_session_reflection_runner.py`

3. P3 文档资产清理：
   - 重写或删除 `README_zh.md`
   - 更新 `docs/status.md`
   - 删除未引用的旧架构图 assets
   - 验证：`rg` 确认当前入口文档不再出现旧命令

4. P2 最后再决定：
   - 是否保留 `migrate-worktrees`
   - 是否移除 `review_memory` / `review_skills` 状态字段

## 当前不建议直接动的内容

- `session_reflection.trigger` 的 counter 体系：虽然 review scope 已经变成 both，但 trigger reasons 仍需要 memory/tool 两类计数。
- `session_reflection.validation`：这是 child 写入边界的硬保护，不适合为了减行数合并掉。
- `session_recall` SQLite/FTS：这是新 recall 主路径，不是旧 recall。
- `skill_paths.py`：虽然很小，但当前 reflection worker 仍用它定位 `~/.codex/skills`。
