# 2026-04-21 可发布性差距审计

> 目的:盘点当前项目离"可以给陌生用户装起来用"还差什么。
> 执行时间点:`agent:opencode` backend 刚接通(commit `a620605`)、per-repo
> home dir 刚迁移完(commit `9cc81d2`),reviewer 链路已稳定。
>
> 本文档**只列差距**,不做实现。命中的项请在 `docs/todo.md` 建条目再做。

---

## 关键前置上下文(影响多条判断)

**Codex 原生 CLI 目前不支持 plugin manifest 里的 hooks 字段。**

2026-04-20 实测:`codex marketplace add` 能装 plugin,但 `plugin.json` 里声明的
hook(`SessionStart` / `Stop` / etc.)**不会被 Codex 自动注册**,需要用户手工
写入 `~/.codex/hooks.json`。这是 Codex CLI 侧的限制,不是本插件的问题。

因此:

- `plugins/codex-self-evolution/.codex-plugin/hooks.json` 缺失不是真·阻塞
  (就算补上,Codex 也不会用它)
- 目前唯一可行的挂接路径就是 `scripts/install-codex-hook.sh`,直接改
  `~/.codex/hooks.json`,绕开 plugin manifest
- marketplace 那套 `uvx --from git+...` 命令写了但没人能真走通,除非
  Codex 将来开放 plugin hooks

下面标 🚨 的条目都已经考虑过这个前置。

---

## 🚨 阻塞级 — 别人装了也用不起来

### 1. `plugins/codex-self-evolution/.codex-plugin/hooks.json` 缺失(低危)

- **现状**:`plugin.json` 里 `"hooks": "./hooks.json"`,但 hooks.json 文件不
  存在。`codex marketplace add` 会装 plugin,但没 hooks 什么都不发生。
- **为什么不紧迫**:Codex 现在根本不读 plugin hooks(见上面"关键前置上下
  文")。补上也没用。
- **建议做法**:在 plugin.json 旁边加一个 `hooks.json.disabled` 占位 + README
  说明"等 Codex 支持 plugin hooks 后改回来",避免未来的自己或贡献者走回
  头路。

### 2. install 脚本只装了 Stop hook,其它 hook 没装

- **现状**:`scripts/install-codex-hook.sh` 只往 `~/.codex/hooks.json` 写一条
  `Stop` entry。但 `plugin.json` 声明的 `commands` 有 6 个:
  - `session-start`(SessionStart event)
  - `stop-review`(Stop event) ✅ 已装
  - `compile-preflight` / `compile`(scheduler)
  - `recall` / `recall-trigger`(live turn recall)
- **影响**:用户装完实际只拿到 "Stop → reviewer → pending 落盘" 一小段。
  README 和 getting-started 宣传的 "stable background 注入" / "focused
  recall" / "managed skills" 等能力全部**没挂起来**,虽然代码都在。
- **建议做法**:install 脚本扩到装全 5 类 hook。需要先确认 Codex 各 event
  的 payload shape(SessionStart 是否同样用 `--from-stdin`、recall-trigger
  怎么被 Codex 触发)。

### 3. launchd scheduler 完全手工

- **现状**:compile-preflight / compile 需要定时调度,`docs/launchd/` 下有
  plist 模板,但用户要自己:
  - 改 4 处占位(仓库绝对路径 × 2、`.venv/bin/python` × 2、state-dir、
    `StartInterval`)
  - 确保 plist 的 `EnvironmentVariables.PATH` 能找到 `opencode`(launchd
    默认 PATH 不含 `/opt/homebrew/bin`)
  - 手动 `cp`、`launchctl load`、`launchctl start`
- **影响**:没装 scheduler = pending 永远不 compile = **闭环断在 writer
  之前**。目前 `~/.codex-self-evolution/projects/` 下两个 repo bucket 累计
  7 条 pending 就是这么攒下来的。
- **建议做法**:`scripts/install-scheduler.sh` 自动生成 plist(读取当前仓库
  路径、venv、home dir)+ `launchctl load`;配套 `uninstall-scheduler.sh`。
  opencode 路径探测用 `command -v opencode`,探不到就 warn。

### 4. 非 git clone 路径从没验证过

- **现状**:`plugin.json` 的 `commands` 都是 `uvx --from git+https://...
  codex-self-evolution ...`,理论上支持"不 clone 直接装"。但这条路径
  从未在真实环境跑通过 —— 所有测试都走 `$REPO/.venv/bin/python`。
- **影响**:即使未来 Codex 开放 plugin hooks,`uvx` 路径也可能挂(包缺
  entry point、`--cwd` / `--state-dir` 路径传参不合预期、`.env.provider`
  source 不到)。
- **建议做法**:现在这条不急,但如果要往 PyPI / marketplace 发,必须
  在 clean macOS 跑一次 `uvx --from git+... codex-self-evolution session-
  start --cwd /tmp/foo --state-dir /tmp/foo-state`,把坑填完。

---

## 🔶 可用性 — 装了也不好用

### 5. 没有 `status` 诊断命令

- **现状**:用户装完完全不知道"插件在不在工作"。要自己 `ls ~/.codex-
  self-evolution/projects/*/suggestions/pending/` + `cat compiler/last_receipt
  .json`,体验等于纯裸文件系统。
- **建议做法**:`codex-self-evolution status` 输出一屏 JSON/表格:
  - 所有 repo bucket 的 pending / processing / done / failed 计数
  - 每个 bucket 的最后一次 review 时间、最后一次 compile receipt
  - `~/.codex/hooks.json` 里 managed 的 Stop hook 是否还在
  - launchd job 是否 loaded
  - `.env.provider` 是否存在、API key 是否设置(不打印值,只判非空)
  - opencode / codex CLI 版本

### 6. 没有日志落盘

- **现状**:`grep print(` 只有 4 处,全是 CLI JSON 输出。Stop hook 跑失败
  时 stderr 不会显示给 Codex 用户。reviewer 超时 / 401 / 模型漏字段,
  从用户视角看就是"什么都没发生"。目前只有 `review/failed/<snapshot_id>.
  txt` 在 reviewer parse 失败时落盘(这条是 commit 621e675 新加的),
  其它错误路径全无痕迹。
- **建议做法**:最小化加一个 `~/.codex-self-evolution/logs/` 目录,每个
  stop-review 子进程、compile 子进程、scheduler preflight 都各落一份
  `<ts>-<kind>.log`(stdout + stderr + structured summary)。保留最近 N
  份即可,不搞日志系统。

### 7. marketplace plugin.json 里 compile 还硬编码 `--backend script`

- **现状**:`plugin.json` 的 `scheduler.compile_command` 和 commands 里
  的 compile 全是 `--backend script`。本轮(commit `a620605`)把
  `agent:opencode` 修通了,但 marketplace 装进来的用户默认还是走 script,
  享受不到语义级合并。
- **建议做法**:改成 `--backend agent:opencode`。agent 不可用时代码层面
  已有自动 fallback 到 script,不会整体失败。

---

## 🟡 发布信号 — 外部贡献者看到的第一印象弱

### 8. 没有 CI

- **现状**:`.github/workflows/` 不存在。README 没有 "tests passing" 徽章。
  外部读者打开仓库看不到健康信号。
- **建议做法**:一条 GitHub Actions(pytest + optional ruff/mypy)+
  README 顶部徽章。不需要跑真 provider 冒烟。

### 9. 没有 LICENSE 文件

- **现状**:`plugin.json` 声明 MIT,但仓库根目录没 `LICENSE` 文件。
  GitHub 默认认不出许可证。
- **建议做法**:`curl -o LICENSE https://raw.githubusercontent.com/.../MIT`
  之类的,或直接手写一份。5 分钟。

### 10. 没发 PyPI

- **现状**:`pip install codex-self-evolution-plugin` 用不了,只能
  `pip install -e .` 或 `uvx --from git+...`。
- **影响**:发 PyPI 之前 marketplace 那条 `uvx --from git+...` 是唯一
  "零 clone" 路径。发了 PyPI 才能改成 `uvx codex-self-evolution`,安装
  体验提升一大截。
- **建议做法**:不急。先把 Codex plugin hooks 那边的前置问题解决,
  否则 PyPI 包装了也没人用。

### 11. README 的 "30 秒安装" 过于乐观

- **现状**:README 第 25 行 `pip install -e .` 完事,但实际要:
  1. `git clone`
  2. 建 venv + `pip install -e .`
  3. `cp .env.provider.example ~/.codex-self-evolution/.env.provider`
     填 API key
  4. `./scripts/install-codex-hook.sh`
  5. 手工装 launchd plist(见 getting-started 阶段 4)
  6. 可选装 opencode
  - 一共大约 15-30 分钟,不是 30 秒。
- **建议做法**:README 顶部加 "Reality check:首次装需要 ~20 分钟,因为
  Codex 暂不支持 plugin hooks(见 docs/2026-04-21-ready-for-others-gap-
  analysis.md),详细步骤看 getting-started.md"。或者直接在 README
  写一个 "one-command bootstrap",跑完全部 1-4 步(需要先做完前面几个
  阻塞项)。

---

## ✅ 已经做得够好的(不动)

- 3 种 reviewer provider(MiniMax / OpenAI-compatible / Anthropic-style)都
  有 smoke test;`.env.provider.example` 模板清晰
- 测试覆盖率高(101 passed,含 agent backend 的 mocked 集成)
- Docker(Dockerfile + docker-compose.yml + `scripts/docker-e2e.sh`)可跑
- `docs/getting-started.md` 内容扎实(只是步骤多)
- 关键路径都有幂等性(install/uninstall、compile lock reclaim、suggestion
  去重)
- `agent:opencode` backend 真通了(commit `a620605` 冒烟验证 28 秒跑完
  一条 pending,fallback_backend=null)

---

## 建议优先级

| 梯队 | 条目 | 估时 | 目标 |
| --- | --- | --- | --- |
| **P0** | 2 装全 hooks、3 scheduler 自动化、5 status 命令、7 plugin.json 改 agent:opencode | 4-6 h | 从"作者自用"到"可以给朋友装" |
| **P1** | 6 日志落盘、8 CI、9 LICENSE、11 README reality check | 2-3 h | 从"能用"到"愿意让人用" |
| **P2** | 1 hooks.json 占位(等 Codex 支持)、4 uvx 路径验证、10 PyPI 发包 | 依赖外部 | 从"有人装"到"可以发 HN / X / marketplace" |

P0 做完基本过坎。其它按需。

---

## 不要做什么

- 不要在 Codex 开放 plugin hooks 之前折腾 marketplace 发布。那条路径
  当前**本质不通**,花时间也白花。
- 不要为发 PyPI 反过来改代码风格。package 已经 `pip install -e .` 就能
  跑,瓶颈在发布流程不在代码。
- 不要加"推荐模型"之类的智能默认。用户的 provider 选择跟他手里
  有哪家 API key 强相关,硬塞默认只会踩坑。
