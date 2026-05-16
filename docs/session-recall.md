# Session Recall

Session recall 负责把过去 Codex session 中的具体上下文找回来。它和 stable memory 分层：长期稳定偏好进 memory，具体做过什么、验证过什么、踩过什么坑留在 session recall。

## 目标

- `Stop` hook 自动归档 Codex transcript。
- 支持手动归档单个 transcript。
- 支持回填 `~/.codex/sessions` 下的历史 sessions。
- 使用 SQLite/FTS 做本地检索底座。
- 默认只查当前 repo；显式 `--global` 才跨 repo。
- 输出受字符预算控制，避免把历史上下文塞得过大。

## 写入路径

默认数据库：

```text
~/.codex-self-evolution/session_recall/state.db
```

写入入口：

| 入口 | 用途 |
| --- | --- |
| `csep session-stop --from-stdin` | Stop hook 中后台归档当前 transcript |
| `csep session-archive --transcript-path ...` | 手动归档单个 transcript |
| `csep recall bootstrap --root ~/.codex/sessions` | 回填历史 Codex sessions |

归档时会解析 transcript 中的 messages，并记录：

- session id
- session path
- cwd / repo scope
- message index
- role
- content
- tool name
- timestamp
- 原始事件 metadata

## 检索路径

日常使用：

```bash
csep recall "这个仓库之前 trigger policy 怎么设计的"
csep recall --recent
csep recall bootstrap --since-days 30
csep recall "session reflection" --global
```

`csep recall` 默认按当前工作目录解析 repo scope，只返回同 repo / worktree 相关内容。跨 repo 需要显式加 `--global`。

主要参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--limit` / `--top-k` | `3` | 最多返回几个命中 session |
| `--before` | `3` | 命中消息前保留几条 |
| `--after` | `5` | 命中消息后保留几条 |
| `--budget-chars` | `12000` | 总输出预算 |
| `--message-chars` | `1200` | 普通消息截断预算 |
| `--tool-message-chars` | `600` | 工具消息截断预算 |
| `--format` | `markdown` | 可选 `json` |

历史回填参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--root` | `~/.codex/sessions` | Codex 历史 transcript 根目录 |
| `--since-days` | `30` | 只回填最近多少天修改过的 transcript |
| `--all-history` | `false` | 不按时间截断，回填全部历史 |
| `--limit-files` | 无 | 限制处理文件数，按 transcript mtime 最新优先 |

## SessionStart 注入

`SessionStart` 会注入两类内容：

1. stable background：
   - `USER.md`
   - `MEMORY.md`
2. recall contract：
   - 什么时候应该主动调用 `csep recall`
   - 推荐命令格式
   - same repo first 的边界

也就是说，CSEP 不会每次新 session 都自动把历史 session 全塞进去。默认只给 Codex 一份使用 recall 的说明，让模型在需要历史上下文时主动查询。

## 和 memory 的分层

应该进 memory 的内容：

- 用户长期偏好
- 当前 repo 长期约定
- 稳定工具路径
- 经常重复的协作规则

应该留在 session recall 的内容：

- 某次具体排障过程
- 某个方案为什么被否掉
- 某次测试命令和结果
- 一段阶段性计划或交接
- 需要按关键词查回来的历史上下文

如果 recall 不稳定，不应该把所有历史都塞进 memory。正确方向是提升 recall 的 repo scope、metadata 和检索质量。

## 边界

- 数据只存在本机 SQLite。
- 默认不跨 repo 检索。
- 当前 session 已在上下文中，recall 主要找过去 session。
- 空 query 只支持 `--recent` 模式。
- 查不到时返回 `no_match`，不 fallback 到旧 `recall/index.json`。

## 排障

检查数据库是否存在：

```bash
csep status | python3 -m json.tool
```

查看最近归档：

```bash
csep recall --recent --format json | python3 -m json.tool
```

手动归档：

```bash
csep session-archive \
  --transcript-path /path/to/session.jsonl \
  --cwd /path/to/repo \
  --session-id <id>
```

回填最近 14 天：

```bash
csep recall bootstrap --root ~/.codex/sessions --since-days 14
```
