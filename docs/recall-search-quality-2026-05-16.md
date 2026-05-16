# Recall 搜索质量验证

日期：2026-05-16

本页记录 Session Recall grep-like 改造后的真实本机 DB 验证。命令均使用当前源码入口：

```bash
PYTHONPATH=src /Users/haha/hermes-agent/venv/bin/python3.11 -m codex_self_evolution.csep recall ...
```

## 测试环境

- CWD：`/Users/haha/workspace/codex-self-evolution-plugin`
- Scope：repo
- 数据源：`~/.codex-self-evolution/session_recall/state.db`
- 验证目标：真实历史 session，不是测试 fixture

## Case 1: grep-like `|` query

Query：

```bash
csep recall "Recall|grep-like|plugin skill|evidence window|scope fallback" --cwd "$PWD"
```

结果：

- Status：matched
- 命中 session：`019e3009-92c7-7471-8f03-115e958c8268`
- 命中内容：Recall 线四个拍板点，包括 CLI grep-like、`--windows-per-session`、developer/system 降噪、scope fallback
- 质量判断：通过。旧 CLI 对同类 `|` needle 曾经 `no_match`，改造后能直接找回目标讨论。

## Case 2: 中英混合 / CJK fallback

Query：

```bash
csep recall "summary需要llm|不要llm|Hermes" --cwd "$PWD"
```

结果：

- Status：matched
- 命中 session：`019e3009-92c7-7471-8f03-115e958c8268`
- 命中内容：用户提出 “summary 需要 LLM，我的初衷是不要 LLM”，以及后续 Hermes 非 LLM extractive recall 设计
- 质量判断：通过。对中英混合无空格 needle 有明显改善。

## Case 3: 普通多 token query 的 OR fallback

Query：

```bash
csep recall "summary需要llm 不要llm Hermes" --cwd "$PWD"
```

结果：

- Status：matched
- 命中 session：`019e3009-92c7-7471-8f03-115e958c8268`
- 命中内容：`summary需要llm|不要llm|Hermes` 的 skill 示例和设计讨论
- 质量判断：通过。普通空格 query 不再因为 FTS5 默认 AND 语义直接空召回。

## Case 4: Memory refs / 二级引用

Query：

```bash
csep recall "MEMORY.md|refs|二级引用" --cwd "$PWD"
```

结果：

- Status：matched
- 命中 session：`019e3009-92c7-7471-8f03-115e958c8268`
- 命中内容：Memory 热上下文 / `memory/refs/` 二级引用设计，以及 Recall 和 Memory/refs 的边界讨论
- 质量判断：通过。适合查用户原话和设计关键词。

## Case 5: recent preview 降噪

Query：

```bash
csep recall --recent --cwd "$PWD"
```

结果：

- Status：matched
- preview 优先显示真实用户任务：
  - `帮我看看这个项目`
  - `你好，你调研一下这个项目`
  - `你好`
- 质量判断：通过。旧行为容易显示 AGENTS.md / developer 背景，当前 preview 已跳过明显 AGENTS/env 注入。

## Case 6: tool metadata

Query：

```bash
csep recall "exec_command|apply_patch" --cwd "$PWD"
```

结果：

- Status：no_match
- DB 检查显示现有真实库中 `messages.tool_name != ''` 的行数为 0

质量判断：

- 当前源码已支持从 `tool_name` 做 LIKE fallback，单元测试覆盖了未来归档后按工具名召回。
- 现有真实 DB 是旧版本归档结果，未保存 tool_name，因此无法用真实库验证 tool metadata 搜索。
- 若要让旧历史也支持 tool metadata，需要重新 backfill 或做 reingest。

## 总体判断

本轮改造对真实 recall 的主要问题有实际改善：

- `|` grep-like needle 可用。
- 普通多 token query 可以 strict -> OR fallback。
- 中英混合 needle 能通过 fallback 找回。
- 默认 evidence window 不再把 developer/system 作为展示主体。
- recent preview 更接近真实用户任务。

仍需后续观察：

- 旧 DB 没有 tool_name，tool metadata 搜索需要重新 backfill 才能在真实历史上体现。
- 命中量很大时仍会出现 “Other hits hidden by budget”，后续可以继续优化 session ranking 和窗口合并。
