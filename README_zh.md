# Codex Self-Evolution Plugin（中文）

> 语言：**中文** | [English](README.md)
>
> 第一次在本机起步？直接看 [docs/getting-started.md](docs/getting-started.md) 按阶段跑一遍。

一个本地的 Codex 插件，运行分阶段的自我进化循环：

- **`SessionStart`** — 初始化运行时状态，注入 `USER.md` + `MEMORY.md` 构成的稳定背景，以及 recall policy 与 session-recall skill。
- **`Stop`** — 构建标准化 review snapshot，调用 provider-backed reviewer，落盘一份结构化的 `SuggestionEnvelope`（memory updates、recall candidates、skill actions）。
- **`compile-preflight`** — 廉价的调度器唤醒/检查步骤，返回 `skip_empty`、`skip_locked` 或 `run`。
- **`compile`** — writer-owned 的批量晋升步骤。先读取 existing memory / recall 作为上下文，再运行可插拔 backend（`script` 或 `agent:opencode`），最后原子写入终态资产。
- **`recall` / `recall-trigger`** — 在 live turn 中做聚焦召回。

compiler 是唯一负责最终资产写入（memory、recall、managed skills、receipt）的组件；backend 只负责产出结构化 artifacts。

---

## 安装

```bash
pip install -e .
# 或者不安装：
PYTHONPATH=src python -m codex_self_evolution.cli --help
```

需要 Python 3.11+。

---

## 命令

```bash
codex-self-evolution session-start --cwd /path/to/repo
codex-self-evolution stop-review --hook-payload /path/to/stop_payload.json
codex-self-evolution compile-preflight --state-dir data
codex-self-evolution compile --once --state-dir data --backend script
codex-self-evolution compile --once --state-dir data --backend agent:opencode
codex-self-evolution recall --query "context" --cwd /path/to/repo
codex-self-evolution recall-trigger --query "remember previous flow" --cwd /path/to/repo
```

等价的模块调用形式：

```bash
python -m codex_self_evolution.cli session-start --cwd /path/to/repo
```

---

## 配置项

本节列出所有可配置项。所有变量**默认都是可选的**；如果你只跑确定性的 `dummy` / `script` 路径，不需要任何配置。"必须"一栏说明在什么场景下某项才成为强制。

### 1. 运行时路径

| Flag / 参数 | 必须 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `--cwd` | `session-start`、`recall`、`recall-trigger` 必须 | — | 当前会话操作的 repo。 |
| `--state-dir` | 可选 | `<cwd>/data` | 持久化运行时状态的根目录（suggestions、memory、recall、skills、compiler receipts、review snapshots、scheduler）。 |
| `--repo-root` | `compile`、`compile-preflight` 可选 | 当前进程 CWD | 当 `--state-dir` 未指定时用来解析 state-dir 的 repo 根。 |
| `--once` | `compile` 可选 | 关闭 | 只跑一次 compile，不循环。 |
| `--backend` | `compile` 可选 | `script` | 取值 `script` 或 `agent:opencode`。默认 scheduler plist 用 `agent:opencode`。 |
| `--explicit` | `recall-trigger` 可选 | 关闭 | 标记该 recall 触发为用户显式发起。 |

`--state-dir` 下的目录布局：

```
data/
├── suggestions/{pending,processing,done,failed,discarded}/
├── memory/            # USER.md, MEMORY.md, memory.json
├── recall/            # index.json, compiled.md
├── skills/managed/    # managed skill markdown + manifest.json
├── compiler/          # compile.lock, last_receipt.json
├── review/snapshots/  # Stop 时的标准化 snapshot
└── scheduler/
```

### 2. Hook 环境变量（Codex 注入）

这些变量由 Codex 宿主在调用 `.codex-plugin/plugin.json` 里定义的 hook 命令时注入，**用户不需要手工设置**。

| 变量 | 作用范围 | 用途 |
| --- | --- | --- |
| `CODEX_CWD` | `session-start`、`recall`、`recall-trigger` | 当前 repo 工作目录。 |
| `CODEX_STATE_DIR` | 所有 hook | 指向运行时 state 目录。 |
| `CODEX_HOOK_PAYLOAD` | `stop-review` | Stop payload JSON 的路径。 |
| `CODEX_RECALL_QUERY` | `recall`、`recall-trigger` | 聚焦召回的查询串。 |

### 3. Reviewer providers（`Stop` 阶段）

reviewer 是 provider-backed 的。选择优先级：

1. Stop payload 里的 `reviewer_provider` 字段。
2. 否则走 `dummy`。

| Provider | 用途 | 使用时必须配置 |
| --- | --- | --- |
| `dummy` | 测试 / dry run 用的确定性 stub | **无**（可选支持 Stop payload 里的 `provider_stub_response`） |
| `openai-compatible` | OpenAI chat-completions 协议 | `OPENAI_API_KEY`（或显式传入 `api_key` 选项） |
| `anthropic-style` | Anthropic messages 协议 | `ANTHROPIC_API_KEY` |
| `minimax` | MiniMax（走 Anthropic 协议的端点） | `MINIMAX_API_KEY` |

#### Reviewer 环境变量

| 变量 | 必须 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | `openai-compatible` 必须 | — | Bearer token。 |
| `OPENAI_BASE_URL` | 可选 | `https://api.openai.com/v1/chat/completions` | 覆盖端点。 |
| `OPENAI_REVIEW_MODEL` | 可选 | `gpt-4.1-mini` | 请求体里的 model id。 |
| `ANTHROPIC_API_KEY` | `anthropic-style` 必须 | — | `x-api-key` 头。 |
| `ANTHROPIC_BASE_URL` | 可选 | `https://api.anthropic.com/v1/messages` | 覆盖端点。 |
| `ANTHROPIC_REVIEW_MODEL` | 可选 | `claude-3-5-haiku-latest` | Model id。 |
| `MINIMAX_API_KEY` | `minimax` 必须 | — | Bearer token。 |
| `MINIMAX_REGION` | 可选 | `global` | `global` → `https://api.minimax.io/anthropic/v1/messages`；`cn` → `https://api.minimaxi.com/anthropic/v1/messages`。 |
| `MINIMAX_BASE_URL` | 可选 | 由 region 推导 | 完整 URL 覆盖，优先级高于 `MINIMAX_REGION`。 |
| `MINIMAX_REVIEW_MODEL` | 可选 | `MiniMax-M2.7` | Model id。 |

#### Reviewer provider options（编程入参）

直接调用 `run_reviewer(...)` 时通过 `provider_options` dict 传入。每个选项会覆盖对应的环境变量。

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | 从环境变量读 | 覆盖 provider 的 env key。 |
| `api_base` | provider 默认 | 完整 URL。 |
| `model` | provider 默认 | Model id。 |
| `max_tokens` | `800` | OpenAI / Anthropic / MiniMax 协议都适用。 |
| `timeout_seconds` | `30` | HTTP 超时。 |
| `anthropic_version` | `2023-06-01` | Anthropic 协议的 `anthropic-version` 头。 |
| `stub_response` | — | 仅 Dummy provider 用，预置的 reviewer JSON。 |

### 4. Compile backends

通过 `--backend` 选择：

| Backend | 必须 | 说明 |
| --- | --- | --- |
| `script` | 无 | 确定性 Python merge，安全默认。读取 existing memory / recall，做保守增量 merge（不会洗掉稳定条目）。 |
| `agent:opencode` | `opencode` 二进制在 `PATH` 中 **或** 指定 `opencode_command` | 把 `{batch, existing_assets, repo, contract}` JSON 从 stdin 送入，从 stdout 解析严格 JSON。任何失败（二进制缺失 / 非零退出 / 超时 / 非法 JSON / schema 不符）都会 fallback 到 `script`，除非 `allow_fallback=False`。 |

#### Agent compiler 配置

| 途径 | 变量 / 选项 | 默认值 | 用途 |
| --- | --- | --- | --- |
| 环境变量 | `CODEX_SELF_EVOLUTION_OPENCODE_COMMAND` | — | 用空格分隔的 argv，替代 `opencode run --stdin-json --stdout-json`。 |
| `options["opencode_command"]` | — | env 变量，其次 `["opencode", "run", "--stdin-json", "--stdout-json"]` | 显式 argv 列表，优先级高于 env 变量。 |
| `options["opencode_timeout_seconds"]` | — | `900`（15 分钟） | 子进程超时。**严格小于 `DEFAULT_LOCK_STALE_SECONDS`**，确保 agent 卡住时子进程先 timeout → backend fallback → `finally` 清锁，避免被下一个 preflight 当 stale 抢锁导致并发写。 |
| `options["allow_fallback"]` | — | `True` | 为 `False` 时，agent backend 失败不会 fallback，而是直接抛 `RuntimeError`。 |

Agent 路径失败时追加到 `CompileArtifacts.discarded_items` 的原因：

- `opencode_unavailable` — 二进制不在 `PATH` 上，也没有自定义 invoker。
- `agent_invoke_failed` — 子进程抛异常（非零退出、超时等）；`detail` 带截断后的错误信息。
- `agent_output_invalid` — stdout 不是合法 JSON，或不符合响应 schema；`detail` 带解析错误。

Agent 响应 schema（`src/codex_self_evolution/compiler/agent_io.py::COMPILE_CONTRACT`）：

```json
{
  "memory_records": {"user": [...], "global": [...]},
  "recall_records": [...],
  "compiled_skills": [...],
  "manifest_entries": [...],
  "discarded_items": [...]
}
```

### 5. Compile runtime

在 `src/codex_self_evolution/config.py` 中定义：

| 常量 | 默认值 | 用途 |
| --- | --- | --- |
| `DEFAULT_BATCH_SIZE` | `100` | 每次 compile pass 最多 claim 的 suggestion 数。从自有调度器调用 `run_compile(batch_size=...)` 可覆盖。 |
| `DEFAULT_LOCK_STALE_SECONDS` | `1800`（30 分钟） | `compile.lock` 的硬上限。正常 compile 应远低于此值（预期 5-10 分钟）。stale 判定细节见 [Compile lock 保护](#6-compile-lock-保护)。 |
| `PLUGIN_OWNER` | `codex-self-evolution-plugin` | 只有 owner 等于这个字符串的 managed skill 才允许 compiler 修改。用于拒绝写入非托管 skill。 |

### 6. Compile lock 保护

`<state-dir>/compiler/compile.lock` 文件锁串行化 compile 运行。锁内容是 JSON：`{created_at, pid}`。下一次 `preflight` / `file_lock` 调用时，**满足任一条件即判 stale 可回收**：

| 条件 | 检测方式 | 原因 |
| --- | --- | --- |
| 持锁 `pid` 已不存在 | `os.kill(pid, 0)` 抛 `ProcessLookupError` | 进程被 SIGKILL / crash / 机器重启后锁成孤儿。立即清。 |
| `created_at` 在未来（`age_seconds < 0`） | `utc_now() - created_at` | 时钟回拨 / NTP 调整，不信任来自"未来"的锁。 |
| `created_at` 早于 `DEFAULT_LOCK_STALE_SECONDS`（30 分钟）前 | age 阈值 | 进程仍在但跑得过久，超过容忍时间。 |

**设计契约（无 heartbeat）**：`opencode_timeout_seconds`（默认 15 分钟）**必须严格小于** lock stale 窗口（30 分钟）。agent 卡住时 → 子进程 timeout → `AgentCompilerBackend._fallback` 跑 → `finally` 释放锁。整条链在下一个 preflight 判 stale 前完成。改动任一常量都要保持这个不变量。

`lock_status(paths)` 返回 `{locked, stale, stale_reason, pid_alive, age_seconds, owner_pid}` 便于诊断。

### 7. Scheduler（launchd）

模板 plist：`docs/launchd/com.codex-self-evolution.preflight.plist`。

**必须改的字段**：

- **解释器路径**（例如 `/Users/haha/hermes-agent/venv/bin/python3.11`）要匹配你本地的 Python venv。
- **工作目录**改为你的 repo 根。
- **`--state-dir`** 改成你 runtime state 的绝对路径。

作业要廉价唤醒：跑 `compile-preflight`，只在其返回 `run` 时才调 `compile`：

```bash
codex-self-evolution compile-preflight --state-dir data
# 如果 status == run:
codex-self-evolution compile --once --state-dir data --backend agent:opencode
```

### 8. Docker / 冒烟测试

| 变量 | 使用位置 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `PYTHON` | Makefile targets | `/Users/haha/hermes-agent/venv/bin/python3.11` | `make test`、`make preflight`、`make provider-smoke-*` 使用的解释器。自己 venv 路径不同时请覆盖。 |
| `IMAGE` | `make docker-*` | `codex-self-evolution-e2e` | Docker 镜像 tag。 |
| `ENV_FILE` | `make provider-smoke-*` | `.env.provider` | 跑真实 provider 冒烟测试前自动 source。 |

存在 `.env.provider` 文件时 Makefile 会自动 source。从模板复制：

```bash
cp .env.provider.example .env.provider
# 填上需要的 key
```

---

## Reviewer 运行时

Reviewer 调用在 `src/codex_self_evolution/review/runner.py`，流程：

1. 读取 `src/codex_self_evolution/review/prompt.md` 的 prompt。
2. 解析出对应 provider（`dummy`、`openai-compatible`、`anthropic-style`、`minimax`）。
3. 发送标准化后的 review snapshot。
4. 通过 `parse_reviewer_output(...)` 解析 JSON，并用 `ReviewerOutput` schema 校验。非法输出会抛 `SchemaError` 直接拒绝。

主 Stop 路径不再信任 payload 里预置的 `reviewer_output`；fixture 只在测试中作为替身。

---

## Compile 流水线

```
pending suggestion batch
  + existing memory / recall / manifest（由 build_compile_context 读入）
  -> backend.compile(batch, context, options)
  -> apply_compiler_outputs(...)   # 原子写入 memory / recall / skills
  -> write_receipt(...)
```

- `build_compile_context` 读取 `memory/USER.md`、`memory/MEMORY.md`、`memory/memory.json`、`recall/index.json`、`recall/compiled.md` 以及 skill manifest。文件缺失或损坏会优雅回退为空值，不会抛异常。
- `ScriptCompilerBackend` 用 `compile_memory(existing_index=...)` 和 `compile_recall(existing_records=...)`：existing 条目默认保留，new suggestions 只在出现新的 `(scope, content)`（memory）或新的 `sha1(content)`（recall）时 append。
- `AgentCompilerBackend` 把完整 payload（batch + existing_assets + repo + contract）送入 `opencode`，严格解析返回 JSON，任何失败都 fallback 到 `script`。
- 最终写入（`apply_compiler_outputs`）由 compiler engine 持有，不再依赖独立 writer 模块。

`suggestions/` 下每条 suggestion 带：

- 稳定的 `suggestion_id`
- `idempotency_key`
- 显式的 `state`
- `attempt_count`
- 可选的 `failure_reason`
- `transition_log`

---

## Docker E2E

容器化的冒烟/e2e 流程已经集成：

```bash
docker build -t codex-self-evolution-e2e .
docker run --rm codex-self-evolution-e2e
# 或 compose：
docker compose run --rm e2e
# 或一步：
make docker-e2e
```

容器里跑的是 `scripts/docker-e2e.sh`，流程为：

1. 跑 `pytest`
2. 跑 `session-start`
3. 生成 Stop payload 并跑 `stop-review`
4. 跑 `compile-preflight`
5. 跑 `compile --backend agent:opencode`（容器内未装 `opencode`，会 fallback 到 `script`）
6. 跑 `recall-trigger`
7. 验证最终的 memory / skill / receipt 产物

### 真实 provider 冒烟测试

```bash
make provider-smoke-minimax
make provider-smoke-openai
make provider-smoke-anthropic
```

推荐首选：`make provider-smoke-minimax`。

各 provider 必需的 env：`MINIMAX_API_KEY`、`OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`。可选覆盖项见上文[Reviewer providers](#3-reviewer-providersstop-阶段) 章节。

这些 target 会调用 `scripts/provider-smoke-test.py` 去打真实 provider 接口，并打印出结构化 reviewer 输出 + 请求 payload 元数据。

### 本地测试

```bash
make test           # pytest
make e2e-local      # scripts/docker-e2e.sh 不走 Docker
make preflight      # 对 data/ 跑一次 compile-preflight
```

---

## 开发笔记

- Hook 配置只存在于 `.codex-plugin/plugin.json`。
- 最终写入归属 `src/codex_self_evolution/compiler/engine.py`（不再是单独的 `writer.py`）。
- Managed skills 隔离在 `skills/managed/` 下，需要 plugin-owned manifest 条目（owner = `codex-self-evolution-plugin`）。compiler 拒绝改非此 owner 的 skill。
- review snapshot 被标准化后保存在 `review/snapshots/` 下，便于调试与审计。
- recall 使用 repo/cwd-first 排序策略，改用 trigger helper，而不是在 session start 预加载大量召回内容。
- 改动 compile 行为前建议先读 `docs/2026-04-20-compiler-existing-assets-handoff.md`，了解当前 existing-assets 流水线背后的设计依据。
