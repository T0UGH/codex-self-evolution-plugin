# Codex Self-Evolution Plugin

Local Codex plugin that implements a staged self-evolution loop:

- `SessionStart` creates runtime state and injects stable background from `USER.md` + `MEMORY.md`, plus recall policy
- `Stop` reconstructs a normalized review snapshot, runs a provider-backed reviewer, and stores structured suggestions
- `compile-preflight` is the cheap scheduler wake/check step
- `compile` is the final writer-owned batch promotion step, with pluggable backends
- `recall` and `recall-trigger` support focused recall during a live turn

## Install

Use either of these:

```bash
pip install -e .
```

```bash
PYTHONPATH=src python -m codex_self_evolution.cli --help
```

## Commands

```bash
codex-self-evolution session-start --cwd /path/to/repo
codex-self-evolution stop-review --hook-payload /path/to/stop_payload.json
codex-self-evolution compile-preflight --state-dir data
codex-self-evolution compile --once --state-dir data --backend script
codex-self-evolution compile --once --state-dir data --backend agent:opencode
codex-self-evolution recall --query "context" --cwd /path/to/repo
codex-self-evolution recall-trigger --query "remember previous flow" --cwd /path/to/repo
```

The module form is equivalent:

```bash
python -m codex_self_evolution.cli session-start --cwd /path/to/repo
```

## Reviewer Runtime

The reviewer is provider-backed and pluggable.

Current provider choices:
- `dummy` — deterministic test/dry-run provider
- `openai-compatible` — OpenAI-compatible chat-completions style request formatting
- `anthropic-style` — Anthropic-style messages request formatting

The main hook/runtime path no longer depends on a pre-baked `reviewer_output` payload.
Instead, `Stop` builds a normalized review snapshot and passes it to the selected provider runtime.

## Scheduler Guidance

The recommended scheduler path is **launchd**, split into two stages:

1. `compile-preflight`
   - fast wake/check
   - returns `skip_empty`, `skip_locked`, or `run`
2. `compile`
   - only invoked when preflight says work exists
   - can use either the deterministic `script` backend or the `agent:opencode` backend scaffold

Typical flow:

```bash
codex-self-evolution compile-preflight --state-dir data
codex-self-evolution compile --once --state-dir data --backend agent:opencode
```

If `opencode` is unavailable, the agent backend falls back safely to the script backend while preserving the writer boundary.

## State Layout

Runtime state defaults to `data/` under the repo root:

- `data/suggestions/pending/`
- `data/suggestions/processing/`
- `data/suggestions/done/`
- `data/suggestions/failed/`
- `data/suggestions/discarded/`
- `data/memory/`
- `data/recall/`
- `data/skills/managed/`
- `data/compiler/`
- `data/review/snapshots/`
- `data/scheduler/`

Each suggestion now carries:
- stable `suggestion_id`
- `idempotency_key`
- explicit `state`
- `attempt_count`
- optional `failure_reason`
- `transition_log`

## Launchd Example

A launchd job should wake cheaply, run preflight, and only then invoke compile.
A template plist is available at:

- `docs/launchd/com.codex-self-evolution.preflight.plist`

You can adapt the working directory and interpreter path for your machine.

## Docker E2E

A containerized smoke/e2e flow is included.

Run with Docker directly:

```bash
docker build -t codex-self-evolution-e2e .
docker run --rm codex-self-evolution-e2e
```

Or with Compose:

```bash
docker compose run --rm e2e
```

The container entrypoint runs `scripts/docker-e2e.sh`, which:
- runs `pytest`
- executes `session-start`
- generates a stop payload
- runs `stop-review`
- runs `compile-preflight`
- runs `compile --backend agent:opencode`
- runs `recall-trigger`
- verifies final memory / skill / receipt artifacts

## Development Notes

- Hook wiring lives only in `.codex-plugin/plugin.json`
- Final writes are owned by `src/codex_self_evolution/writer.py`
- Managed skills are isolated under `skills/managed/` and require plugin-owned manifest entries
- Review snapshots are normalized and persisted for debugging / auditability
- Recall keeps repo/cwd-first ranking and exposes a trigger helper instead of preloading large recall material at session start
