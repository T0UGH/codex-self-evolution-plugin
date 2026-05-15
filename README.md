# Codex Self-Evolution Plugin

[![tests](https://github.com/T0UGH/codex-self-evolution-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/T0UGH/codex-self-evolution-plugin/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Codex 本地自我进化插件 | Session Reflection / Session Recall / Reflection Skills | 面向重度 Codex 工作流

## 概述

Codex Self-Evolution Plugin 是一个本地优先的 Codex 自我进化层。它把一次会话里的稳定经验沉淀为下一次会话可读取的上下文，重点保留三条线：

| 线 | 产生方式 | 消费方式 |
| --- | --- | --- |
| Memory | Stop hook 触发 session reflection，child thread 按规则写入 `USER.md` / `MEMORY.md` | 下一次 `SessionStart` 注入 stable background |
| Skill | 同一轮 session reflection 可写入 `~/.codex/skills/csep-reflect-*` | Codex 原生 skills loader 自动加载 |
| Session Recall | Stop hook 归档 transcript 到本机 SQLite/FTS | `csep recall` / `csep recall --recent` 按 repo 或全局召回 |

核心闭环：

```text
SessionStart 注入 memory + recall 使用说明
  -> Codex 正常工作
  -> Stop 归档 transcript
  -> session 级触发规则判断是否需要 reflection
  -> 后台通过 Codex app-server fork 当前 thread
  -> child 写 memory / csep-reflect-* skill / receipt
  -> parent 校验 receipt 和写入边界
  -> 下一次 Codex 会话读取更新后的上下文
```

## 安装

前置依赖：

```bash
brew install uv
```

安装本地 CLI，并刷新 Codex plugin cache：

```bash
git clone https://github.com/T0UGH/codex-self-evolution-plugin.git
cd codex-self-evolution-plugin

mkdir -p ~/.codex-self-evolution
cp .env.provider.example ~/.codex-self-evolution/.env.provider

scripts/install.sh
```

`scripts/install.sh` 会做这些事：

- 用 `uv tool install --force <当前仓库>` 安装主命令 `csep` 和兼容命令 `codex-self-evolution`
- 刷新 `~/.codex/plugins/cache/codex-self-evolution/...`
- 清理旧版 marker-managed `~/.codex/hooks.json` 注入项
- 不再向 `~/.codex/hooks.json` 写入新 hook

## 启用 Codex Plugin Hooks

如果你的 Codex CLI 已支持 `plugin_hooks`，在 `~/.codex/config.toml` 中启用：

```toml
[features]
plugins = true
hooks = true
plugin_hooks = true

[plugins."codex-self-evolution@codex-self-evolution"]
enabled = true
```

插件 hook 定义位于：

```text
plugins/codex-self-evolution/.codex-plugin/plugin.json
plugins/codex-self-evolution/.codex-plugin/hooks.json
```

启用后，Codex 生命周期会调用：

```text
SessionStart -> csep session-start --from-stdin
Stop         -> csep session-stop --from-stdin
```

## 快速检查

查看只读状态：

```bash
csep status | python3 -m json.tool
```

重点看：

| 字段 | 期望 |
| --- | --- |
| `plugin_hooks.manifest_exists` | `true` |
| `plugin_hooks.hooks_file_exists` | `true` |
| `plugin_hooks.session_start_declared` | `true` |
| `plugin_hooks.stop_declared` | `true` |
| `plugin_hooks.uses_local_cli` | `true` |
| `session_reflection.latest.status` | 有任务时为 `succeeded` / `failed` / `skipped` |
| `session_recall.enabled` | `true` |

查看或初始化配置：

```bash
csep config path
csep config init
csep config show | python3 -m json.tool
csep config validate
```

默认配置只包含当前系统需要的段：

```toml
[session_reflection]
enabled = true
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
skill_prefix = "csep-reflect-"

[session_reflection.trigger]
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
skill_generation_mode = "one_shot_active"

[session_recall]
enabled = true
stop_hook_archive = true
```

## Session Reflection

`session-stop --from-stdin` 会做三件事：

1. 归档当前 Codex transcript，供 session recall 使用。
2. 按当前 session 的本地计数器和高信号关键词判断是否需要 reflection。
3. 如果需要，创建后台 job，并快速返回 `{"continue": true}`。

触发规则是 session 级别，不是全局计数。默认策略：

| 规则 | 默认 |
| --- | --- |
| memory nudge | 每 3 次 Stop 或上下文增量达到 `memory_context_chars` |
| skill nudge | 每 15 次工具调用 |
| high-signal | 命中 correction / handoff / memory / skill 等关键词可立即触发 |
| active job guard | 同一父 session 有活跃 job 时只归档，不递归 fork |

查看 reflection 状态：

```bash
csep session-reflect --status | python3 -m json.tool
```

常见排查文件：

```text
~/.codex-self-evolution/session_reflection/latest.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/receipt.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/validation.json
/tmp/codex-self-evolution/session-reflect-*.log
```

## Session Recall

Stop hook 会把 session transcript 归档到本机 SQLite/FTS。召回默认按当前 repo 范围检索；同一个 git common dir 下的多个 worktree 视为同一个 repo。

```bash
csep recall "这个仓库之前 phase2 hooks 怎么设计的"
csep recall --recent
csep recall "hermes OR recall" --global
csep recall "focused query" --format json
```

手动归档或回填历史会话：

```bash
csep session-archive --transcript-path /path/to/session.jsonl --cwd /path/to/repo --session-id <id>
csep session-ingest --backfill --root ~/.codex/sessions
```

## 运行时目录

默认状态目录：

```text
~/.codex-self-evolution/
├── .env.provider
├── config.toml
├── logs/
├── session_reflection/
│   ├── jobs/
│   ├── runs/
│   ├── child_threads/
│   ├── triggers/
│   ├── locks/
│   └── latest.json
├── session_recall/
│   └── state.db
└── projects/
    └── -Users-you-code-repo/
        └── memory/
            ├── USER.md
            └── MEMORY.md
```

每个 repo 会按绝对路径分配独立 bucket，不会把运行时产物写进业务代码仓库。

## CLI 命令

| 命令 | 说明 |
| --- | --- |
| `csep session-start --from-stdin` | Codex SessionStart hook 入口。 |
| `csep session-stop --from-stdin` | Codex Stop hook 入口：归档 session，评估触发规则，必要时创建 reflection job。 |
| `csep session-reflect --status` | 查看 session reflection 最新 job、全局锁和触发器状态。 |
| `csep session-reflect --hook-payload <file>` | 用保存的 Stop payload 手动创建并执行 reflection job。 |
| `csep status` | 输出只读诊断快照。 |
| `csep config show/init/validate/path` | 管理本地 `config.toml`。 |
| `csep migrate-worktrees` | 合并同一个 git common dir 下的历史 bucket。 |
| `csep recall "..."` | 面向模型使用的 session recall wrapper，默认 repo scope。 |
| `csep recall --recent` | 返回当前 repo 最近归档的 session。 |
| `csep recall "..." --global` | 跨 repo/worktree 检索 session recall。 |
| `csep session-archive --transcript-path ... --cwd ... --session-id ...` | 手动归档一份 Codex transcript。 |
| `csep session-ingest --backfill` | 回填历史 Codex session transcript。 |

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

也可以使用 Makefile：

```bash
make test PYTHON=.venv/bin/python
```

构建发布包：

```bash
uvx --from build pyproject-build
```

## 当前状态

已经可用：

- Codex-first `SessionStart` / `Stop` 生命周期接入
- session 级 trigger counters
- app-server fork reflection
- memory 写入与 receipt 边界校验
- `csep-reflect-*` skill 写入与前缀保护
- SQLite/FTS session recall
- worktree-aware repo scope
- 只读 status / config / migration 工具

仍在演进：

- Codex CLI plugin hooks 的正式发布版本兼容
- first-run onboarding
- reflection 质量评估
- Claude Code / Cursor 等其他客户端适配

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/getting-started.md](docs/getting-started.md) | 本机安装、启用、验证和排障。 |
| [docs/2026-05-15-memory-skill-session-recall-architecture.html](docs/2026-05-15-memory-skill-session-recall-architecture.html) | Memory / Skill / Session Recall 三条线架构图。 |
| [docs/superpowers/specs/2026-05-15-session-reflection-trigger-policy-design.md](docs/superpowers/specs/2026-05-15-session-reflection-trigger-policy-design.md) | session reflection trigger policy 设计。 |
| [docs/superpowers/specs/2026-05-15-legacy-review-compile-synthesis-removal-design.md](docs/superpowers/specs/2026-05-15-legacy-review-compile-synthesis-removal-design.md) | 本轮系统收敛设计。 |

## License

MIT
