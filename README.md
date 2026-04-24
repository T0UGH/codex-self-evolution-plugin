# Codex Self-Evolution Plugin

[![tests](https://github.com/T0UGH/codex-self-evolution-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/T0UGH/codex-self-evolution-plugin/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

> A self-evolving collaboration layer for Codex  
> Inspired by Hermes

> Language: **English** | [中文](README_zh.md)
>
> Early-stage, but already useful for Codex-heavy workflows.

Codex can solve today’s task. It usually does not carry today’s learning into tomorrow’s session.

Codex Self-Evolution Plugin is an early-stage attempt to fix that. It gives Codex durable memory, contextual recall, and reusable skills, so completed work can become better input for future sessions instead of disappearing into chat history.

Under the hood, it runs a self-evolution loop: SessionStart injects stable background, Stop review extracts durable insights, and the compiler promotes them into managed artifacts for the next session.

## How the loop works

### Runtime loop

`SessionStart → Work → Stop Review → Compile / Promote → Next Session`

### Promoted artifacts

`USER.md / MEMORY.md / recall index / managed skills / receipts`

```text
SessionStart
    ↓
Live work in Codex
    ↓
Stop review
    ↓
Compile / promote durable artifacts
    ↓
Next session starts with better context
```

## 30-second quickstart

```bash
brew install uv

curl -fsSL https://raw.githubusercontent.com/T0UGH/codex-self-evolution-plugin/main/scripts/install-codex-hook.sh \
  -o /tmp/install-codex-hook.sh
curl -fsSL https://raw.githubusercontent.com/T0UGH/codex-self-evolution-plugin/main/scripts/install-scheduler.sh \
  -o /tmp/install-scheduler.sh
chmod +x /tmp/install-codex-hook.sh /tmp/install-scheduler.sh

mkdir -p ~/.codex-self-evolution
curl -fsSL https://raw.githubusercontent.com/T0UGH/codex-self-evolution-plugin/main/.env.provider.example \
  -o ~/.codex-self-evolution/.env.provider

# fill in your provider key, then install hooks + scheduler
/tmp/install-codex-hook.sh
/tmp/install-scheduler.sh

uvx --from codex-self-evolution-plugin codex-self-evolution status
```

For full setup, provider options, and troubleshooting, see [Installation](#installation).

## Who this is for

This project is built for:

- **Codex-heavy users** who are tired of re-explaining the same context across sessions
- **Claude Code / Cursor users** who want a Hermes-style long-term collaboration layer in Codex
- **Agent workflow builders** who care about memory, recall, review, and promotion as system primitives

## Core capabilities

### Memory

Persist durable facts about the user, environment, and collaboration style.

### Recall

Bring back contextual experience from past sessions when it is useful again.

### Skills

Promote repeatable ways of working into reusable procedural knowledge.

### Background review & promotion

Review finished work and decide what deserves to become future capability.

## Why Hermes-style self-evolution?

This is not just a memory capture tool for Codex.

The model behind it comes from Hermes-style self-evolution: durable facts go to memory, situational experience goes to recall, reusable methods become skills, and post-task review decides what is worth promoting into future sessions.

That is the key idea: not just storing more history, but turning completed work into better future collaboration.

Today this project is **Codex-first**. Over time, the same model is intended to extend to **Claude Code** and **Cursor** as well.

## Architecture

At a high level, the system is built from four parts:

- **Hooks** — SessionStart / Stop-time integration with Codex
- **Reviewer** — extracts structured suggestions from completed work
- **Compiler** — promotes approved suggestions into managed artifacts
- **Storage** — persists memory, recall, skills, receipts, and runtime state

Current implementation also includes:

- provider-backed review
- pluggable compile backends
- scheduler-driven background promotion
- named profiles for different providers / models

---

## Installation

> Codex CLI currently does **not** read plugin-manifest hooks
> ([gap analysis](docs/2026-04-21-ready-for-others-gap-analysis.md)), so the
> plugin is installed by writing directly to `~/.codex/hooks.json` via the
> provided scripts. Hooks and the scheduler both invoke the CLI via
> `uvx --from codex-self-evolution-plugin ...`, so you don't need a long-lived
> venv or a repo clone. Full step-by-step guide:
> [docs/getting-started.md](docs/getting-started.md) (中文).

End-to-end happy-path install on macOS. The only prerequisite is
[`uv`](https://docs.astral.sh/uv/#installation) (`brew install uv`):

```bash
# 1. grab the installer scripts (they're small; no pip/venv/clone needed)
curl -fsSL https://raw.githubusercontent.com/T0UGH/codex-self-evolution-plugin/main/scripts/install-codex-hook.sh -o /tmp/install-codex-hook.sh
curl -fsSL https://raw.githubusercontent.com/T0UGH/codex-self-evolution-plugin/main/scripts/install-scheduler.sh -o /tmp/install-scheduler.sh
chmod +x /tmp/install-*.sh

# 2. provider credentials (lives under ~/.codex-self-evolution/)
mkdir -p ~/.codex-self-evolution
curl -fsSL https://raw.githubusercontent.com/T0UGH/codex-self-evolution-plugin/main/.env.provider.example \
  -o ~/.codex-self-evolution/.env.provider
# edit the file and set MINIMAX_API_KEY (or OPENAI_API_KEY / ANTHROPIC_API_KEY)

# 3. Stop + SessionStart hooks in ~/.codex/hooks.json
/tmp/install-codex-hook.sh

# 4. launchd scheduler running `scan --backend agent:opencode` every 5 min
/tmp/install-scheduler.sh

# 5. sanity check
uvx --from codex-self-evolution-plugin codex-self-evolution status | python3 -m json.tool
```

Every invocation after that (hooks, scheduler, manual `status`) runs out of
uvx's cached wheel (~100ms warm). Bumping the PyPI release auto-upgrades
next time a hook fires.

**Developer install** (editable, for contributing): clone, `pip install -e .`,
and use `.venv/bin/codex-self-evolution ...` directly — see
[阶段 2](docs/getting-started.md#阶段-2手动跑一次完整循环2-分钟)
in the guide for a walkthrough that drives reviewer → compile → memory
entirely from the CLI without touching Codex / launchd.

Removing everything: `/tmp/install-scheduler.sh` has a peer
`uninstall-scheduler.sh` in the same repo; same for `install-codex-hook.sh`.
Both are idempotent and won't touch other tools' hooks or launchd jobs.

---

## Commands

Every subcommand is invokable via
`uvx --from codex-self-evolution-plugin codex-self-evolution <subcommand>`
once `uv` is installed. For readability the examples below drop the
`uvx --from codex-self-evolution-plugin` prefix.

```bash
codex-self-evolution session-start --cwd /path/to/repo
codex-self-evolution stop-review --hook-payload /path/to/stop_payload.json
codex-self-evolution compile-preflight --state-dir data
codex-self-evolution compile --once --state-dir data --backend agent:opencode
codex-self-evolution scan --backend agent:opencode        # preflight+compile across all per-project buckets
codex-self-evolution status                               # read-only diagnostic snapshot
codex-self-evolution recall --query "context" --cwd /path/to/repo
codex-self-evolution recall-trigger --query "remember previous flow" --cwd /path/to/repo
```

The module form is equivalent:

```bash
python -m codex_self_evolution.cli session-start --cwd /path/to/repo
```

---

## Configuration

This section lists everything you can configure. All variables are **optional by default**; if you only run the deterministic `dummy` / `script` path you need zero configuration. The "Required" column tells you what becomes mandatory in which scenario.

### 1. Runtime paths

| Flag / arg | Required | Default | Purpose |
| --- | --- | --- | --- |
| `--cwd` | Required for `session-start`, `recall`, `recall-trigger` | — | Repo the session is operating on. |
| `--state-dir` | Optional | `~/.codex-self-evolution/projects/<mangled-cwd>/` | Root of persistent runtime state (suggestions, memory, recall, skills, compiler receipts, review snapshots, scheduler). Each repo gets an isolated bucket named after its absolute path with `/` → `-`, so user source trees stay clean. Override with `CODEX_SELF_EVOLUTION_HOME` to relocate the whole root. |
| `--repo-root` | Optional for `compile`, `compile-preflight` | CWD of process | Repo root used to resolve `state-dir` when `--state-dir` is omitted. |
| `--once` | Optional for `compile` | off | Run a single compile pass instead of looping. |
| `--backend` | Optional for `compile` | `script` | `script` or `agent:opencode`. The default scheduler plist uses `agent:opencode`. |
| `--explicit` | Optional for `recall-trigger` | off | Mark the recall trigger as user-explicit. |

State layout under `--state-dir` (default `~/.codex-self-evolution/projects/<mangled-cwd>/`):

```
~/.codex-self-evolution/
├── .env.provider                 # API keys (created by install-codex-hook.sh)
└── projects/
    └── -Users-alice-code-myrepo/ # one bucket per repo; / → -
        ├── suggestions/{pending,processing,done,failed,discarded}/
        ├── memory/               # USER.md, MEMORY.md, memory.json
        ├── recall/               # index.json, compiled.md
        ├── skills/managed/       # managed skill markdown + manifest.json
        ├── compiler/             # compile.lock, last_receipt.json
        ├── review/snapshots/     # normalized Stop-time snapshots
        ├── review/failed/        # raw reviewer response when parse fails
        └── scheduler/
```

### 2. Hook environment variables (Codex-provided)

These are injected by the Codex host when it invokes the hook commands defined in `.codex-plugin/plugin.json`. You do not set them manually.

| Variable | Used by | Purpose |
| --- | --- | --- |
| `CODEX_CWD` | `session-start`, `recall`, `recall-trigger` | Current repo working directory. |
| `CODEX_STATE_DIR` | all hooks | Points at runtime state dir. |
| `CODEX_HOOK_PAYLOAD` | `stop-review` | Path to the Stop payload JSON. |
| `CODEX_RECALL_QUERY` | `recall`, `recall-trigger` | Query string for focused recall. |

### 3. Reviewer providers (`Stop` step)

The reviewer is provider-backed. Selection priority:

1. The `reviewer_provider` field in the Stop payload.
2. Otherwise: `dummy`.

| Provider | Purpose | Required when used |
| --- | --- | --- |
| `dummy` | Deterministic stub for tests / dry runs | **nothing** (optionally honors `provider_stub_response` in the Stop payload) |
| `openai-compatible` | OpenAI chat-completions dialect | `OPENAI_API_KEY` (or explicit `api_key` option) |
| `anthropic-style` | Anthropic messages dialect | `ANTHROPIC_API_KEY` |
| `minimax` | MiniMax (Anthropic-dialect endpoint) | `MINIMAX_API_KEY` |

#### Reviewer env vars

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Required for `openai-compatible` | — | Bearer token. |
| `OPENAI_BASE_URL` | Optional | `https://api.openai.com/v1/chat/completions` | Override endpoint. |
| `OPENAI_REVIEW_MODEL` | Optional | `gpt-4.1-mini` | Model id sent in request body. |
| `ANTHROPIC_API_KEY` | Required for `anthropic-style` | — | `x-api-key` header. |
| `ANTHROPIC_BASE_URL` | Optional | `https://api.anthropic.com/v1/messages` | Override endpoint. |
| `ANTHROPIC_REVIEW_MODEL` | Optional | `claude-3-5-haiku-latest` | Model id. |
| `MINIMAX_API_KEY` | Required for `minimax` | — | Bearer token. |
| `MINIMAX_REGION` | Optional | `global` | `global` → `https://api.minimax.io/anthropic/v1/messages`. `cn` → `https://api.minimaxi.com/anthropic/v1/messages`. |
| `MINIMAX_BASE_URL` | Optional | derived from region | Full URL override. Takes precedence over `MINIMAX_REGION`. |
| `MINIMAX_REVIEW_MODEL` | Optional | `MiniMax-M2.7` | Model id. |

#### Reviewer provider options (programmatic)

Passed in the `provider_options` dict when calling `run_reviewer(...)` directly. Each option overrides the corresponding env var.

| Option | Default | Notes |
| --- | --- | --- |
| `api_key` | from env | Overrides the provider's env-sourced key. |
| `api_base` | provider default | Full URL. |
| `model` | provider default | Model id. |
| `max_tokens` | `4096` | Output budget (not context — that's 200k). Safely within every supported model's 8k output ceiling; room for 10+ suggestions without truncation. |
| `timeout_seconds` | `30` | HTTP timeout. |
| `anthropic_version` | `2023-06-01` | `anthropic-version` header (Anthropic dialect only). |
| `stub_response` | — | Dummy provider only: canned reviewer JSON. |

### 4. Compile backends

Selected via `--backend`:

| Backend | Required | Notes |
| --- | --- | --- |
| `script` | nothing | Deterministic Python merge. Safe default. Reads existing memory / recall and does conservative incremental merge (does not wipe stable entries). |
| `agent:opencode` | `opencode` binary on `PATH` **or** an explicit `opencode_command` | Writes the `{batch, existing_assets, repo, contract}` payload to a temp JSON file, invokes `opencode run --format json --file <payload> --dangerously-skip-permissions -- <prompt>`, parses the event stream, strips code fences, and extracts the first balanced JSON object. Any failure (binary missing / non-zero exit / timeout / empty output / schema mismatch) falls back to `script`, unless `allow_fallback=False`. Validated against opencode 1.4.0. |

#### Agent compiler configuration

| Channel | Variable / option | Default | Purpose |
| --- | --- | --- | --- |
| Env var | `CODEX_SELF_EVOLUTION_OPENCODE_COMMAND` | — | Space-separated argv used instead of the default `opencode run --format json --file <payload> --dangerously-skip-permissions -- <prompt>`. Use this if your opencode install has a different CLI shape or needs extra flags. |
| Env var | `CODEX_SELF_EVOLUTION_OPENCODE_MODEL` | — | `--model <provider/name>` appended to the default command. Useful when the default build model produces truncated or non-JSON output. |
| Env var | `CODEX_SELF_EVOLUTION_OPENCODE_AGENT` | — | `--agent <name>` appended to the default command. Pick a narrow opencode agent profile if you want to lock down tool access. |
| `options["opencode_command"]` | — | env var, else built via `_build_default_opencode_command` | Explicit argv list. Takes precedence over env var. |
| `options["opencode_model"]` / `options["opencode_agent"]` | — | env var fallback | Override model / agent per invocation. |
| `options["opencode_skip_permissions"]` | — | `True` | Pass `--dangerously-skip-permissions` so the agent can call file-read tools without a TUI prompt (required for headless subprocess use). Turn off only if you've vetted the agent profile. |
| `options["opencode_timeout_seconds"]` | — | `900` (15 min) | Subprocess timeout. Kept strictly below `DEFAULT_LOCK_STALE_SECONDS` so a hung agent times out, the backend falls back, and `finally` releases the lock before preflight evicts it. |
| `options["allow_fallback"]` | — | `True` | If `False`, the agent backend raises `RuntimeError` instead of falling back to `script` on failure. |

Discard reasons appended to `CompileArtifacts.discarded_items` when the agent path fails:

- `opencode_unavailable` — binary not on `PATH` and no custom invoker.
- `agent_invoke_failed` — subprocess raised (non-zero exit, timeout, etc.); `detail` has the truncated error.
- `agent_output_invalid` — stdout was not valid JSON, or did not match the response schema; `detail` has the parse error.

The agent response schema (`src/codex_self_evolution/compiler/agent_io.py::COMPILE_CONTRACT`) is:

```json
{
  "memory_records": {"user": [...], "global": [...]},
  "recall_records": [...],
  "compiled_skills": [...],
  "manifest_entries": [...],
  "discarded_items": [...]
}
```

### 5. Compile runtime

Defined in `src/codex_self_evolution/config.py`:

| Constant | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_BATCH_SIZE` | `100` | Max suggestions claimed per compile pass. Override by calling `run_compile(batch_size=...)` from your own scheduler. |
| `DEFAULT_LOCK_STALE_SECONDS` | `1800` (30 min) | Hard upper bound for a `compile.lock`. A normal compile is expected to finish well under this (target 5-10 min). See [Compile lock protection](#compile-lock-protection) for how stale locks are detected. |
| `PLUGIN_OWNER` | `codex-self-evolution-plugin` | Only managed skills owned by this string can be modified by the compiler. Used to reject writes to unmanaged skills. |

### 6. Compile lock protection

A single `compile.lock` file under `<state-dir>/compiler/compile.lock` serializes compile runs. It is JSON: `{created_at, pid}`. A lock is considered **stale** and reclaimable by the next `preflight`/`file_lock` call if **any** of the following hold:

| Condition | Detected by | Why |
| --- | --- | --- |
| Owning `pid` is no longer a running process | `os.kill(pid, 0)` → `ProcessLookupError` | SIGKILL, crash, or machine reboot orphaned the lock. Cleared immediately. |
| Lock `created_at` is in the future (`age_seconds < 0`) | `utc_now() - created_at` | Clock skew / NTP rollback. Never trust a lock from the future. |
| Lock `created_at` older than `DEFAULT_LOCK_STALE_SECONDS` (30 min) | age threshold | Process is still alive but has been running past the tolerance. |

Design contract: since **there is no heartbeat**, `opencode_timeout_seconds` (default 15 min) must stay strictly below the lock stale window (30 min). If the agent hangs, the subprocess times out → `AgentCompilerBackend._fallback` runs → `finally` releases the lock — all well before the next preflight would steal it. Changing one of these constants must preserve that invariant.

`lock_status(paths)` returns `{locked, stale, stale_reason, pid_alive, age_seconds, owner_pid}` for diagnostics.

### 7. Scheduler (launchd)

Template plist: `docs/launchd/com.codex-self-evolution.preflight.plist`.

You must edit:

- **Interpreter path** (e.g. `/Users/haha/hermes-agent/venv/bin/python3.11`) to match your Python venv.
- **Working directory** to your repo root.
- **`--state-dir`** to the absolute path of your runtime state.

The job should wake cheaply, run `compile-preflight`, and only invoke `compile` when preflight returns `run`:

```bash
codex-self-evolution compile-preflight --state-dir data
# if status == run:
codex-self-evolution compile --once --state-dir data --backend agent:opencode
```

### 8. Docker / smoke tests

| Variable | Used by | Default | Purpose |
| --- | --- | --- | --- |
| `PYTHON` | Makefile targets | `/Users/haha/hermes-agent/venv/bin/python3.11` | Interpreter for `make test`, `make preflight`, `make provider-smoke-*`. Override if your venv lives elsewhere. |
| `IMAGE` | `make docker-*` | `codex-self-evolution-e2e` | Docker image tag. |
| `ENV_FILE` | `make provider-smoke-*` | `~/.codex-self-evolution/.env.provider` | Sourced before running real-provider smoke tests. Lives under the plugin home dir so it's shared with the installed Stop hook. Set `ENV_FILE=.env.provider` if you still keep a repo-root copy. |

`.env.provider` is auto-sourced by the Makefile if present. Copy from the template into the plugin home dir:

```bash
mkdir -p ~/.codex-self-evolution
cp .env.provider.example ~/.codex-self-evolution/.env.provider
# fill the keys you need — both make provider-smoke-* and the installed
# Stop hook read from this single location.
```

`scripts/install-codex-hook.sh` will auto-migrate a legacy `<repo>/.env.provider` into `~/.codex-self-evolution/.env.provider` on its first run.

---

## Reviewer runtime

Reviewer invocation lives in `src/codex_self_evolution/review/runner.py`. It:

1. Loads the baked prompt at `src/codex_self_evolution/review/prompt.md`.
2. Resolves a provider (`dummy`, `openai-compatible`, `anthropic-style`, `minimax`).
3. Sends the normalized review snapshot.
4. Parses the JSON response via `parse_reviewer_output(...)` and validates it against `ReviewerOutput` schema. Malformed output raises `SchemaError` and is rejected.

The main Stop path no longer trusts pre-baked `reviewer_output` in the payload: fixtures are test-only.

---

## Compile pipeline

```
pending suggestion batch
  + existing memory / recall / manifest (loaded by build_compile_context)
  -> backend.compile(batch, context, options)
  -> apply_compiler_outputs(...)  # atomic writes to memory / recall / skills
  -> write_receipt(...)
```

- `build_compile_context` reads `memory/USER.md`, `memory/MEMORY.md`, `memory/memory.json`, `recall/index.json`, `recall/compiled.md`, and the skill manifest. Missing or corrupt files fall back to empty values without raising.
- `ScriptCompilerBackend` uses `compile_memory(existing_index=...)` and `compile_recall(existing_records=...)` — existing entries are preserved by default; new suggestions only append on new `(scope, content)` pairs (memory) or new `sha1(content)` (recall).
- `AgentCompilerBackend` sends the full payload (batch + existing_assets + repo + contract) to `opencode`, parses strict JSON back, and falls back to `script` on any failure.
- Final writes (`apply_compiler_outputs`) are owned by the compiler engine, not by a separate writer module.

Each suggestion in `suggestions/` carries:

- stable `suggestion_id`
- `idempotency_key`
- explicit `state`
- `attempt_count`
- optional `failure_reason`
- `transition_log`

---

## Docker E2E

A containerized smoke/e2e flow is included.

```bash
docker build -t codex-self-evolution-e2e .
docker run --rm codex-self-evolution-e2e
# or via compose:
docker compose run --rm e2e
# or one command:
make docker-e2e
```

The container runs `scripts/docker-e2e.sh`, which:

1. Runs `pytest`.
2. Runs `session-start`.
3. Generates a Stop payload and runs `stop-review`.
4. Runs `compile-preflight`.
5. Runs `compile --backend agent:opencode` (falls back to `script` in the container because `opencode` is not installed).
6. Runs `recall-trigger`.
7. Verifies final memory / skill / receipt artifacts.

### Real provider smoke tests

```bash
make provider-smoke-minimax
make provider-smoke-openai
make provider-smoke-anthropic
```

Recommended first path: `make provider-smoke-minimax`.

Required env (per provider): `MINIMAX_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`. See [Reviewer providers](#3-reviewer-providers-stop-step) for optional overrides.

These run `scripts/provider-smoke-test.py` against the real provider APIs and print the structured reviewer output plus request payload metadata.

### Local testing

```bash
make test           # pytest
make e2e-local      # scripts/docker-e2e.sh without Docker
make preflight      # one compile-preflight call against data/
```

---

## Current scope

This project is still early-stage.

What already works today:

- Codex-first hooks for `SessionStart` and `Stop`
- per-repo runtime buckets under `~/.codex-self-evolution/projects/`
- durable promotion into memory, recall, managed skills, and receipts
- scheduler-driven background compilation
- multiple reviewer/provider profiles

What is still evolving:

- installation UX and first-run onboarding
- promotion quality and review heuristics
- cross-platform support beyond Codex
- broader hardening for real multi-user / team setups

If you want a polished end-user product today, this repo is still too early.
If you want a working Codex-first self-evolution loop that is moving quickly, it is already usable.

---

## Development Notes

- Hook wiring lives only in `.codex-plugin/plugin.json`.
- Final writes are owned by `src/codex_self_evolution/compiler/engine.py` (not a separate `writer.py`).
- Managed skills are isolated under `skills/managed/` and require plugin-owned manifest entries (owner = `codex-self-evolution-plugin`). The compiler refuses to modify skills owned by anything else.
- Review snapshots are normalized and persisted under `review/snapshots/` for debugging / auditability.
- Recall uses repo/cwd-first ranking and exposes a trigger helper instead of preloading large recall material at session start.
- When touching compile behaviour, read `docs/2026-04-20-compiler-existing-assets-handoff.md` for the rationale behind the current existing-assets pipeline.
