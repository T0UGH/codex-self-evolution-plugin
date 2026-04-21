# 起步指南(本机上跑起来)

> 适用环境:macOS + bash + Python 3.11+。
>
> 目标:在**不改任何插件代码**的前提下,按阶段把 reviewer → compile → memory/recall/skills 的整条循环在你本机跑通,然后再选择是否挂到 launchd 自动调度。

全文档结构:

1. [前置检查](#1-前置检查)
2. [阶段 1:provider 冒烟](#阶段-1provider-冒烟30-秒)
3. [阶段 2:手动跑一次完整循环](#阶段-2手动跑一次完整循环2-分钟)
4. [阶段 3:挂 Codex 原生 Stop hook(一键脚本)](#阶段-3挂-codex-原生-stop-hook一键脚本1-分钟)
5. [阶段 4:挂 launchd 自动调度](#阶段-4挂-launchd-自动调度5-分钟)
6. [阶段 5:诊断命令(确认装对了)](#阶段-5诊断命令确认装对了)
7. [Codex CLI 插件集成](#codex-cli-插件集成)
8. [常见坑](#常见坑)

---

## 1. 前置检查

在仓库根目录(`/path/to/codex-self-evolution-plugin`)先确认以下都满足:

```bash
# Python 3.11+
python3 --version

# opencode 可选,但推荐装上
which opencode && opencode --version

# 至少配一个 reviewer provider 的 API key(任选)
env | grep -E '^(MINIMAX|OPENAI|ANTHROPIC)_API_KEY' | sed 's/=.*/=<set>/'
```

如果是首次使用,创建并激活虚拟环境,装好依赖:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install pytest        # 仅跑测试需要
```

以下所有命令都假设用 `.venv/bin/python` 作为解释器。没用虚拟环境的话,把 `.venv/bin/python` 替换为你的 Python 可执行文件。

---

## 阶段 1:provider 冒烟(30 秒)

**目的**:验证 reviewer provider key + 网络都通,避免后面调 provider 才发现问题。

推荐 MiniMax(首选成本低):

```bash
.venv/bin/python scripts/provider-smoke-test.py --provider minimax
```

其他 provider:

```bash
.venv/bin/python scripts/provider-smoke-test.py --provider openai-compatible
.venv/bin/python scripts/provider-smoke-test.py --provider anthropic-style
```

**期望输出**:一段结构化的 reviewer JSON + 请求 payload 元信息。
**常见失败**:

- `MINIMAX_API_KEY` 没设 → 看[常见坑](#常见坑)
- 401 / 403 → key 本身失效
- `JSONDecodeError` / `reviewer did not return valid JSON` → provider 返回了非 JSON 内容(模型加了代码块围栏已被兼容;加了解释文本就会挂)

---

## 阶段 2:手动跑一次完整循环(2 分钟)

**目的**:在不依赖 Codex CLI 的情况下,完整演示 `session-start → stop-review → preflight → compile → 产物落盘` 的闭环。

```bash
# 1. 定义:被插件“记忆”的目标 repo,以及 playground 用的 state 目录
REPO=/path/to/your/target/repo          # 可以就填本仓库本身,做 self-hosting
STATE=/tmp/csep-tutorial                # 教程用独立目录便于随时 rm -rf
```

**默认位置**:不指定 `--state-dir` 时,所有状态(suggestions/memory/recall/review)
都会写到 `~/.codex-self-evolution/projects/<$REPO-absolute-path-with-/-replaced-by-->`,
**不再污染原始代码仓库**。下面教程里为了好清理用了 `/tmp/csep-tutorial`,生产
场景直接省略 `--state-dir` 即可。

建议 `$REPO` 就填这个插件自身的路径作为 playground:

```bash
REPO=/Users/$USER/code/github/codex-self-evolution-plugin
STATE=/tmp/csep-tutorial
```

### 2.1 Session 初始化

```bash
.venv/bin/python -m codex_self_evolution.cli session-start --cwd $REPO --state-dir $STATE
```

**期望**:stdout 打印 `stable_background`、`recall.policy` 的 JSON;`$STATE/memory/`、`$STATE/recall/` 等目录被创建。

### 2.2 构造一个假的 Stop payload

真实场景下 Codex 会自动生成 Stop payload。手工模拟:

```bash
cat > /tmp/stop.json <<EOF
{
  "thread_id": "thread-demo",
  "turn_id": "turn-1",
  "cwd": "$REPO",
  "transcript": "修好了 compile lock,加了 pid 检查,超时从 120s 调到 15 分钟",
  "thread_read_output": "context",
  "reviewer_provider": "minimax"
}
EOF
```

`reviewer_provider` 改成 `openai-compatible` / `anthropic-style` / `dummy` 都可以。用 `dummy` 时可以顺便放 `provider_stub_response` 字段让 review 产出可控。

### 2.3 调 reviewer,产出 pending suggestion

```bash
.venv/bin/python -m codex_self_evolution.cli stop-review \
  --hook-payload /tmp/stop.json \
  --state-dir $STATE
```

**期望**:
- 打印 `suggestion_count > 0`、`pending_suggestion_path` 指向 `$STATE/suggestions/pending/<id>.json`
- `$STATE/review/snapshots/` 下生成这次的 normalized snapshot

如果 `suggestion_count == 0`,说明 reviewer 没产出任何 suggestion(模型判断本轮没什么可记的);换一个更"值得沉淀"的 `transcript` 再试。

### 2.4 Preflight:判断是否真需要跑 compile

```bash
.venv/bin/python -m codex_self_evolution.cli compile-preflight --state-dir $STATE
```

**期望**:`"status": "run"`(有 pending 且无 lock)。

其他可能:
- `"status": "skip_empty"` — 没 pending 也没 retryable failed,不用跑
- `"status": "skip_locked"` — 有活的 compile 在跑,这次跳过

### 2.5 真正 compile 写入终态资产

```bash
.venv/bin/python -m codex_self_evolution.cli compile \
  --once --state-dir $STATE --backend agent:opencode
```

> **两种 backend 的取舍**:`agent:opencode` 会真正调 opencode CLI 做一次语义级合并(dedupe + 改写更流畅,约 20–40 秒),需要本地 `opencode` 可用且已登录。`script` 走纯规则拼装(< 100ms,确定性),不依赖 LLM。调试和 CI 用 `--backend script`;生产/定时任务默认用 `agent:opencode`。
>
> 如果 opencode 不在 PATH 或调用失败,agent backend 会在 receipt 的 `discarded_items` 里记 `agent_invoke_failed` / `opencode_unavailable`,然后**自动 fallback 到 script**,compile 不会整个失败。

**期望**:
- `"status": "success"` + `processed_count >= 1`
- `$STATE/memory/USER.md`、`MEMORY.md`、`memory.json` 写好
- `$STATE/recall/index.json`、`compiled.md` 写好
- `$STATE/skills/managed/*.md`(如果 reviewer 给了 skill_action)
- `$STATE/compiler/last_receipt.json` 记录这次的结果
- 对应 `pending/*.json` 被移到 `done/*.json`

### 2.6 查看产出

```bash
cat $STATE/memory/MEMORY.md
cat $STATE/memory/USER.md
cat $STATE/recall/compiled.md
cat $STATE/compiler/last_receipt.json
ls $STATE/suggestions/done/
```

验证增量合并行为:再造一份 Stop payload,transcript 换成别的内容,重跑 2.3-2.5。`USER.md` / `MEMORY.md` 里**旧条目应该仍在**,新条目 append 到后面。这就是 P3 保守增量 merge 在起作用。

---

## 阶段 3:挂 Codex 原生 Stop hook(一键脚本,1 分钟)

**目的**:让 Codex 每次对话结束自动触发 stop-review,不再需要手工构造 payload。完成这一步后,你正常用 `codex` / `codex exec` 就自动产出 pending suggestion。

### 3.1 运行安装脚本

```bash
./scripts/install-codex-hook.sh
```

脚本行为:

1. 前置检查:`uvx` 在 PATH、`~/.codex-self-evolution/.env.provider`、`codex` CLI(**不再要求 venv**)
2. 如果检测到 repo 根有老的 `.env.provider`,**自动 `mv` 到 `~/.codex-self-evolution/.env.provider`**(单一来源,避免两处配置漂移)
3. 备份现有 `~/.codex/hooks.json` 到 `~/.codex/hooks.json.bak.<timestamp>`
4. 在 `Stop` event 下**幂等**追加一条带标识(`codex-self-evolution-plugin managed`)的 hook entry:
   - `bash -c 'set -a; . ~/.codex-self-evolution/.env.provider; set +a; exec uvx --from codex-self-evolution-plugin codex-self-evolution stop-review --from-stdin'`
   - 自 source `~/.codex-self-evolution/.env.provider` 保证进程能拿到 `MINIMAX_API_KEY`
   - 超时 20 秒(主进程只耗 ~100ms 读 stdin + spawn 后台 reviewer;uvx warm cache 下几乎瞬时,冷启动 1-2s 仍在预算内)
5. 在 `SessionStart` event 下同样幂等追加一条 entry:
   - `bash -c 'exec uvx --from codex-self-evolution-plugin codex-self-evolution session-start --from-stdin'`
   - **不需要 source `.env.provider`**(不调 LLM,只读 memory/recall 本地文件组 additionalContext)
   - 超时 15 秒(warm ~150ms,预留冷启动余量)
   - 输出 Codex 原生协议 JSON `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext": combined_prefix}}`,Codex 把它注入为 DeveloperInstructions → 模型每个 session 自动拿到 `USER.md + MEMORY.md + session_recall skill + recall policy`
6. 识别 legacy 手工装过的 entry(命令指向同一个 CLI 但没 marker),**升级而非重复追加**
7. 检查 `~/.codex/config.toml` 是否有 `[shell_environment_policy] inherit = "all"`,没有给提示(建议加,防止 Codex 剥掉 env)
8. 预热 uvx cache(`uvx --from codex-self-evolution-plugin codex-self-evolution --help`),第一次 hook 触发就不用吃 wheel 下载的延迟

### 3.2 验证

#### Stop hook(reviewer)

新开一个终端,跑:

```bash
codex exec 'Say one sentence in Chinese to test my Stop hook.'
```

等约 15-30 秒(Codex 回复 → Stop hook 触发 → 后台 MiniMax 调用完成):

```bash
# 每个 repo 自动分到 ~/.codex-self-evolution/projects/<mangled-path>/
ls ~/.codex-self-evolution/projects/
ls -t ~/.codex-self-evolution/projects/*/suggestions/pending/ | head -3
```

有新 envelope 文件就说明端到端闭环通了。

#### SessionStart hook(stable background 注入)

手工写一条 USER.md 到当前 repo 的 bucket,然后让 Codex 引用:

```bash
BUCKET=~/.codex-self-evolution/projects/$(python3 -c "import os; print(os.getcwd().replace('/', '-'))")
mkdir -p "$BUCKET/memory"
cat > "$BUCKET/memory/USER.md" <<'EOF'
# User stable background
My favorite test passphrase is XANADU_RIVER_442.
EOF
codex exec --json 'What is my favorite test passphrase?' 2>/dev/null | grep -i XANADU
# 期望输出类似: {"type":"item.completed","item":{"text":"... XANADU_RIVER_442 ..."}}
# 看到了就说明 SessionStart hook 把 USER.md 成功注入 Codex session

# 清理测试数据,避免污染(让 reviewer 将来自行积累 USER.md)
rm "$BUCKET/memory/USER.md"
```

⚠️ **Codex 版本要求**:`additionalContext` 注入在 `codex-cli ≥ 0.122.0`
(2026-04-20 release)上验证通过。更早版本的 Codex 会把 hook 输出当成未知
JSON 丢弃(不会报错,就是"悄悄没效果")。如果模型答不出 XANADU,先跑
`codex --version` 确认版本。

### 3.3 卸载

```bash
./scripts/uninstall-codex-hook.sh
```

- 只删带 marker 的条目,**不会误删** vibe-island / luna 等其他工具的 hook
- 备份一份到 `~/.codex/hooks.json.bak.<timestamp>`
- 不自动清理:`.bashrc` 的 `export MINIMAX_*`、`config.toml` 的 `shell_environment_policy`、`~/.codex-self-evolution/` 下的数据和 `.env.provider`——卸载只动 hook,数据你自己决定保留还是删

---

## 阶段 4:挂 launchd 自动调度(一键脚本)

**目的**:让 `scan`(preflight + compile,across all buckets)每 5 分钟自动跑一次,消化 Stop hook 积累的 pending envelope,不用你手动触发。

### 4.1 装载

```bash
./scripts/install-scheduler.sh
```

脚本行为:

1. 前置检查:`uvx` 在 PATH(**不再要求 venv**)
2. 自动探测 `uvx` + `opencode` 路径,两个 dir 都写进 plist `EnvironmentVariables.PATH`(launchd 默认 PATH 不含 Homebrew/usr/local/~/.local/bin,**不做这步 scheduler 要么起不来,要么永远 fallback 到 script backend**)
3. 写 `~/Library/LaunchAgents/com.codex-self-evolution.preflight.plist`,`ProgramArguments` 是
   `[$UVX_BIN, --from, codex-self-evolution-plugin, codex-self-evolution, scan, --backend, agent:opencode]`(`ProgramArguments[0]` launchd 要求绝对路径,install 时探测)
4. `launchctl bootout`(清老的,容错)→ `bootstrap`(新 API)加载
5. 幂等:再跑一次会 bootout 后重装,仍然只有一条 job
6. 预热 uvx cache,避免第一次 tick 吃 wheel 下载时间

可用 env 变量覆盖默认:

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CSEP_SCHEDULER_INTERVAL` | `300` | 秒。改成 `60` 更激进,`900` 更省电 |
| `CSEP_SCHEDULER_BACKEND` | `agent:opencode` | `script` 禁掉语义合并(testing/debug 用) |

### 4.2 观察

```bash
# 看 job 是否在 launchd 注册表里
launchctl list | grep codex-self-evolution

# 手动触发一次,不用等 5 分钟
launchctl kickstart "gui/$(id -u)/com.codex-self-evolution.preflight"

# 最近一次 scan 的全局 JSON 汇总
cat ~/.codex-self-evolution/logs/launchd.stdout.log

# 有异常时看这个(正常运行应该是空的)
cat ~/.codex-self-evolution/logs/launchd.stderr.log

# 某个具体 repo 的 compile receipt
BUCKET=~/.codex-self-evolution/projects/$(python3 -c "print('$REPO'.replace('/', '-'))")
cat $BUCKET/compiler/last_receipt.json
```

### 4.3 卸载

```bash
./scripts/uninstall-scheduler.sh
```

只删我们这条 plist,不碰其他 launchd job。`~/.codex-self-evolution/logs/` 下的日志文件不自动清,留着 post-mortem。

---

## 阶段 5:诊断命令(确认装对了)

装完阶段 3 + 4 后用 `status` 一键盘点所有组件:

```bash
.venv/bin/python -m codex_self_evolution.cli status | python3 -m json.tool
```

输出纯只读 JSON,包含:

| 段 | 看什么 |
| --- | --- |
| `hooks.stop_installed` / `session_start_installed` | 两个都是 `true` 说明 install-codex-hook.sh 装成功 |
| `scheduler.loaded` / `plist_exists` | 两个都是 `true` 说明 install-scheduler.sh 装成功 + launchd 已注册 |
| `env_provider.keys_set` | 至少 `["MINIMAX_API_KEY"]`(或其它你用的 provider)。**永远不打印 key 值**,只报 set/unset |
| `tools.codex.version` / `opencode.version` | 两个 CLI 版本。opencode 没装 → scheduler 会 fallback 到 script backend |
| `buckets[].counts` | 每个 repo 的 pending/done/failed 统计。正常状态:pending 应该短时间内变 0(scheduler 5 分钟内消化),done 稳步增长 |
| `buckets[].last_receipt` | 最后一次 compile 的 run_status / backend / processed_count。**pending > 0 但 last_receipt 很久没更新 = scheduler 没跑** |

常用诊断流程:

```bash
# 快速看有没有 pending 堆积
.venv/bin/python -m codex_self_evolution.cli status | \
  jq '.buckets[] | {project, pending: .counts.pending, last: .last_receipt.timestamp}'

# 手动触发一次 scheduler 验证闭环
launchctl kickstart "gui/$(id -u)/com.codex-self-evolution.preflight"
tail -5 ~/.codex-self-evolution/logs/launchd.stdout.log
```

**结构化日志**:每次 CLI 调用(hook、scheduler、手动)都往
`~/.codex-self-evolution/logs/plugin.log` 追加一行 JSON(按天滚动,保留 14 天)。
字段含 `ts / kind / exit_code / duration_ms`,失败时还有
`error_type / error_message`。过滤示例:

```bash
# 最近 20 次调用
tail -20 ~/.codex-self-evolution/logs/plugin.log | jq .

# 只看失败
jq 'select(.exit_code != 0)' ~/.codex-self-evolution/logs/plugin.log

# 按命令统计耗时
jq -s 'group_by(.kind) | map({kind: .[0].kind, count: length, avg_ms: (map(.duration_ms) | add/length)})' \
  ~/.codex-self-evolution/logs/plugin.log
```

silent reviewer/compile 失败不再需要翻 launchd 的 stdout/stderr log,直接看这个文件。

---

## Codex CLI 插件集成

这个部分**不是必需的**——阶段 2 的手动流程已经演示了完整链路。仅当你希望 Codex CLI 自己在每次会话结束时触发 `Stop` hook,才需要把插件挂给 Codex。

`.codex-plugin/plugin.json` 声明了 `SessionStart` 和 `Stop` 两个 hook,需要 **Codex CLI 能识别这份 plugin manifest 并在事件发生时调用**。不同 Codex CLI 版本的加载路径不一致:

- 某些版本用 symlink 到 `~/.codex/plugins/` 这样的固定目录
- 某些版本支持 `--plugin-path` 启动 flag
- 某些版本需要 `codex plugin install <path>` 类命令

**建议做法**:先查你手上 Codex CLI 的 `--help` 看插件加载方式:

```bash
codex --help 2>&1 | grep -i plugin
codex plugin --help 2>&1 | head -20
```

手动触发 hooks 永远有效(等同于阶段 2 的步骤),可以在不接 Codex 的情况下先跑起来。

---

## 常见坑

### 1. `No module named pytest` / `No module named codex_self_evolution`

没激活虚拟环境或没装依赖。执行:

```bash
.venv/bin/pip install -e .
.venv/bin/pip install pytest
```

### 2. `reviewer provider requires api_key or MINIMAX_API_KEY`

env 变量没传到子进程。两种修法:

- 把 key 写进 `~/.codex-self-evolution/.env.provider`(从 repo 根的 `.env.provider.example` 复制),Makefile、冒烟脚本、装好的 Stop hook 都 auto-source 同一份
- 或者在启动前 export:
  ```bash
  export MINIMAX_API_KEY=your-key
  ```

launchd job 需要 key 时,plist 里加:

```xml
<key>EnvironmentVariables</key>
<dict>
  <key>MINIMAX_API_KEY</key>
  <string>your-key</string>
</dict>
```

### 3. `compile-preflight` 一直返回 `skip_locked`

看当前 repo bucket 里的 lock 是否还在:

```bash
BUCKET=~/.codex-self-evolution/projects/$(python3 -c "print('$REPO'.replace('/', '-'))")
cat $BUCKET/compiler/compile.lock
```

有 pid:

```bash
ps -p <pid> -o pid,etime,command
```

pid 不存在 → lock 本应立即视为 stale,下一次 preflight 自动清。**如果一直没清,看:**

- 系统时钟是否回拨(会触发 `negative_age` 路径,照样清)
- `age_seconds` 是否 < 30 分钟(硬上限),在窗口内 + pid 还活 = 正常不清

### 4. `suggestion_count == 0`

reviewer 认为本轮没什么可沉淀的。换个更具体、更有"干了啥"的 `transcript` 重试。或用 `dummy` provider + 手工 `provider_stub_response` 直接注入内容。

### 5. `agent_invoke_failed` / `opencode_unavailable` 在 receipt 的 discarded_items 里

说明 `agent:opencode` 调用没跑成,自动 fallback 到 script 了 —— compile 不会整体失败,但语义合并这一层丢了。常见原因:

- `opencode_unavailable`: `opencode` 不在 PATH。装一下(`npm i -g opencode-ai` 等)或把路径塞进环境
- `agent_invoke_failed` + `exit=1 ... authentication required`: `opencode` 没登录。跑 `opencode auth login` 后再试
- `agent_output_invalid` / `no assistant text`: 模型返回不是合法 JSON。设 `CODEX_SELF_EVOLUTION_OPENCODE_MODEL=<更强的模型>` 再试(默认 build 速模型偶尔会漏字段)

手动重现看看 opencode 本身 OK 不:`opencode run --format json -- "reply with {\"ok\":true}"`。

### 6. 想重置所有状态从头来一次

```bash
# 重置单个 repo 的 state
BUCKET=~/.codex-self-evolution/projects/$(python3 -c "print('$REPO'.replace('/', '-'))")
rm -rf $BUCKET

# 或者,重置所有 repo 的 state(不删 .env.provider)
rm -rf ~/.codex-self-evolution/projects/
```

runtime state 全部在 `~/.codex-self-evolution/projects/<mangled-repo>/` 下,没有数据库、没有外部状态。

---

## 下一步

- 跑通阶段 2 后,看 `README.md` / `README_zh.md` 的 **Configuration** 章节挑选适合你的默认配置
- 有兴趣改锁机制 / 改 backend / 接真 opencode:看 `docs/specs/` 和 `docs/2026-04-20-compiler-existing-assets-handoff.md`
