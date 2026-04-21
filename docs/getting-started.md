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
6. [Codex CLI 插件集成](#codex-cli-插件集成)
7. [常见坑](#常见坑)

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
# 1. 定义:被插件“记忆”的目标 repo,以及状态目录
REPO=/path/to/your/target/repo          # 可以就填本仓库本身,做 self-hosting
STATE=$REPO/data
```

建议 `$REPO` 就填这个插件自身的路径作为 playground:

```bash
REPO=/Users/$USER/code/github/codex-self-evolution-plugin
STATE=$REPO/data
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
  --once --state-dir $STATE --backend script
```

> **为什么用 `script` 不用 `agent:opencode`**:opencode CLI 的真实 `run` 子命令接口(`message` 是位置参数、用 `--format json` 输出)跟当前占位默认命令 `opencode run --stdin-json --stdout-json` 不匹配,会触发 `agent_invoke_failed` → fallback 到 script。直接用 `script` 更干净。真实对接 opencode 需要一轮 prompt + 调用约定的工作,见 README 里的 "Compile backends" 章节。

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

1. 前置检查:Python 3.11+、venv、`.env.provider`、`codex` CLI
2. 备份现有 `~/.codex/hooks.json` 到 `~/.codex/hooks.json.bak.<timestamp>`
3. 在 `Stop` event 下**幂等**追加一条带标识(`codex-self-evolution-plugin managed`)的 hook entry:
   - `bash -c 'set -a; . .env.provider; set +a; exec .venv/bin/python -m codex_self_evolution.cli stop-review --from-stdin'`
   - 自 source `.env.provider` 保证进程能拿到 `MINIMAX_API_KEY`
   - 超时 10 秒(主进程只耗 ~100ms,真正 reviewer 调用在后台 subprocess 异步,不阻塞 Codex)
4. 识别 legacy 手工装过的 entry(命令指向同一个 CLI 但没 marker),**升级而非重复追加**
5. 检查 `~/.codex/config.toml` 是否有 `[shell_environment_policy] inherit = "all"`,没有给提示(建议加,防止 Codex 剥掉 env)

### 3.2 验证

新开一个终端,跑:

```bash
codex exec 'Say one sentence in Chinese to test my Stop hook.'
```

等约 15-30 秒(Codex 回复 → Stop hook 触发 → 后台 MiniMax 调用完成):

```bash
ls -t data/suggestions/pending/ | head -3
```

有新 envelope 文件就说明端到端闭环通了。

### 3.3 卸载

```bash
./scripts/uninstall-codex-hook.sh
```

- 只删带 marker 的条目,**不会误删** vibe-island / luna 等其他工具的 hook
- 备份一份到 `~/.codex/hooks.json.bak.<timestamp>`
- 不自动清理:`.bashrc` 的 `export MINIMAX_*`、`config.toml` 的 `shell_environment_policy`、仓库的 `.venv` 和 `.env.provider`——它们可能是你其他工具共用的,脚本不碰

---

## 阶段 4:挂 launchd 自动调度(5 分钟)

**目的**:让 `compile-preflight → compile` 每 5 分钟自动跑一次,消化阶段 3 产出的 pending envelope,不用你手动触发。

### 4.1 生成你本机的 plist

模板在 `docs/launchd/com.codex-self-evolution.preflight.plist`。复制一份出来填空:

```bash
cp docs/launchd/com.codex-self-evolution.preflight.plist \
   /tmp/com.codex-self-evolution.preflight.plist
```

编辑 `/tmp/com.codex-self-evolution.preflight.plist`,**必改的 4 处**:

| 占位 | 替换为 | 说明 |
| --- | --- | --- |
| `/ABSOLUTE/PATH/TO/codex-self-evolution-plugin` | 你的仓库绝对路径 | 出现 4 次 |
| `/Users/haha/hermes-agent/venv/bin/python3.11` | 你的 `.venv/bin/python` 绝对路径 | 出现 2 次 |
| `--state-dir data` | 如需自定义 state 路径才改 | 保持 `data` 就写 `$REPO/data` |
| `StartInterval` | 选一个:`60` / `300`(默认) / `900` | 唤醒间隔秒数 |

同时把 `--backend agent:opencode` 改成 `--backend script`(原因同 2.5):

```xml
-m codex_self_evolution.cli compile --once --state-dir data --backend script
```

### 4.2 装载 launchd job

```bash
# 拷到 ~/Library/LaunchAgents/(用户级,不需要 sudo)
cp /tmp/com.codex-self-evolution.preflight.plist \
   ~/Library/LaunchAgents/

# 装载并立即触发一次
launchctl load ~/Library/LaunchAgents/com.codex-self-evolution.preflight.plist
launchctl start com.codex-self-evolution.preflight
```

### 4.3 观察

```bash
# 看 job 是否在 launchd 注册表里
launchctl list | grep codex

# 看 preflight 最近一次的输出
cat $REPO/data/scheduler/last-preflight.json

# 看 stdout / stderr 日志
tail -f $REPO/data/scheduler/launchd.stdout.log
tail -f $REPO/data/scheduler/launchd.stderr.log

# 看 compile receipt
cat $REPO/data/compiler/last_receipt.json
```

### 4.4 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.codex-self-evolution.preflight.plist
rm ~/Library/LaunchAgents/com.codex-self-evolution.preflight.plist
```

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

- 把 key 写进 `.env.provider`(从 `.env.provider.example` 复制),Makefile 和冒烟脚本会 auto-source
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

看 `$STATE/compiler/compile.lock` 是否还在:

```bash
cat $REPO/data/compiler/compile.lock
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

正常:当前 `agent:opencode` 默认命令跟 opencode 1.4.0 实际 CLI 不匹配。fallback 到 script 的流程是被测过的,不影响结果。用 `--backend script` 更干净。

### 6. 想重置所有状态从头来一次

```bash
rm -rf $REPO/data
```

runtime state 全部在 `data/` 下,没有数据库、没有外部状态。

---

## 下一步

- 跑通阶段 2 后,看 `README.md` / `README_zh.md` 的 **Configuration** 章节挑选适合你的默认配置
- 有兴趣改锁机制 / 改 backend / 接真 opencode:看 `docs/specs/` 和 `docs/2026-04-20-compiler-existing-assets-handoff.md`
