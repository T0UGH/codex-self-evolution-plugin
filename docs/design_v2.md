# Design v2 — Unified Config + Multi-Provider Reviewer + Subprocess Backend

**Status**: Approved(2026-04-22) · **Owner**: T0UGH · **Date**: 2026-04-22
**Target release**: v0.6.0 · **Supersedes**: nothing (v1 was implicit in scattered code)

---

## 0. TL;DR

让更多人用起来,要做三件相互耦合的事:

1. **Reviewer 支持更多模型**:`HTTPReviewProvider` 扩出完整的厂商 base URL 对照清单(Gemini/GLM/DeepSeek/Qwen/Kimi),`.env.provider.example` 每个都给示例,README 增加价格对照 + 选型指南。
2. **Subprocess reviewer 作为"无 API key"路径**:新增 `SubprocessReviewProvider`,让 reviewer 可以调用本机已登录的 CLI(`codex` / `opencode` / `claude`),免费蹭订阅、合法合规。
3. **集中配置到 `~/.codex-self-evolution/config.toml`**:把散落在环境变量、CLI 参数、代码常量里的 behavior 配置都收拢到一个 TOML 文件。API key 仍独占 `.env.provider`(安全隔离)。

完成后用户体验:一个 `config.toml` 写完想要的 provider/model/backend,一张 `.env.provider` 放 key,`codex-self-evolution config show` 验证最终生效值。

---

## 1. 背景与动机

### 1.1 现状:配置一团散

截至 v0.5.2,一个用户要把插件跑起来得关心这些地方:

| 设置 | 配置位置 | 类型 |
|---|---|---|
| 选 reviewer provider | Codex hook payload 里的 `reviewer_provider` 字段(经 `map_codex_stop_payload` 硬编码) | 代码硬编码 |
| `MINIMAX_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | `~/.codex-self-evolution/.env.provider` | env file |
| `MINIMAX_REGION` / `MINIMAX_BASE_URL` | `.env.provider` | env file |
| `MINIMAX_REVIEW_MODEL` / `OPENAI_REVIEW_MODEL` / `ANTHROPIC_REVIEW_MODEL` | `.env.provider` | env file |
| `CODEX_SELF_EVOLUTION_OPENCODE_COMMAND` | env | env |
| `CODEX_SELF_EVOLUTION_OPENCODE_MODEL` / `_AGENT` | env | env |
| Compile backend 选 script / agent:opencode | CLI `--backend` flag + 代码 default | CLI flag |
| Scheduler 默认 backend | launchd plist `ProgramArguments`(装时生成) + CLI flag | 系统文件 |
| `CODEX_SELF_EVOLUTION_HOME` | env | env |
| Reviewer max_tokens / timeout | `review/providers.py` 代码常量 | 硬编码 |
| Reviewer max_retries / backoff | `review/providers.py` 代码常量 | 硬编码 |
| Compile lock timeout | `config.py` 代码常量 | 硬编码 |
| Log retention | `logging_setup.py` 代码常量 | 硬编码 |
| Hook 是否启用 | `~/.codex/hooks.json`(install-codex-hook.sh 生成) | 系统文件 |
| Schedule interval | launchd plist `StartInterval` | 系统文件 |

**用户感知**:调个 MiniMax 换 Gemini 要动 2-3 个 env var、改 install 脚本,还不知道改完是不是真生效。

### 1.2 为什么现在做

- 前面几个 PhaseMEMORY dedup / worktree 归并 / observability 都已经稳了,是时候回头把 **安装和配置 UX** 清理一遍。
- 实机数据显示 MiniMax 43% 失败率(HTTP 529 过载为主),**单一 provider 是实实在在的风险**。多 provider 能当备胎。
- 用户想推广给"更多人":零 API key 门槛的 subprocess reviewer 是最有用的拓展点。但不先把配置统一,加 subprocess 会让散乱更糟。

### 1.3 非目标

本 design **不做**以下事情:

- 不做 WebUI / TUI 配置工具(目前命令行够)
- 不做 cross-machine config sync(用户可自行 Git / rsync `~/.codex-self-evolution/`)
- 不自动从 `.env.provider` 迁移到 config.toml(保留后兼,env vars 仍能覆盖)
- 不做 Gemini native API 的 dialect(他们官方提供 OpenAI-compat 端点,短期没必要)
- 不重写 opencode compile backend(它运行得好)
- 不重构 hooks.json 的管理(install-codex-hook.sh 没问题)
- 不切 YAML,坚持 TOML(Python 3.11+ stdlib 有 `tomllib`,零 deps 是 plugin 的卖点)

---

## 2. 设计目标

按重要性降序:

1. **单一事实源**:一个文件能回答"reviewer 现在用什么 provider / model / base_url"
2. **零新依赖**:配置读写用 stdlib `tomllib`(read)/ 手写 `tomli_w` 式 minimal writer(write)
3. **后向兼容**:现有用 env var 配置的用户升级到 0.6.0 **无需任何动作**,行为不变
4. **显式优先级**:CLI args > env vars > config.toml > built-in defaults,可打印验证
5. **安全**:API key 继续只放 `.env.provider`,config.toml **不许存密钥**(代码层强制)
6. **可扩展**:新加一个 provider(比如未来想加 Claude Code CLI subprocess)只要几十行 + 一个 config 字段
7. **可观察**:`config show` / plugin.log 都能看到"最终用的是什么"

---

## 3. 配置架构

### 3.1 文件布局

```
~/.codex-self-evolution/
├── config.toml              # 主配置(behavior)  <-- NEW
├── .env.provider            # 仅 API keys + provider 专属 secret env
├── .env.provider.example    # 模板,列各家 base URL 示例  <-- UPDATED
├── projects/
│   └── <mangled-cwd>/
│       └── ...
├── logs/
│   └── plugin.log
└── skills/ (等)
```

**约定**:

- `config.toml` 不存在时,完全用内置默认值(零配置开箱用 MiniMax)
- `config.toml` 存在但字段缺失时,缺的字段用默认(容错,不强求用户写全)
- `.env.provider` 只存形如 `FOO_API_KEY=...` 的密钥,其他配置写在 `config.toml`。**如果 `.env.provider` 里出现非密钥 key(例如 `MINIMAX_REVIEW_MODEL`),继续工作但 log warning 提醒迁移**。

### 3.2 TOML schema(完整定义)

```toml
# Schema version — bump when making breaking field renames.
# Loader tolerates unknown keys so forward-compat users aren't stranded,
# but refuses to load a newer schema than it understands.
schema_version = 1

# =====================================================================
# [reviewer] — Stop hook 背景审查代理的配置
# =====================================================================
[reviewer]
# provider 决定如何调 reviewer。合法值:
#   "minimax"           — HTTP POST to MiniMax (Anthropic-style endpoint)
#   "openai-compatible" — HTTP POST to any OpenAI-compat endpoint
#   "anthropic-style"   — HTTP POST to any Anthropic-style endpoint
#   "codex-cli"         — Subprocess: local `codex exec --json`
#   "opencode-cli"      — Subprocess: local `opencode run --format json`
#   "dummy"             — For tests; returns stubbed response
# 每个 provider 有不同的默认 model / base_url / command。
provider = "minimax"

# model:provider-specific 模型名。空字符串 → 用 provider 内置默认。
# minimax         → "MiniMax-M2.7"
# openai-compatible → "gpt-4.1-mini"
# anthropic-style → "claude-3-5-haiku-latest"
# codex-cli       → (由 codex 自己决定;这里通常不填)
# opencode-cli    → (由 opencode 配置决定;这里通常不填)
model = ""

# base_url:HTTP 端点 URL。空字符串 → 用 provider 内置默认。
# 多 provider 切换的主要抓手。示例:
#   Gemini   → "https://generativelanguage.googleapis.com/v1beta/openai"
#   GLM      → "https://open.bigmodel.cn/api/paas/v4"
#   DeepSeek → "https://api.deepseek.com/v1"
#   Qwen     → "https://dashscope.aliyuncs.com/compatible-mode/v1"
#   Kimi     → "https://api.moonshot.cn/v1"
#   MiniMax  → null(由 MINIMAX_REGION 决定 global vs cn)
base_url = ""

# API 超时、token 上限、重试策略
timeout_seconds = 30
max_tokens = 4096
max_retries = 2

# 重试 backoff(秒数列表)。max_retries=2 → [first_backoff, second_backoff]。
# 数组长度必须 >= max_retries。
retry_backoff = [2.0, 5.0]


# =====================================================================
# [reviewer.subprocess] — 只在 provider = codex-cli / opencode-cli 时生效
# =====================================================================
[reviewer.subprocess]
# command:子进程 argv。空数组 → 用 provider 的内置默认:
#   codex-cli    → ["codex", "exec", "--json", "--skip-git-repo-check"]
#   opencode-cli → ["opencode", "run", "--format", "json", "--dangerously-skip-permissions"]
# 自定义场景:可填 ["claude", "--output-format", "json"] 等。
command = []

# payload_mode:怎么把 review snapshot 送给子进程:
#   "file"   — 写到临时文件,路径传给 CLI(opencode 模式)
#   "stdin"  — 从 stdin 读 JSON(codex 模式)
#   "inline" — 序列化后塞进 prompt 末尾(简单但易爆长度)
payload_mode = "stdin"

# response_format:CLI 输出的解析方式。
#   "codex-events"    — `codex exec --json` 的 event stream
#   "opencode-events" — `opencode run --format json` 的 event stream
#   "raw-json"        — CLI 直接 print 一个 JSON object(简单 CLI)
response_format = "codex-events"

# 子进程超时。reviewer 在 Stop hook 后台跑,超时时间不阻塞主流程。
timeout_seconds = 90


# =====================================================================
# [compile] — 把 reviewer 产出的 suggestion 编译成 memory/recall/skills
# =====================================================================
[compile]
# backend:"script"(本地 dedup 脚本) | "agent:opencode"(LLM 合并,更智能)
backend = "agent:opencode"

# 当 agent backend 失败时是否自动 fallback 到 script。
# 建议保持 true,否则 opencode 一点抖动整个管道就停。
allow_fallback = true


# =====================================================================
# [compile.opencode] — compile backend = agent:opencode 时生效
# =====================================================================
[compile.opencode]
# 空字符串 → 用 opencode 自己的默认 model(通常读 ~/.config/opencode/opencode.json)
model = ""
agent = ""

# 子进程超时上限。务必 < 30 分钟(compile 锁的 stale 阈值)
timeout_seconds = 900


# =====================================================================
# [scheduler] — launchd 定时任务的默认参数
# =====================================================================
[scheduler]
# scan 命令默认 backend(被 launchd 启动时)
backend = "agent:opencode"

# scan 间隔(秒)。注意:改这里不影响已装的 plist,需要重装 scheduler。
# 目前纯文档用途,plist 里 StartInterval 仍是权威。
interval_seconds = 300


# =====================================================================
# [log] — plugin.log 配置
# =====================================================================
[log]
retention_days = 14
```

### 3.3 优先级规则

给定一个配置项(如 `reviewer.model`),最终生效值按以下顺序解析,**首个非空的胜出**:

```
优先级(高 → 低):
1. CLI args                   codex-self-evolution compile --reviewer-model=X
2. process env vars           MINIMAX_REVIEW_MODEL=X (legacy) 或
                              CODEX_SELF_EVOLUTION_REVIEWER_MODEL=X (new)
3. ~/.codex-self-evolution/config.toml   [reviewer] model = "X"
4. built-in default           provider 自己的默认
```

**关键决策**:**legacy env vars 保留**(`MINIMAX_REVIEW_MODEL` 等),只是降优先级到"比 config.toml 高"。老用户升级后行为不变。新用户推荐全写 config.toml。

### 3.4 Schema versioning

`schema_version = 1` 作为顶层字段。未来如果某次版本做**破坏性重命名**(比如 `reviewer.model` 改名 `reviewer.model_id`),写 migration script 并 bump 到 `schema_version = 2`。Loader 读到未知 version 时报错而不是"我猜猜看",防止 silently misinterpret。

未来加字段不算 breaking(loader 已做 tolerate-unknown-keys),不 bump version。

---

## 4. Provider 系统

### 4.1 HTTP providers(扩展现有)

保留 `HTTPReviewProvider` 的 3 个 dialect 不变(`openai` / `anthropic` / `minimax`),只在 `provider` → `dialect` 的映射上做加法:

| config `provider` | dialect | 默认 base URL | 默认 model |
|---|---|---|---|
| `minimax` | minimax | `$MINIMAX_REGION`-based | `MiniMax-M2.7` |
| `openai-compatible` | openai | `$OPENAI_BASE_URL` 或 `https://api.openai.com/v1/chat/completions` | `gpt-4.1-mini` |
| `anthropic-style` | anthropic | `$ANTHROPIC_BASE_URL` 或 `https://api.anthropic.com/v1/messages` | `claude-3-5-haiku-latest` |

**主流便宜模型都走 `openai-compatible`**,只是 `base_url` 指向不同域名。不新增代码,改 prompt + 文档 + `.env.provider.example`。

### 4.2 Subprocess providers(新增)

新类 `SubprocessReviewProvider`,实现 `ReviewProvider` protocol:

```python
class SubprocessReviewProvider:
    name: str  # e.g. "codex-cli" or "opencode-cli" or a custom label

    def __init__(self, argv: list[str], payload_mode: str, response_format: str,
                 timeout: float): ...

    def run(self, snapshot: dict, prompt: str, options: dict) -> ProviderResult:
        # 1. 按 payload_mode 决定怎么送 snapshot:
        #    file   → tempfile + --file 附加
        #    stdin  → json.dumps 从 stdin 输入
        #    inline → append 到 prompt 里
        # 2. subprocess.run(argv, ...) with timeout
        # 3. 按 response_format 解析 stdout:
        #    codex-events     → parse codex event stream, 聚合 text parts
        #    opencode-events  → reuse _extract_assistant_text 逻辑
        #    raw-json         → 假设 stdout 就是一个 JSON object
        # 4. cleanup tempfile
        # 5. 返回 ProviderResult(raw_text=..., provider=self.name)
```

关键技术点:

- **argv 起点**:`argv[0]` 必须在 PATH 里。Loader 在启动时 `shutil.which(argv[0])` 预检,失败立即报错不等到 reviewer 触发。
- **事件解析复用**:codex 和 opencode 的 event stream shape 差不多(都有 `{"type":"text","text":"..."}`),公共解析器放在 `provider_utils.py`。
- **stdin 模式下重试**:每次 retry 都要 reconstruct subprocess + 重发 stdin。不能 reuse 已关闭的 Popen。
- **timeout 处理**:`subprocess.run(timeout=)` 会 raise TimeoutExpired。我们转换成 `ReviewProviderError("subprocess reviewer timed out")`,走跟 HTTP retry 一样的重试逻辑(`_RETRYABLE_STATUS_CODES` 机制 + 新增 timeout 类别)。

### 4.3 Provider factory

在 `review/runner.py` 的 `run_reviewer()` 里:

```python
def run_reviewer(snapshot, ..., config: PluginConfig | None = None):
    config = config or load_config()
    reviewer_cfg = config.reviewer.resolved()  # merge env/defaults
    provider = _build_provider(reviewer_cfg)
    ...

def _build_provider(cfg: ResolvedReviewerConfig) -> ReviewProvider:
    if cfg.provider == "dummy":
        return DummyReviewProvider()
    if cfg.provider in {"minimax", "openai-compatible", "anthropic-style"}:
        return HTTPReviewProvider(name=cfg.provider, dialect=_DIALECT_MAP[cfg.provider])
    if cfg.provider == "codex-cli":
        argv = cfg.subprocess.command or ["codex", "exec", "--json", "--skip-git-repo-check"]
        return SubprocessReviewProvider(
            name="codex-cli", argv=argv,
            payload_mode=cfg.subprocess.payload_mode,
            response_format=cfg.subprocess.response_format,
            timeout=cfg.subprocess.timeout_seconds,
        )
    if cfg.provider == "opencode-cli":
        argv = cfg.subprocess.command or ["opencode", "run", "--format", "json",
                                           "--dangerously-skip-permissions"]
        return SubprocessReviewProvider(...)
    raise ReviewProviderError(f"unknown provider: {cfg.provider}")
```

---

## 5. 公开 API / CLI surface

### 5.1 新 subcommands

```bash
codex-self-evolution config show          # 打印解析后的完整配置(已 merge env)
codex-self-evolution config show --raw    # 只打印 config.toml 内容(未 merge)
codex-self-evolution config init          # 生成 config.toml 骨架到 ~/.codex-self-evolution/
codex-self-evolution config validate      # 校验 config.toml 语法 + 字段合法性
codex-self-evolution config path          # 打印 config.toml 所在路径
```

**`config show` 输出示例**:

```json
{
  "config_path": "/Users/alice/.codex-self-evolution/config.toml",
  "config_exists": true,
  "schema_version": 1,
  "resolved": {
    "reviewer": {
      "provider": "codex-cli",
      "provider_source": "config.toml",
      "model": "",
      "model_source": "default",
      "base_url": "",
      "base_url_source": "default",
      "timeout_seconds": 30,
      "max_retries": 2,
      "subprocess": {
        "command": ["codex", "exec", "--json", "--skip-git-repo-check"],
        "command_source": "provider_default",
        "payload_mode": "stdin",
        "response_format": "codex-events"
      }
    },
    "compile": { ... },
    "scheduler": { ... }
  }
}
```

每个字段都带 `_source` 兄弟字段,明确告诉用户"这个值是从哪来的"(`"config.toml"` / `"env:MINIMAX_REVIEW_MODEL"` / `"default"` / `"provider_default"`)。彻底消除"我到底读了哪个值"悬念。

### 5.2 环境变量映射表

| 新 env var | 旧 env var | config.toml 字段 |
|---|---|---|
| `CODEX_SELF_EVOLUTION_REVIEWER_PROVIDER` | — | `reviewer.provider` |
| `CODEX_SELF_EVOLUTION_REVIEWER_MODEL` | `MINIMAX_REVIEW_MODEL` / `OPENAI_REVIEW_MODEL` / `ANTHROPIC_REVIEW_MODEL` | `reviewer.model` |
| `CODEX_SELF_EVOLUTION_REVIEWER_BASE_URL` | `MINIMAX_BASE_URL` / `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` | `reviewer.base_url` |
| `CODEX_SELF_EVOLUTION_REVIEWER_TIMEOUT` | — | `reviewer.timeout_seconds` |
| `CODEX_SELF_EVOLUTION_REVIEWER_COMMAND` | `CODEX_SELF_EVOLUTION_OPENCODE_COMMAND` | `reviewer.subprocess.command` |
| `CODEX_SELF_EVOLUTION_COMPILE_BACKEND` | — | `compile.backend` |
| `CODEX_SELF_EVOLUTION_OPENCODE_MODEL` | (同名,保留) | `compile.opencode.model` |
| `CODEX_SELF_EVOLUTION_OPENCODE_AGENT` | (同名,保留) | `compile.opencode.agent` |

旧 env var 继续识别,但 `config show` 里 `source` 会显示 `"env:MINIMAX_REVIEW_MODEL (legacy)"`,提示考虑迁移。

### 5.3 示例 workflow

**场景 1:换用 DeepSeek(10x 便宜于 MiniMax)**

```bash
# 1. 加 DeepSeek key 到 .env.provider
echo "DEEPSEEK_API_KEY=sk-..." >> ~/.codex-self-evolution/.env.provider

# 2. 写 config.toml
cat > ~/.codex-self-evolution/config.toml <<EOF
schema_version = 1
[reviewer]
provider = "openai-compatible"
model = "deepseek-chat"
base_url = "https://api.deepseek.com/v1"
EOF

# 3. 把 DEEPSEEK_API_KEY 映射成 OPENAI_API_KEY(HTTP provider 从这读)
# 或:在 .env.provider 里直接用 OPENAI_API_KEY=<deepseek key>
# 推荐后者,更直接
echo "OPENAI_API_KEY=sk-..." >> ~/.codex-self-evolution/.env.provider

# 4. 验证
codex-self-evolution config show
# → resolved.reviewer.provider = "openai-compatible"
#   resolved.reviewer.base_url = "https://api.deepseek.com/v1"
```

**场景 2:完全免 API key,用已登录的 codex CLI**

```bash
# 1. 确保 codex CLI 已 auth
codex auth login

# 2. 切 provider
cat > ~/.codex-self-evolution/config.toml <<EOF
schema_version = 1
[reviewer]
provider = "codex-cli"
EOF

# 3. 验证
codex-self-evolution config show
# → resolved.reviewer.provider = "codex-cli"
#   resolved.reviewer.subprocess.command = ["codex", "exec", "--json", "--skip-git-repo-check"]
```

无需任何 API key。

---

## 6. 后向兼容与迁移

### 6.1 现有 env var 行为

**保留以下 env vars 并继续识别**(不做 remove, 只做 "soft deprecation"):

- `MINIMAX_API_KEY`, `MINIMAX_REGION`, `MINIMAX_BASE_URL`, `MINIMAX_REVIEW_MODEL`
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_REVIEW_MODEL`
- `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_REVIEW_MODEL`, `ANTHROPIC_VERSION`
- `CODEX_SELF_EVOLUTION_HOME`
- `CODEX_SELF_EVOLUTION_OPENCODE_COMMAND` / `_OPENCODE_MODEL` / `_OPENCODE_AGENT`

**Dep warning**:loader 运行时 detect 到"legacy env var + config.toml 对应字段都存在"时 log warning:

```
WARN: MINIMAX_REVIEW_MODEL is set but config.toml also has [reviewer] model.
      config.toml is being overridden by the env var. Consider removing the
      env var once you've confirmed the config.toml value is correct.
```

### 6.2 升级路径

0.5.x → 0.6.0 的场景:

1. **已有 env vars,没有 config.toml**(最常见):0.6.0 加载时检测不到 config.toml → 全用 env 或 defaults → 行为完全不变。零动作升级。

2. **已有 env vars,要清理**:用户跑 `codex-self-evolution config init --from-env` → 生成 config.toml,把当前 env override 值固化进去。然后手动 `unset` env 或删 .env.provider 里对应行。

3. **新安装**:跑 `config init` 生成骨架;或直接从 `config.toml.example` 复制。

### 6.3 Deprecation timeline

- **v0.6.0**(本次):legacy env vars 100% 支持,不打 warning
- **v0.7.0**:legacy env vars 支持 + detect 冲突时 log warning
- **v1.0.0**(遥远):考虑真 remove 部分 env vars。实际上可能永远不 remove,因为 env 是 Unix 主流约定。

---

## 7. 安全

### 7.1 Key 隔离原则

**硬性规则**:`config.toml` 不许出现任何看起来像 API key 的字段。加载时做 linter 检查:

```python
_KEY_LOOKALIKE_RE = re.compile(r"_?(api_key|token|secret|password)$", re.IGNORECASE)

def _lint_no_keys_in_config(cfg: dict) -> list[str]:
    """递归扫所有叶子键,发现类 key 字段 → warn(不阻止加载)。"""
    warnings = []
    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if _KEY_LOOKALIKE_RE.search(k):
                    warnings.append(f"config.toml[{path}.{k}] looks like an API key; "
                                    "keys should live in .env.provider, not config.toml")
                walk(v, f"{path}.{k}")
    walk(cfg)
    return warnings
```

Config 里禁止存 key 的理由:

1. `config.toml` 用户可能 commit 进 dotfiles 或 git 仓库,`.env.provider` 不会
2. diagnostic 命令(`config show`)会打印 config 全文,如果存 key 会暴露
3. key 应该是 env (Unix 约定)既能 env file 也能 shell export,`.env.provider` 已经是这个定位

### 7.2 Subprocess 命令注入防护

`subprocess.run(argv, ...)` 只收 list,不用 shell。但 `reviewer.subprocess.command` 是用户填的 TOML 数组,需要:

- **不允许空 argv**:loader 拒绝 `command = []` 但 provider = `codex-cli`(因为没有 fallback 默认……这与上面描述矛盾,要修订:空数组走 provider 默认 argv,非空数组直接用,不做 shell escape)
- **不对字符串做 split**:不支持 `command = "codex exec"`(会产生分割歧义)。必须是 list。
- **argv[0] 预检**:`shutil.which(argv[0])` 失败时 loader 报错

用户想传 shell 表达式的场景几乎不存在;如果真有需求,让他写 `["bash", "-c", "..."]`,责任自负。

---

## 8. 观察性

### 8.1 `config show` 输出形态

已在 §5.1 详述。关键是每个字段带 `_source` 兄弟字段,说明这个值是从哪个层级解析来的。

### 8.2 plugin.log 增加字段

每次 reviewer 调用的 log 行加:

```json
{"kind":"stop-review", "reviewer_provider":"codex-cli",
 "reviewer_provider_source":"config.toml",
 "reviewer_model":"", "reviewer_base_url":"",
 "suggestion_count":3, ...}
```

`reviewer_provider_source` 让日志也能回答"我是怎么跑到这个 provider 的"。

### 8.3 `status` 输出集成

`recent_activity.stop_review` 里再加一个维度:

```json
{
  "stop_review": {
    "total": 54,
    "succeeded": 31,
    ...,
    "by_provider": {"minimax": 48, "codex-cli": 6}
  }
}
```

用户切换 provider 后一天内能看到新旧 provider 各自的表现。

---

## 9. 实施计划

### 9.1 阶段划分

**阶段 A(0.6.0-a1,约 4 小时)**:

1. 新建 `src/codex_self_evolution/config_file.py`:
   - `@dataclass PluginConfig` + 子 dataclass
   - `load_config(home, env=os.environ) -> PluginConfig`,读 toml + merge env + apply defaults
   - `resolve_field(value, env_name, default) -> (value, source)` 工具函数
2. CLI 加 `config show / init / validate / path`
3. 单元测试(空 config / 完整 config / 缺字段 / env 覆盖 / invalid toml)
4. `config.toml.example` 文件
5. README 加 "配置" 段落

**阶段 B(0.6.0-a2,约 3 小时)**:

1. 把 `reviewer` 相关字段从硬编码 / env var 迁移到经过 config:
   - `review/runner.py` 接收 `config` 参数
   - `HTTPReviewProvider` 的 model / base_url / timeout / max_retries 从 config 读
   - `stop_review` 用 resolved config 决定 provider
2. Legacy env var 依然覆盖(但通过 config 层统一读)
3. 测试确保已有 156 个 tests 全部绿
4. `.env.provider.example` 扩展多 provider 示例

**阶段 C(0.6.0-a3,约 4 小时)**:

1. 新建 `src/codex_self_evolution/review/subprocess_provider.py`:
   - `SubprocessReviewProvider` 类
   - 3 个 payload_mode(file / stdin / inline)
   - 3 个 response_format(codex-events / opencode-events / raw-json)
   - Timeout + retry 集成到现有 `_execute_with_retries` 类似路径
2. `_build_provider` factory 加两个 case
3. 测试(mock subprocess + 3 种 response_format + 3 种 payload_mode + timeout 路径)
4. README 加 "无 API key 用 codex-cli" 快速入门

**阶段 D(0.6.0 release,约 2 小时)**:

1. 全量测试,覆盖率目标 > 80%
2. 实机冒烟(本机跑一轮 config init → 切 provider → scan 验证)
3. bump 0.5.2 → 0.6.0
4. Build + upload PyPI
5. 打 tag + push
6. 更新 `docs/todo.md` 记录完成

Total estimated effort: ~13 小时(1.5 个工作日)

### 9.2 测试策略

| 层 | 测试内容 | 估计数 |
|---|---|---|
| config_file.py | toml parse / env merge / default apply / linter | 10-15 |
| CLI config 子命令 | show/init/validate/path 输出合法性 | 5-8 |
| subprocess_provider.py | 3 modes × 3 formats + timeout + failure + argv missing | 10-12 |
| 集成 | 新 provider 在 run_reviewer 下真跑通 | 3-5 |

目标:新增 ~30 个测试,加到现有 204 → ~235,全量时间仍 < 5 秒。

### 9.3 发布节奏

单个 **v0.6.0**(minor bump)一次性发全部。不搞 alpha 预发。原因:

- 新功能互相依赖(config.toml 是 subprocess reviewer 的前提),切 alpha 零碎发布反而容易让部分功能先跑起来再冲突
- 用户升级成本低(零配置能升,升完默认行为不变)
- 测试覆盖足够前提下,直接 minor 版本发没问题

### 9.4 版本选择说明

- 不走 0.5.3 patch: 新增 config.toml 是显著增加的 surface,语义上是 minor
- 不走 1.0: 仍在 Beta,Codex plugin-hook 支持那摊子事没落地,自称 1.0 不负责任

---

## 10. 风险与备选

### 10.1 TOML vs YAML(已决:TOML)

**权衡表**:

| 维度 | TOML | YAML |
|---|---|---|
| Python stdlib | ✅ 3.11+ | ❌ PyYAML 需装 |
| 零依赖是 plugin 卖点 | ✅ | ❌ |
| 写起来是否直观 | 可 | 更直观(无引号) |
| 深嵌套友好 | 一般 | 好 |
| 语法陷阱 | 少 | 多(缩进、隐式类型) |
| Rust / Go 生态 | 主流 | 主流 |
| 用户手写难度 | 低 | 中等(缩进害苦) |

结论:stdlib 支持 + 零陷阱两条硬条件,TOML 赢。

### 10.2 Subprocess reviewer 的失败模式

**风险**:

- `codex` CLI auth 过期 → 每次 scan 都失败
- opencode 更新后 event shape 变 → 解析器跟不上
- subprocess 启动开销比 HTTP 高(首次冷启动 1-2s)

**缓解**:

- 启动时 `shutil.which` + 连通性预检(如 `codex --version`)
- response_format 解析带容错,unknown event type 静默忽略
- config.toml 里 timeout 留足余量(默认 90s)
- 失败时走和 HTTP 一样的 fallback(0.4.0 的 env hydration + error event 识别已覆盖)

### 10.3 Price table 维护成本

README 里列的"每个 provider 价格对照"会过时。处理:

- 价格数据来源加 "as of 2026-04" 时间戳
- 链到各厂商官方定价页,让用户自己校准
- 加句 disclaimer:"Prices change; verify with each provider."

---

## 11. 已决议(原开放问题)

T0UGH 2026-04-22 拍板:

- **Q1 · RESOLVED**:`provider = "codex-cli"` 时 `codex` 不在 PATH → **硬 fail**。
  loader 启动时 `shutil.which` 预检,失败抛 `ConfigError("codex binary not found on PATH; install codex-cli or switch provider")`。
- **Q2 · RESOLVED**:`config show` **展示** API key 存在性(不含值)。沿用 diagnostics.py 的 `keys_set / keys_unset` 风格,JSON 中显示 `"minimax_api_key_set": true`。
- **Q3 · RESOLVED**:**支持自动迁移**。实现为显式子命令 `codex-self-evolution config migrate-from-env`,扫 `os.environ` + `.env.provider` 里的 legacy vars 写入 config.toml。不在每次启动时隐式迁移(避免对用户文件做惊喜操作)。
- **Q4 · RESOLVED**:不做 per-repo override,保持 home-scoped 唯一 config.toml。
- **Q5 · RESOLVED**:subprocess reviewer 失败**不 fallback 到 dummy**。失败外泄走已有的 retry → raise 路径,由 stop_review 写入 review/failed/ 并在 plugin.log 记 `exit_code=1`。

---

## 12. 附录

### A. 完整 provider 对照表(v0.6.0 支持)

| Provider Label | API 形态 | 默认 base URL | 默认 model | 需要的 env |
|---|---|---|---|---|
| `minimax` | Anthropic-style | `https://api.minimaxi.com/anthropic/v1/messages`(CN) | `MiniMax-M2.7` | `MINIMAX_API_KEY` |
| `openai-compatible` | OpenAI chat completions | `https://api.openai.com/v1/chat/completions` | `gpt-4.1-mini` | `OPENAI_API_KEY` |
| `anthropic-style` | Anthropic messages | `https://api.anthropic.com/v1/messages` | `claude-3-5-haiku-latest` | `ANTHROPIC_API_KEY` |
| `codex-cli` | subprocess(codex CLI) | N/A | N/A(由 codex 决定) | 无(用 CLI 自己的 auth) |
| `opencode-cli` | subprocess(opencode CLI) | N/A | N/A(由 opencode 配置决定) | 无 |
| `dummy` | in-memory stub | — | — | 无 |

**通过 `openai-compatible` 走的其他厂商**(只需换 base_url + model + 对应 API key,代码不动):

| 厂商 | base_url | 推荐 model | Token 价格参考(2026-04) |
|---|---|---|---|
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` | $0.075 / $0.30 per M |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | $0.14 / $0.28 per M |
| GLM(智谱) | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | ¥0.1 / ¥0.1 per M |
| Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` | ¥0.3 / ¥0.6 per M |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | ¥12 / ¥12 per M |
| Together AI | `https://api.together.xyz/v1` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | $0.88 / M |
| Fireworks | `https://api.fireworks.ai/inference/v1` | `accounts/fireworks/models/llama-v3p3-70b-instruct` | $0.90 / M |

(价格数据截至 2026-04,以各厂商官方定价页为准)

### B. 完整 env 变量迁移映射

见 §5.2。

### C. 参考资料

- Hermes agent 的 `config.yaml` 设计: `~/.hermes/config.yaml`(ref: `/Users/bytedance/code/github/hermes-agent/hermes_cli/config.py`)
- Python `tomllib` 官方文档: https://docs.python.org/3/library/tomllib.html
- XDG Base Directory Specification(未采用,但可参考): https://specifications.freedesktop.org/basedir-spec/
- 现有 plugin 配置分布(问题分析):§1.1

---

## 13. Sign-off

请 T0UGH 审阅后标记 approved / changes-requested。批准后进入阶段 A。

**Approval status**: [x] Approved 2026-04-22 by T0UGH

**Review comments**:
- Q1-Q5 拍板记录见 §11。
- 核心架构选择(TOML / .env.provider 只放 key / 单 v0.6.0 发布)一并 approved。

**Next step**: 阶段 A 落地(config_file.py + config show/init/validate/path subcommands)
