# Stable Memory 设计

状态：设计定稿草案  
日期：2026-05-16

本文记录 Stable Memory 线的新设计。它替代当前架构文档中 `USER.md` / `MEMORY.md` 双文件模型的描述。

## 目标

Stable Memory 负责保存下一次 `SessionStart` 时就应该知道的低熵上下文，包括：

- 当前项目状态摘要
- 人机协作中的 gotcha
- 稳定规则和偏好
- 指向更长背景资料的本地绝对路径

Stable Memory 不负责保存完整历史。完整 transcript、命令结果、排障过程和原始证据继续留在 Session Recall 中。

## 非目标

- 不做强 schema 系统。
- 不做安全沙箱。
- 不自动过滤、截断、回滚或删除用户可见内容。
- 不把 `MEMORY.md` 做成 changelog。
- 不让 SessionStart 自动展开二级引用。

## 文件模型

Stable Memory 收敛为单文件热上下文和二级冷引用：

```text
~/.codex-self-evolution/projects/<project-bucket>/memory/
├── MEMORY.md
└── refs/
    └── ...
```

`MEMORY.md` 是唯一默认注入文件。`refs/` 下的 Markdown 文件是 review agent 整理出的按需引用资料，不默认注入。

`USER.md` 概念直接移除：

- SessionStart 不读取 `USER.md`
- review agent 不写入 `USER.md`
- validation 不把 `USER.md` 视为合法 memory 写入目标
- 如果旧 `USER.md` 已存在，保留在磁盘上但完全忽略
- status 可以提示 `legacy USER.md exists but ignored`

## MEMORY.md

`MEMORY.md` 使用纯 Markdown。

推荐结构如下，但不强制：

```markdown
# Project Memory

## Current State
- ...

## Gotchas
- ...

## Rules
- ...

## Preferences
- ...

## References
- ...
```

规则：

- 缺章节不报错。
- 章节名不完全一致不报错。
- 不要求 YAML、JSON、frontmatter 或稳定字段。
- review agent 尽量维护成推荐结构。
- linter 和 status 只提示，不做强校验。

`MEMORY.md` 可以包含这些内容：

- `state`：当前项目最近状态，例如发布进度、本机安装状态、当前待处理事项。
- `gotcha`：人机之间或本机环境里容易反复踩坑的点。
- `rule`：项目规则、验证顺序、边界约束。
- `preference`：用户或协作偏好。
- `reference`：一句摘要加一个本地绝对路径，指向 `refs/` 里的长内容。

这些类型不要求写成结构化字段。它们是 review agent 的写作约定，不是 parser contract。

## refs/

`refs/` 是二级引用区，用来保存有价值但不适合默认注入的长内容。

适合放入 `refs/` 的内容：

- 设计讨论记录
- 发布记录
- 长排障过程
- 阶段总结
- 被压缩出主 memory 的历史状态

`refs/` 根目录固定，但子目录和文件名不强制。review agent 可以大致按类型整理，例如：

```text
refs/state/release-1.1.0.md
refs/design/memory-line.md
refs/gotchas/python-version.md
```

也可以使用其他命名方式。parent/status 最多检查引用文件是否位于项目 memory 目录下，不因为命名不规范报错。

主 `MEMORY.md` 引用 `refs/` 时使用“一句摘要 + 绝对路径”：

```markdown
- Memory 线设计细节见：/Users/haha/.codex-self-evolution/projects/<project-bucket>/memory/refs/design/memory-line.md
```

SessionStart 只注入这行，不自动打开引用文件。后续 agent 看到路径后自行决定是否读取。

## SessionStart 行为

SessionStart 保持极轻：

- 只读取 `MEMORY.md`
- 全文注入 `MEMORY.md`
- 不读取 `USER.md`
- 不读取 `refs/`
- 不按章节裁剪
- 不按 token 或字符预算裁剪
- 不因 validation warning、secret warning 或文件过大而跳过

如果 `MEMORY.md` 很大，status 可以提示风险，但 SessionStart 不改变行为。体积治理交给 review agent。

## Review Agent 行为

Stable Memory 由后台 review agent 维护。

触发时机：

- 沿用现有 reflection trigger。
- Stop hook 每次仍归档 transcript 到 Recall。
- 只有 trigger 命中时才运行 review agent 维护 `MEMORY.md` / `refs/`。

可用输入：

- 当前 session transcript
- 现有 `MEMORY.md`
- 现有 `refs/`
- 必要时自行调用 `csep recall` 查历史

review agent 调用 recall 不设专门次数限制，由 agent 自己判断。整体仍受 reflection job 的超时、锁和后台任务预算约束。

review agent 可以：

- 直接修改 `MEMORY.md`
- 创建或修改 `refs/`
- 删除无价值内容
- 将有价值但占地方的长内容整理到 `refs/`
- 在主 `MEMORY.md` 留一句摘要加绝对路径

判断原则：

- 无价值内容可以直接删除，例如重复内容、临时流水账、错误结论、无复用意义的细节。
- 有价值但不适合默认注入的内容下沉到 `refs/`。
- 下次启动就应该知道的内容保留在主 `MEMORY.md`。

## Parent Validation

parent 不负责合并 memory，也不做重型审计。

review agent 直接修改 `MEMORY.md` / `refs/`。parent 只做轻量校验和状态记录：

- receipt 存在
- receipt 能解析
- job id 对得上
- 如果 receipt 声明改了 memory 相关内容，路径应位于项目 memory 目录或 `memory/refs/` 下

parent 不要求 receipt 记录所有改动文件和 hash。

validation 失败时：

- 不回滚
- 不删除
- 不过滤
- 不阻断后续 SessionStart
- 只把 reflection job 标记为 failed 或 partial，并在 status 中暴露 warning

secret detector 和 memory linter 也只产生 warning，不 hard block。

## Status 行为

`csep status` 可以暴露这些信号：

- `memory_file_exists`
- `memory_size_bytes`
- `legacy_user_md_ignored`
- `refs_count`
- `latest_memory_validation_status`
- `latest_memory_validation_warning`

这些字段只用于可观测性，不改变 SessionStart 注入行为。

## 与 Session Recall 的边界

Memory 和 Recall 的分工：

```text
MEMORY.md = 启动时应该知道的压缩上下文
refs/ = review agent 整理出的按需长资料
Session Recall = 原始历史、transcript、命令结果和可查证证据
```

review agent 可以从 recall 中提炼内容进 memory 或 refs，但 recall 本身仍是历史证据层，不默认进入 SessionStart。

## 实现影响

需要调整的主要模块：

- `hooks/session_start.py`：只读取并注入 `MEMORY.md`。
- session reflection prompt：只要求写 `MEMORY.md` 和 `refs/`，不再提 `USER.md`。
- validation：移除 `USER.md` 合法写入目标，保留 memory 目录边界检查。
- diagnostics/status：提示 legacy `USER.md` ignored、memory size、refs count、validation warning。
- docs：更新 architecture、session-reflection、README 中的双文件描述。

测试重点：

- SessionStart 不读取 `USER.md`。
- SessionStart 全文注入 `MEMORY.md`。
- `refs/` 不被自动注入。
- legacy `USER.md` 存在时不删除、不读取。
- review receipt 轻量校验通过。
- path boundary warning 能被 status 暴露。
