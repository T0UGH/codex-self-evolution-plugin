# Codex Self-Evolution Plugin

[![tests](https://github.com/T0UGH/codex-self-evolution-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/T0UGH/codex-self-evolution-plugin/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

[中文](README.md) | English

Turn every Codex session into progress for the next one.

> Use your idle Codex 5.3 Spark quota to let Codex build up experience in the background.

If you run several Codex sessions a day, the annoying part shows up quickly: Codex can finish the current task, but the next session still walks into the repo like it has never been there. You repeat the test command, the project boundaries, and the style preferences you just corrected.

You can build a full harness yourself and manage prompts, scripts, knowledge files, and logs. That works, but it is a lot of machinery. `csep` takes the smaller path: it hooks into the Codex lifecycle and lets Codex accumulate experience from real work.

Codex Self-Evolution Plugin, or `csep`, injects stable background into Codex during `SessionStart`; archives the transcript on `Stop`; and, when a session is worth keeping, starts a background reflection worker that writes memory or `csep-reflect-*` skills. Historical sessions are stored locally in SQLite/FTS and can be searched later with `csep recall`.

This is not a chat-history backup tool. Backups let you look back. `csep` is about making the next Codex session ask less, guess less, and repeat fewer mistakes.

![Codex Self-Evolution data flow](docs/assets/readme-csep-data-flow.png)

The diagram has three lines: `SessionStart` reads memory, `Stop` archives sessions, and the reflection worker writes memory and skills when the trigger policy fires.

## Why It Exists

After a few days with Codex, the real drag is usually not the big task. It is the small repeated context:

- How this repo runs tests.
- What style the user dislikes.
- Which files should not be touched.
- Which approach was already verified last time.
- Which tool or command should be used for a recurring task.
- Which correction is worth turning into a long-lived rule or skill.

`csep` keeps that experience on your machine. At the start of the next Codex session, the stable parts are injected into context. When you need details from past work, `csep recall` searches previous sessions.

## What It Does

| Capability | When it writes | How the next session uses it |
| --- | --- | --- |
| Stable Memory | After the trigger policy fires, session reflection writes `USER.md` / `MEMORY.md` | `SessionStart` injects it into Codex context |
| Session Recall | The `Stop` hook archives transcripts into local SQLite/FTS, and the bundled `csep-session-recall` skill teaches Codex how to search | `csep recall "needle1|needle2"` searches historical evidence like `rg`, scoped to the current repo unless `--global` is explicit |
| Reflection Skills | The reflection worker writes `~/.codex/skills/csep-reflect-*` | Codex loads them through the native skills loader |
| Runtime Status | Hooks, config, logs, reflection jobs, and recall DB are read-only inspectable | `csep status` shows the current runtime state |

Every `Stop` archives the session, but reflection only runs when counters or high-signal keywords hit the trigger policy.

## Design Principles

- Runtime state lives under `~/.codex-self-evolution/`, not in your application repo.
- `SessionStart` / `Stop` hook failures should not break normal Codex work.
- Every `Stop` archives, but not every `Stop` reflects.
- Reflection writes must leave a `receipt.json`; the parent process validates paths, hashes, and skill namespaces.
- Memory writes stay in the current repo bucket; generated skills must use the `csep-reflect-*` prefix.
- The core package uses only the Python standard library to keep install and hook behavior predictable.

## Install And Enable

You need Codex CLI, and your Codex version must support `plugins`, `hooks`, and `plugin_hooks`.

Recommended path:

```bash
uvx csep setup
```

This installs the `csep` CLI, registers this repo as a Codex plugin marketplace source, and enables the plugin in `~/.codex/config.toml`. Start a new Codex session after setup and the lifecycle hooks will run.

If you do not have `uv` yet:

```bash
brew install uv
uvx csep setup
```

You can also use the setup script:

```bash
curl -fsSL https://raw.githubusercontent.com/T0UGH/codex-self-evolution-plugin/main/scripts/setup.sh | bash
```

Check the current state:

```bash
csep status | python3 -m json.tool
```

Look for `plugin_hooks.manifest_exists`, `plugin_hooks.session_start_declared`, and `plugin_hooks.stop_declared` to be `true`. After that, every `SessionStart` injects memory, every `Stop` archives the transcript, and reflection runs in the background only when triggered.

### First-Time History Bootstrap

The `Stop` hook archives future Codex sessions automatically. On a fresh install, existing `~/.codex/sessions` history still needs a one-time bootstrap so the first `csep recall` can find old context:

```bash
csep recall bootstrap --since-days 30
```

Bootstrap imports transcripts newest-first and reports `new_sessions`, `updated_sessions`, `unchanged_sessions`, and `error_count`. To import all history:

```bash
csep recall bootstrap --all-history
```

To check whether recall has data:

```bash
csep recall --recent
```

For detailed setup, verification, and troubleshooting, see [docs/getting-started.md](docs/getting-started.md).

## Common Commands

Most day-to-day use only needs these commands:

| Command | Purpose |
| --- | --- |
| `csep setup` | Install the CLI, register the Codex plugin marketplace source, and enable plugin hooks |
| `csep status` | Inspect plugin, hook, reflection, and recall runtime state |
| `csep config path` / `show` / `validate` | Inspect config path, resolved config, and validation result |
| `csep recall "needle1|needle2"` | Search prior sessions for the current repo like `rg` over past sessions |
| `csep recall --recent` | List recently archived sessions for the current repo |
| `csep recall bootstrap` | Backfill local Codex session history into the recall database |
| `csep session-reflect --status` | Inspect reflection jobs when something looks wrong |

`csep recall` is not a question-answering interface. Search short needles such as file names, commands, error text, symbols, or exact user wording; use `--global` only when cross-repo history is actually needed.

`session-start`, `session-stop`, `session-archive`, and `session-ingest` are mainly for hooks and low-level troubleshooting. See [docs/getting-started.md](docs/getting-started.md) for the full command surface.

## Performance And Runtime Behavior

The foreground `SessionStart` / `Stop` hooks only do light file reads, archive-trigger bookkeeping, and state checks. Model reflection runs in the background and does not block normal Codex shutdown.

On this machine, local logs from May 15, 2026 showed this foreground hook range over the most recent samples:

| Path | median | p95 |
| --- | --- | --- |
| `session-start` | 26ms | 51ms |
| `session-stop` | 15ms | 33ms |

`session-reflect` is a background model task. Expect seconds to minutes, but it is not waited on by the Codex Stop hook.

## Runtime Directory

Default runtime root:

```text
~/.codex-self-evolution/
├── .env.provider
├── config.toml
├── logs/
├── session_reflection/
│   ├── jobs/
│   ├── runs/
│   ├── child_threads/
│   ├── triggers/
│   ├── locks/
│   └── latest.json
├── session_recall/
│   └── state.db
└── projects/
    └── -Users-you-code-repo/
        └── memory/
            ├── USER.md
            └── MEMORY.md
```

Each repo gets a bucket based on its absolute path. Application repos stay clean; runtime state stays in the user's home directory.

## Safety Boundaries

`csep` lets a background reflection worker read the current session and write local files, so the boundary is explicit:

- Provider secrets do not go into the repo. Put them in `~/.codex-self-evolution/.env.provider`.
- Runtime state does not get written into application repos.
- Skills outside the `csep-reflect-*` namespace are not accepted as generated artifacts.
- The child thread's natural-language claim is not trusted; the parent process validates `receipt.json`.
- Foreground hooks do not run long model work, so Codex can exit normally.

## Boundaries / Non-Goals

- Not a cloud memory service.
- Not a team knowledge base.
- Not a general agent framework.
- Not the old reviewer / compiler / scheduler pipeline.

It does one thing: turn local Codex sessions into a learning loop that can be verified, recalled, and extended over time.

## Current Status

Available today:

- Codex start automatically injects historical background.
- Codex stop automatically archives the current session.
- Triggered background reflection can decide whether to write memory or skills.
- Memory writes are protected by `receipt.json` and parent-process validation.
- Generated skills are restricted to the `csep-reflect-*` prefix.
- `csep recall` can search previous sessions for the current repo.
- Multiple worktrees of the same repo try to share the same repo memory.
- `csep status` / `csep config` provide read-only troubleshooting entry points.

Still being refined:

- First-run onboarding
- Reflection quality evaluation
- README diagrams and demo assets
- Claude Code / Cursor and other client support

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

You can also use the repo's `uv` environment:

```bash
uv run pytest -q
uv run python -m build
uv run twine check dist/*
```

## Docs

| Doc | What it covers |
| --- | --- |
| [docs/getting-started.md](docs/getting-started.md) | Local install, enablement, verification, and troubleshooting |
| [docs/architecture.md](docs/architecture.md) | Current Memory / Reflection Skills / Session Recall architecture |
| [docs/session-reflection.md](docs/session-reflection.md) | Session reflection worker, trigger policy, and write boundaries |
| [docs/session-recall.md](docs/session-recall.md) | SQLite/FTS session recall writes, search, and boundaries |

## License

MIT
