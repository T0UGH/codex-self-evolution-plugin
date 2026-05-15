# 起步指南

> 适用环境：macOS + bash + Python 3.11+。
>
> 目标：把当前唯一保留的链路跑通：`SessionStart` 注入背景，`Stop` 归档 session 并按规则触发 reflection，reflection 写 memory / `csep-reflect-*` skill，`csep recall` 从 session 库召回历史上下文。

## 1. 前置检查

在仓库根目录先确认：

```bash
python3 --version
which uv
codex --version
```

本地开发安装：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

用户级安装：

```bash
scripts/install.sh
```

安装脚本会用 `uv tool install --force <当前仓库>` 安装 `codex-self-evolution` / `csep`，刷新 Codex plugin cache，并清理旧版 marker-managed user hook。

## 2. 初始化配置

查看配置路径：

```bash
codex-self-evolution config path
```

写入默认配置：

```bash
codex-self-evolution config init
codex-self-evolution config validate
```

默认配置：

```toml
schema_version = 2

[session_reflection]
enabled = true
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = true
sandbox = "danger-full-access"
approval_policy = "never"
skill_prefix = "csep-reflect-"
timeout_seconds = 900
max_concurrent_jobs = 1

[session_reflection.trigger]
enabled = true
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 1800

[session_recall]
enabled = true
stop_hook_archive = true

[log]
retention_days = 14
```

## 3. 启用 Codex Plugin Hooks

在 `~/.codex/config.toml` 中启用：

```toml
[features]
plugins = true
hooks = true
plugin_hooks = true

[plugins."codex-self-evolution@codex-self-evolution"]
enabled = true
```

插件声明文件：

```text
plugins/codex-self-evolution/.codex-plugin/plugin.json
plugins/codex-self-evolution/.codex-plugin/hooks.json
```

声明的生命周期入口：

```text
SessionStart -> codex-self-evolution session-start --from-stdin
Stop         -> codex-self-evolution session-stop --from-stdin
```

如果当前 Codex CLI 还不支持 `plugin_hooks`，这一步不会生效；先升级 Codex，再重跑 `scripts/install.sh` 刷新 plugin cache。

## 4. 验证 SessionStart

手工写一条 memory 到当前 repo bucket：

```bash
REPO=$(pwd)
BUCKET=~/.codex-self-evolution/projects/$(python3 -c "import os; print(os.getcwd().replace('/', '-'))")
mkdir -p "$BUCKET/memory"
cat > "$BUCKET/memory/USER.md" <<'EOF'
# User stable background
My favorite test passphrase is XANADU_RIVER_442.
EOF
```

新开一次 Codex 会话并提问：

```bash
codex exec --json 'What is my favorite test passphrase?' 2>/dev/null | grep -i XANADU
```

看到 passphrase 即表示 `SessionStart` 已把 stable background 注入当前会话。测试后清理：

```bash
rm "$BUCKET/memory/USER.md"
```

## 5. 验证 Stop Hook 与 Reflection

正常跑一次 Codex：

```bash
codex exec 'Say one sentence in Chinese to test my Stop hook.'
```

Stop hook 会快速返回；如果触发规则认为需要 reflection，会在后台创建 job。等待 15 到 30 秒后查看：

```bash
codex-self-evolution session-reflect --status | python3 -m json.tool
codex-self-evolution status | python3 -m json.tool
```

关注：

| 字段 | 含义 |
| --- | --- |
| `session_reflection.latest.status` | 最新 job 状态 |
| `session_reflection.global_lock` | 全局 reflection 锁状态 |
| `session_reflection.trigger.latest_state` | 当前 session 的计数器与最近决策 |
| `session_recall.db_exists` | session recall 数据库是否存在 |

失败时先看：

```text
~/.codex-self-evolution/session_reflection/latest.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/receipt.json
~/.codex-self-evolution/session_reflection/runs/<job_id>/validation.json
/tmp/codex-self-evolution/session-reflect-*.log
~/.codex-self-evolution/logs/plugin.log
```

## 6. 手动调试 Reflection

保存一份 Stop payload 后，可以手动创建并执行 job：

```bash
codex-self-evolution session-reflect --hook-payload /path/to/stop-payload.json | python3 -m json.tool
```

只查看状态：

```bash
codex-self-evolution session-reflect --status | python3 -m json.tool
```

只要 `validation.status` 是 `succeeded`，说明 child thread 的写入声明和父进程边界校验都通过。

## 7. Session Recall

Stop hook 默认会把 transcript 归档到本机 SQLite/FTS：

```text
~/.codex-self-evolution/session_recall/state.db
```

召回当前 repo 的历史片段：

```bash
csep recall "这个仓库之前 trigger policy 怎么设计的"
csep recall --recent
```

跨 repo 检索：

```bash
csep recall "session reflection" --global
```

输出 JSON 便于调试：

```bash
csep recall "session reflection" --format json | python3 -m json.tool
```

手动归档单个 transcript：

```bash
csep session-archive --transcript-path /path/to/session.jsonl --cwd /path/to/repo --session-id <id>
```

回填历史 Codex sessions：

```bash
csep session-ingest --backfill --root ~/.codex/sessions --since-days 14
```

## 8. 常见问题

### `plugin_hooks` 不生效

先确认 Codex 支持插件 hook：

```bash
codex --help 2>&1 | grep -i plugin
codex plugin --help 2>&1 | head -20
```

再确认本地 plugin cache 已刷新：

```bash
scripts/install.sh
codex-self-evolution status | python3 -m json.tool
```

### `session_reflection.latest.status` 是 `skipped`

这是正常状态之一。常见原因：

- 当前 session 计数器还没达到 nudge interval
- 只有归档价值，没有触发 reflection 的高信号
- 同一个父 session 已有活跃 job，递归保护生效
- `[session_reflection] enabled = false`

查看 `session_reflection.trigger.latest_state.last_decision` 可以知道具体原因。

### `session_reflection.latest.status` 是 `failed`

优先查看：

```bash
codex-self-evolution session-reflect --status | python3 -m json.tool
tail -50 ~/.codex-self-evolution/logs/plugin.log
```

常见原因：

- `codex app-server proxy` 当前不可用
- child thread 没写 `receipt.json`
- receipt 声明的写入路径越界
- skill 前缀不是 `csep-reflect-`

### `csep recall` 没结果

确认归档是否打开：

```bash
codex-self-evolution config show | python3 -m json.tool
codex-self-evolution status | python3 -m json.tool
```

如果是新安装，先跑几次真实 Codex 会话，或者手动回填：

```bash
csep session-ingest --backfill --root ~/.codex/sessions --limit-files 50
```

### 想重置本地状态

重置当前 repo bucket：

```bash
BUCKET=~/.codex-self-evolution/projects/$(python3 -c "import os; print(os.getcwd().replace('/', '-'))")
rm -rf "$BUCKET"
```

重置 session 级运行状态：

```bash
rm -rf ~/.codex-self-evolution/session_reflection
rm -rf ~/.codex-self-evolution/session_recall
```

不会删除 `~/.codex-self-evolution/config.toml` 和 `.env.provider`。
