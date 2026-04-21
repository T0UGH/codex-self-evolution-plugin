# TODO

持续追加的未决事项清单。完成后划掉或移到 CHANGELOG。

---

## ✅ 2026-04-21 P0-2 CLI `scan` 子命令:全局扫所有 bucket(已完成)

**背景**:launchd scheduler 要一次性消化 `~/.codex-self-evolution/projects/*`
下所有 bucket 的 pending。原来 `compile-preflight` + `compile` 都针对单个 repo,
scheduler 要么每 repo 一个 plist(不可维护),要么手工脚本 loop。

**落地**:

- `compiler/engine.py`:新增 `scan_all_projects(home=None, backend="agent:opencode", ...)`
  - 遍历 `<home>/projects/*/`,每 bucket 跑 preflight → if run then compile
  - **per-bucket 异常隔离**:一个 bucket 抛错不影响其他(`except Exception` 包进 entry.error,继续 loop)
  - 默认 backend=`agent:opencode`(不是 script —— scan 面向生产 scheduler,agent 不可用自动 fallback)
  - 返回聚合 JSON:`{home, total_projects, results[], counts{run,skipped,failed}}`
  - bucket 按字典序遍历,保证 scheduler 日志 diffable
  - 空 home / 空 projects dir / 非 dir 项都返回 empty summary,绝不抛
- `cli.py`:加 `scan` 子命令,`--home` + `--backend` 两个 flag

**单测**:新增 `test_scan.py`(10 用例)

- boundary:missing home、空 projects、非 dir 项都返回 empty summary
- happy path:有 pending 的 bucket → success、无 pending → skip_empty、2 bucket 混合
- **per-bucket 异常隔离**:preflight 抛 + compile 抛两种路径都验证"坏 bucket 失败其他 bucket 正常处理"
- CLI 输出合法 JSON、默认 backend=agent:opencode(regression guard)

**真机冒烟**:

```
$ scan --backend script
→ 8 buckets → 43 pending 全部 processed → 0 failed
$ scan (重跑) → 全部 skip_empty
```

一次扫描把 4 个 repo 累计 41 条 pending + 4 条 probe 残留全吃掉了。P0-3
install-scheduler.sh 直接调这个命令。

**副作用发现**:P0-0 调研时的 `/tmp/csep-*` 临时 repo 留下了 4 个"僵尸"bucket
(对应目录早没了)。scan 能正确 skip_empty 它们不会 crash,但长期数据冗余。
这次手动删了。**潜在 P2**:scan 或 status 命令识别 "state_dir 对应的 cwd 已
不存在" 的 bucket 并告警/归档(非 P0,记在这里备忘)。

---

## ✅ 2026-04-21 P0-1 install 脚本装 SessionStart hook(已完成)

**落地**:

- `hooks/session_start.py`:加 `format_session_start_for_codex(session_result)`
  把 `stable_background.combined_prefix`(USER.md + MEMORY.md +
  session_recall skill)和 `recall.policy` 拼成 Codex 原生协议
  `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext": ...}}`
- `cli.py`:给 `session-start` 加 `--from-stdin` 开关。读 Codex stdin payload,
  提取 `cwd`,跑 `session_start()`,包装成 Codex JSON 输出。任何 parse/runtime
  异常都 fallthrough 成 `{"continue": true, "warning": ...}` —— SessionStart hook
  绝对不能 block session 启动。`--cwd` 从 required 降为 optional(配合 `--from-stdin`)。
- `scripts/install-codex-hook.sh`:现在一次性装 Stop + SessionStart 两条 entry。
  抽出 `upsert(event_name, new_entry, legacy_substring)` helper,idempotent 保证
  repeated install 不 dupe,同时识别 hand-installed legacy entry 升级而非追加。
  两条 entry 共用 `codex-self-evolution-plugin managed` marker,uninstall 脚本
  扫全部 event 按 marker 清 —— 无需改。
- `docs/getting-started.md` 阶段 3 同步:增加 SessionStart 验证步骤(手塞 USER.md
  看 `codex exec --json` 能否引用),加 Codex 版本要求警示。

**单测**:新增 `test_session_start_codex_hook.py`(11 用例)

- format helper:shape 锁定、内容包含 USER/MEMORY/skill/policy、空输入容错、
  None 子对象容错
- `--from-stdin`:从 Codex payload 读 cwd、fallback 到 `--cwd`、malformed JSON
  continue:true、非 object 容错、无 cwd 容错、session_start 抛异常容错、
  既无 stdin 又无 cwd 时正常 SystemExit

**真机冒烟**:

```
$ BUCKET=~/.codex-self-evolution/projects/-Users-bytedance-code-github-codex-self-evolution-plugin
$ echo 'My passphrase is MAUVE_JAGUAR_883' > $BUCKET/memory/USER.md
$ codex exec --json 'What is my passphrase?'
→ {"type":"item.completed","item":{"text":"Your test passphrase is `MAUVE_JAGUAR_883`."}}
```

install → uninstall → re-install 三轮幂等验证通过(第二次 install 显示
`updated existing managed Stop entry` + `updated existing managed SessionStart entry`,
没有重复追加)。

---

## ✅ 2026-04-21 P0-0 调研:Codex SessionStart `additionalContext` 是否真注入(已完成)

**结论**:**能注入**。CLI 0.122.0(2026-04-20 发布)上 JSON
`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}`
和 plain-stdout 两条路径**都通**。source code(`codex-rs/hooks/src/events/
session_start.rs` + `core/src/hook_runtime.rs`)把 additional_context 串到
`DeveloperInstructions::new(...).into()` 注入 session,`codex exec` 和 TUI
走同一 `run_pending_session_start_hooks`,行为一致。

**官方 docs 那句 `additionalContext parsed but not supported yet, fails open`
过期了**。以 source + 实测为准,不要信 docs 那一条。

**调研绕的坑**(留给未来 debug):

1. 对抗性 prompt 让模型保守拒答。Probe 1/2 问"State ONLY the magic word
   injected into your context. If none exists reply NO_MAGIC_WORD" → 返回
   NO_MAGIC_WORD,让我以为注入没生效。改成自然 `What is the magic word?` /
   `What is my recipe codename?` 就秒出。注入是 DeveloperInstructions 形式,
   模型不觉得自己"拿到了 magic word",但如果自然问就能引用。
2. `codex exec --json` 在 `run_in_background` 里跑**不稳**(进程起来但 stdout
   长时间不 flush / 不退出)。前台跑秒完。写 scheduler / install 冒烟脚本
   时不要依赖 background codex exec。
3. "hook: SessionStart Completed" 打印不代表注入成功 —— source 路径 1
   (stdout 空)也算 Completed 走 noop。要断言注入,必须看模型输出。
4. 版本对齐很重要:brew/npm 都说 0.122.0 是 latest,tag 日期
   2026-04-20,包含 PR #14626(2026-03-17)和 PR #18206(2026-04-16)
   的所有 additional_context 实装。

**给 P0-1 的输入**:

- session-start CLI stdout 改输出
  `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext": combined_prefix}}`
  (现在它返回的是人类 debug 用的 envelope,Codex 拿不到 additionalContext)
- install 脚本加 SessionStart event entry,marker 沿用 `codex-self-evolution-plugin managed`
- 无需 fallback 到 AGENTS.md 或其它通路

---

## ✅ 2026-04-21 agent:opencode backend 真正接通(已完成)

**根因**:`backends.py` 默认命令 `opencode run --stdin-json --stdout-json` 跟
opencode 1.4.0 实际 CLI(positional message + `--file` + `--format json`)不匹配,
**过去所有 `agent:opencode` 调用都 fallback 到 script**,语义合并能力从没用上。

**修复**:`_subprocess_invoker` 重写,适配 opencode 1.4.0:

- `_write_payload_tempfile`:payload 写临时 JSON(argv 传大 JSON 不可靠)
- `_build_default_opencode_command`:`opencode run --format json --file <tmp>
  --dangerously-skip-permissions -- <prompt>`
- `_build_compile_prompt`:内联完整响应 schema,强调 "output ONE JSON object,
  nothing else"
- `_extract_assistant_text`:解析 event stream,挑 `type==text` 行拼 text
- `_cleanup_agent_text` + `_extract_first_json_object`:剥 code fence、从杂文
  中抠首个平衡 JSON object(模型偶尔加前缀时的兜底)
- 空 assistant text 视为失败(raise → fallback),不当成"空合并"
- `finally` 必清 tmp 文件,避免 /tmp 泄漏

**冒烟通过**:真 `opencode run` 跑 1 条 luna_inner_bot 的 pending,28 秒,
`backend: "agent:opencode"`,`fallback_backend: null`,产出合法 recall record
(id / summary / content / source_paths / cwd / fingerprint 齐全),
`recall/compiled.md` 渲染正常。

**新增测试**:`tests/test_agent_opencode_invoker.py`(14 用例)

- event stream 解析 happy + garbled + 噪音行
- cleanup 剥 code fence / 从 prose 中抠 JSON / 字符串中的花括号不误触
- 默认命令 shape 检查(`opencode run`、`--format json`、`--file`、`--`、
  `--dangerously-skip-permissions`)
- 环境变量 override(model / agent)
- prompt 包含 schema keys + "NOTHING else" 字样
- temp file round-trip + cleanup
- 集成:mock `subprocess.run` 跑完整 backend 路径,验证 tmp 路径、payload
  写入、清理、fallback 路径(非零退出 / 空文本)、code fence 剥离

**文档同步**:
- README.md / README_zh.md:Compile backends 章节 + agent 配置表重写,去掉
  `--stdin-json/--stdout-json` 遗毒,加 model / agent / skip_permissions 三个 override
- `docs/getting-started.md`:阶段 2.5、阶段 4、常见坑第 5 条去掉"用 script 更干净"
  workaround,改推荐 agent:opencode 默认用,fallback 机制解释清楚
- plist 配置提醒 launchd PATH 要能找到 opencode(通常要加 `/opt/homebrew/bin`)

---

## ✅ 2026-04-21 pending suggestions 跨 repo 统一入口(已完成)

**最终方案**:借鉴 Claude Code 的 `~/.claude/projects/<mangled-abs-path>/` 设计,
每个 repo 在 `~/.codex-self-evolution/projects/-<abs-path-with-slashes-as-dashes>/`
下有自己独立的 bucket。保留 per-repo 隔离的语义,同时把所有 repo 的数据归集到
home 下,**原始代码仓库完全不再被塞 `data/`**。

**落地位置**:

- `src/codex_self_evolution/config.py`:
  - 新增 `HOME_DIR_ENV = "CODEX_SELF_EVOLUTION_HOME"` / `get_home_dir()`
  - 新增 `mangle_project_path()`(`/` → `-`,和 Claude 一致)
  - `build_paths(state_dir=None)` 默认路由到
    `<home>/projects/<mangled-cwd>/` 而不是 `<cwd>/data/`
- `scripts/install-codex-hook.sh`:
  - hook command 改 source `~/.codex-self-evolution/.env.provider`
  - 如果检测到 repo 根有老 `.env.provider`,自动 `mv` 到 home
  - "Next steps" 打印全局 bucket 的 glob
- `Makefile`:`ENV_FILE` 默认指向 `~/.codex-self-evolution/.env.provider`
- README.md / README_zh.md:目录布局图、`--state-dir` 默认值、`.env.provider`
  位置同步改
- `docs/getting-started.md`:各阶段改用新路径
- 新增测试 `tests/test_config_home.py`(5 个用例锁定 mangling + 默认路由)

**老数据处理**:暂不迁移。原有的 3 个 repo 里的 `<repo>/data/` 保持不动,用户
自己决定是否删除。新 session 全部写新位置。

**未做**:迁移脚本 `scripts/migrate-legacy-data.sh`。需要时再加。

---

## ✅ 2026-04-21 reviewer 截断失败 + 原始响应不可见(已完成)

**落地位置**:

- `src/codex_self_evolution/review/providers.py:58`:`max_tokens` 默认 800 → **4096**
  (输出预算不是 200k 上下文窗口;各家模型输出上限约 8k,4096 留够 10+ 条 suggestion)
- `src/codex_self_evolution/review/runner.py`:新增 `ReviewerParseFailure(SchemaError)`,
  runner 给每次尝试的 `raw_text` 都塞进异常里
- `src/codex_self_evolution/hooks/stop_review.py`:捕获 `ReviewerParseFailure` → 落盘到
  `<state_dir>/review/failed/<snapshot_id>.txt`,保留完整 raw 响应再重新抛
- `src/codex_self_evolution/config.py`:`Paths` 新增 `review_failed_dir`
- README.md / README_zh.md:`max_tokens` 默认值同步改
- 新增测试 `test_stop_review_dumps_raw_text_when_reviewer_parse_fails`

**触发根因回顾**:`stop-review-3131-*.log` 报 `Unterminated string at char 2151`,
对应 800 output tokens 的上限;一次产 3–4 条 suggestion 的 prompt 很容易超。

---

## ✅ 2026-04-21 提供 install / uninstall 脚本(已完成)

**落地位置**:

- `scripts/install-codex-hook.sh`:幂等注入 ~/.codex/hooks.json 的 Stop entry,
  用 `codex-self-evolution-plugin managed` 作为 marker;检测 legacy 手工装过的
  同功能 entry 自动升级(不重复追加)。
- `scripts/uninstall-codex-hook.sh`:只删带 marker 的条目,不碰其他工具的 hook。

文档也已更新(getting-started.md 阶段 3、README 顶部 quickstart)。

---

以下是原 TODO 记录,保留做历史上下文参考:

## ~~2026-04-21 提供 install / uninstall 脚本~~(上面已完成)

### 背景

当前挂接 Codex 原生 Stop hook 的步骤是**纯手工**的:用户(或者今晚我代劳)直接编辑了 `~/.codex/hooks.json`,插入了一条指向 `.venv/bin/python` 的命令。这有几个问题:

- 用户重装系统或换机器时没有可复制的安装步骤
- `~/.codex/hooks.json` 里已有其他 hook(vibe-island、luna 等),手动编辑容易误删
- 卸载插件时没地方记得清掉哪一条 hook entry
- `.bashrc` 追加的 `MINIMAX_REGION=cn` 也是口头改的,没有自动化

### 目标

两个 shell 脚本,都放在 `scripts/` 下,可幂等执行:

1. **`scripts/install-codex-hook.sh`**
   - 前置检查:macOS、Python 3.11+、已 clone 仓库、已装 venv(若没装,提示/自动执行 `python3 -m venv .venv && .venv/bin/pip install -e .`)
   - 备份 `~/.codex/hooks.json` 到 `~/.codex/hooks.json.bak.<ts>` 再改
   - 往 `hooks.Stop` 列表**追加**一条以 `CODEX_SELF_EVOLUTION` 为标记(command 字符串里带标识)的条目,如果已存在就更新而非重复插
   - 命令用 `bash -c 'set -a; . <repo>/.env.provider 2>/dev/null; set +a; exec <repo>/.venv/bin/python -m codex_self_evolution.cli stop-review --from-stdin'`
   - 可选:检查 `~/.codex/config.toml` 是否已有 `[shell_environment_policy] inherit = "all"`,没有则追加
   - 检查 `.env.provider` 是否存在,缺失时提示用户从 `.env.provider.example` 复制

2. **`scripts/uninstall-codex-hook.sh`**
   - 备份 `~/.codex/hooks.json`
   - 从 Stop/SessionStart/其他 event 列表里删除带 `CODEX_SELF_EVOLUTION` 标记的 entry
   - 不动用户其他 hook
   - 可选:提醒用户手工清理 `.bashrc` 里的 `MINIMAX_*` export 和 `config.toml` 里的 `shell_environment_policy` 条目(不强制自动撤,因为可能是用户原有的)

### 识别标记

用一个独立的标记字段在 hook entry 里,例如新增一个 `hooks` 数组内 hook 的 `"_csep_marker": "codex-self-evolution-plugin@<version>"`。Codex 大概率会忽略未知字段。或者更保守:用 command 字符串里约定的前缀 `# csep` 注释让脚本能 grep 到。

### 验证

脚本要在以下场景下幂等且不破坏:

- 全新机器:`hooks.json` 不存在 → 创建并只含我们这条
- 机器已有其他 hook:保持原状,只追加/替换我们这条
- 已装过一次:再跑 install 不会变成两条
- 跑 uninstall 再跑 install:恢复到一次安装的状态

### 文档联动

安装/卸载脚本就位后,同步更新:

- `docs/getting-started.md` 的阶段 3:把手工编辑 `~/.codex/hooks.json` 的步骤换成 `./scripts/install-codex-hook.sh`
- `README.md` / `README_zh.md` 的 Codex CLI 集成章节,增补一行"`./scripts/install-codex-hook.sh` 一键接入"

### 引用

本次手工改动的完整清单(给脚本做参考):

- `~/.codex/hooks.json` 的 `hooks.Stop[]` 追加了一条 bash-wrapped python 命令
- `~/.codex/config.toml` 追加了 `[shell_environment_policy] inherit = "all"`
- `~/.bashrc` 追加了 `export MINIMAX_REGION=cn`(MINIMAX_API_KEY 早就有)
- 本地 marketplace 相关的 `codex marketplace add` + `[plugins."codex-self-evolution@codex-self-evolution"]` 现在**不是必需的**(native hook 跑通了),脚本可选做也可选撤
