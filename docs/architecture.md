# 当前架构

本文记录 CSEP 当前保留的系统设计。历史 reviewer、compiler、scheduler、managed skill synthesis 已经退出主链路；如果本文和旧设计文档冲突，以本文为准。

## 目标

CSEP 只做一件事：让本机 Codex session 的经验能在后续 session 中继续使用。

当前系统保留三条线：

| 线 | 写入 | 使用 |
| --- | --- | --- |
| Stable Memory | session reflection 写 `MEMORY.md` / `memory/refs/` | `SessionStart` 注入 `MEMORY.md` |
| Reflection Skills | session reflection 写 `~/.codex/skills/csep-reflect-*` | Codex 原生 skills loader 加载 |
| Session Recall | `Stop` hook 或 backfill 归档 transcript 到 SQLite/FTS | `csep recall` 按 repo 或全局召回历史片段 |

## 生命周期

```text
Codex SessionStart
  -> csep session-start --from-stdin
  -> 读取当前 repo bucket 的 MEMORY.md
  -> 注入 stable background 和极短 recall skill pointer

Codex 正常工作

Codex Stop
  -> csep session-stop --from-stdin
  -> 归档 transcript 到 session_recall SQLite/FTS
  -> deterministic trigger policy 判断是否需要 reflection
  -> archive_only: 快速返回
  -> queued: 创建 session reflection job，并后台 fork worker

Session Reflection Worker
  -> 通过 Codex app-server fork 当前 thread
  -> child Codex review memory 和 skill
  -> child 原子写 MEMORY.md / refs / csep-reflect-* skill / receipt.json
  -> parent 校验 receipt、路径、hash、skill 前缀
  -> 更新 job 状态和 trigger counters

csep recall
  -> 查询 session_recall SQLite/FTS
  -> 使用 grep-like needle query
  -> 默认当前 repo scope
  -> 显式 --global 才跨 repo
  -> 返回受预算控制的 session-level evidence window
```

## 运行时目录

默认根目录是 `~/.codex-self-evolution/`：

```text
~/.codex-self-evolution/
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
    └── <repo-bucket>/
        └── memory/
            ├── MEMORY.md
            └── refs/
                └── ...
```

业务仓库不保存运行时状态。每个 repo 按绝对路径分配 bucket；worktree 场景会尽量解析到稳定的 repo scope。`MEMORY.md` 是默认注入文件；`refs/` 只作为按需引用区，旧 `USER.md` 如存在会被忽略。

## 命令面

用户日常使用的命令：

| 命令 | 说明 |
| --- | --- |
| `csep setup` | 安装 CLI、注册 Codex plugin marketplace、启用 plugin hooks |
| `csep status` | 输出只读运行状态 |
| `csep recall "needle1|needle2"` | 查询当前 repo 的历史 session |
| `csep recall --recent` | 查看最近归档的 session |
| `csep recall bootstrap` | 回填本机历史 Codex sessions |
| `csep session-reflect --status` | 查看 reflection job 状态 |

生命周期 hook 使用的命令：

| 命令 | 说明 |
| --- | --- |
| `csep session-start --from-stdin` | Codex `SessionStart` hook |
| `csep session-stop --from-stdin` | Codex `Stop` hook |

排障和回填命令：

| 命令 | 说明 |
| --- | --- |
| `csep session-reflect --hook-payload <path>` | 手动从 Stop payload 创建 reflection job |
| `csep session-archive ...` | 手动归档单个 transcript |
| `csep session-ingest --backfill ...` | 底层历史回填入口，通常用 `csep recall bootstrap` |
| `csep config ...` | 查看、初始化、验证配置 |
| `csep migrate-worktrees` | 维护旧 worktree bucket 合并 |

`codex-self-evolution` 仍作为兼容入口存在，但 README 和安装路径都以 `csep` 为主。

## 安装模型

普通用户不需要 clone 仓库：

```bash
uvx csep setup
```

`setup` 会做三件事：

1. `uv tool install --force --reinstall --refresh csep`
2. `codex plugin marketplace add T0UGH/codex-self-evolution-plugin`
3. 更新 `~/.codex/config.toml`：

```toml
[features]
plugins = true
hooks = true
plugin_hooks = true

[plugins."codex-self-evolution@codex-self-evolution"]
enabled = true
```

插件 manifest 和 hooks 文件保持两份同步：

```text
plugins/codex-self-evolution/.codex-plugin/
src/codex_self_evolution/plugin_bundle/.codex-plugin/
```

## 边界

- CSEP 不上传 session、memory 或 recall 数据。
- Provider secret 不进仓库，默认放在 `~/.codex-self-evolution/.env.provider`。
- Reflection child 只能写当前 repo memory bucket 和 `csep-reflect-*` skill。
- Parent 只信任 `receipt.json` 和本地校验结果，不信任 child 的口头声明。
- Hook 前台不能做长耗时模型工作；慢任务必须进入后台 worker。
- 旧 pending suggestion、compiler receipt、`recall/index.json`、scheduler 产物不再参与运行。
