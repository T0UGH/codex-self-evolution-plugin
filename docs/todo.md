# TODO

持续追加的未决事项清单。完成后划掉或移到 CHANGELOG。

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
