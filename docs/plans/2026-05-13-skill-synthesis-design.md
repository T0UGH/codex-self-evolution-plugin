# Skill Synthesis Periodic Agent Design

## Goal

Add a periodic skill synthesis loop that can turn repeated historical
workflows into Codex-loadable skills.

This loop is independent from the existing stop-review and compiler pipeline.
The current compiler continues to drain pending suggestions and promote memory,
recall, and compiler-managed skills. The new synthesis loop mines already
produced runtime history and writes only synthesized skills under a separate
namespace.

## Decisions

- Skill synthesis is global and personal, not project-bucket scoped.
- Source input is aggregated from all buckets:
  - `~/.codex-self-evolution/projects/*/memory/`
  - `~/.codex-self-evolution/projects/*/recall/`
  - `~/.codex-self-evolution/projects/*/suggestions/done/`
- The default scheduled run is incremental every 4 hours.
- Incremental mode defaults to a 24-hour lookback, with overlap to tolerate
  failed runs, delayed writes, or provider timeouts.
- Full mode is manual only and scans at most the last 30 days.
- The synthesis agent may directly create, edit, and retire skills under
  `~/.codex/skills/csep-synth-*`.
- CSEP does not automatically delete or roll back files written by the agent.
  It only audits, marks invalid output, and records receipts.
- The synthesis loop is fully independent from `[compile.pi]`; it has its own
  config and does not inherit compiler settings.
- Scheduler installation is independent from the existing compiler scheduler.

## Commands

Manual full scan:

```bash
codex-self-evolution skill-synthesize --mode full --lookback-days 30
```

Scheduled incremental scan:

```bash
codex-self-evolution skill-synthesize --mode incremental --lookback-hours 24
```

Dry run:

```bash
codex-self-evolution skill-synthesize --mode full --lookback-days 30 --dry-run
```

`--dry-run` still launches the synthesis agent, but redirects the writable
skills root to a temporary workspace instead of the real Codex skills root.
This verifies agent behavior, generated skill quality, snapshot tracking, and
receipt output without polluting `~/.codex/skills`.

## State Layout

Global synthesis state lives outside project buckets:

```text
~/.codex-self-evolution/
  skill_synthesis/
    lock
    last_receipt.json
    evidence_packets/
    discarded/
    published_index.json
```

The actual synthesized skill files live directly in Codex's skills root:

```text
~/.codex/skills/
  csep-synth-<skill-id>/
    SKILL.md
```

Invalid synthesized skills are not removed. CSEP writes an audit marker next to
the skill:

```text
~/.codex/skills/csep-synth-foo/
  SKILL.md
  CSEP_INVALID.json
```

`CSEP_INVALID.json` records the check time, run id, evidence keys, and reasons.
It does not prevent Codex from loading the skill; it exists for visibility and
debugging.

## Namespace Boundaries

Skill synthesis must remain orthogonal to existing skills:

- It may only write directories matching
  `~/.codex/skills/csep-synth-[a-z0-9-]+/`.
- It must not modify user-authored skills.
- It must not modify compiler-managed `csep-*` skills.
- It must not modify project-bucket `skills/managed/` state.
- It must not migrate old project-level generated skills into synthesized
  skills.
- Retire is content-level only: keep the directory and `SKILL.md`, but mark the
  skill as retired in frontmatter/body and receipt.

The synthesis loop may read existing non-synth skills only as metadata
inventory:

- Read frontmatter `name` and `description`.
- Record path and mtime if useful.
- Do not read non-synth skill bodies.

This inventory is used only for duplicate avoidance and semantic conflict
checks.

## Configuration

Add independent config in `~/.codex-self-evolution/config.toml`:

```toml
[skill_synthesis]
enabled = true
default_mode = "incremental"
lookback_hours = 24
lookback_days = 30
skills_prefix = "csep-synth-"

[skill_synthesis.agent]
backend = "agent:pi"
provider = "minimax"
model = "MiniMax-M2.7"
timeout_seconds = 1800
```

`skill-synthesize` only reads `[skill_synthesis]` and
`[skill_synthesis.agent]`.

If the section is absent or `enabled=false`, return a clean skip result:

```json
{"status": "skip_unconfigured"}
```

Do not fall back to `[compile.pi]`.

## Agent Contract

CSEP prepares a bounded evidence packet and starts a synthesis agent with an
internal SOP. The agent is not expected to discover arbitrary local context.

The evidence packet should include:

- run id and mode
- lookback window
- source bucket
- source path
- event time when available
- family (`memory_updates`, `recall_candidate`, or `suggestions_done`)
- summary/content excerpt
- stable evidence key
- content hash
- current `csep-synth-*` inventory
- non-synth skill metadata inventory

The SOP should instruct the agent to:

- create skills only for reusable workflows, not one-off facts
- prefer edit over duplicate create when a synthesized skill already covers the
  workflow
- use retire only inside `csep-synth-*`
- write `SKILL.md` only under the allowed prefix
- write a `result.json` listing `written`, `retired`, and `skipped`
- avoid secrets, tokens, account identifiers, and transient local state
- make descriptions trigger-oriented, for example "Use when ..."
- include concrete steps, commands, checks, and failure-handling guidance

## Snapshot And Receipt

CSEP records a pre-run snapshot of `~/.codex/skills/csep-synth-*`, then runs the
agent, then records a post-run snapshot. It combines this with `result.json`.

The receipt should include:

```json
{
  "run_id": "...",
  "status": "success|partial|error|skip_unconfigured",
  "mode": "incremental",
  "lookback_hours": 24,
  "lookback_days": null,
  "dry_run": false,
  "evidence_count": 42,
  "reported_written": [],
  "detected_changed": [],
  "valid": [],
  "invalid": [],
  "retired": [],
  "skipped": [],
  "mismatch": false,
  "started_at": "...",
  "finished_at": "..."
}
```

`reported_written` comes from the agent's `result.json`.
`detected_changed` comes from the before/after filesystem snapshot.

If they disagree, keep the files in place and set `mismatch=true`. For launchd
runs, invalid output or mismatch should produce `status=partial` and still exit
0, so the scheduler does not enter noisy retry loops.

## Validation Rules

CSEP validates every changed or reported synthesized skill:

- directory path must match `csep-synth-[a-z0-9-]+`
- `SKILL.md` must exist
- frontmatter must be parseable enough to extract `name` and `description`
- description must be a concrete trigger condition
- body must contain reusable procedural content
- body must include steps, commands, checks, or verification guidance
- content must not be too short or purely factual
- content must not contain obvious secrets, bearer tokens, API keys, or
  password-like fields
- retire entries must remain inside `csep-synth-*`

Invalid output is marked with `CSEP_INVALID.json`, listed in receipt, and left
on disk.

## Scheduler

Add separate launchd scripts:

```text
scripts/install-skill-synthesis-scheduler.sh
scripts/uninstall-skill-synthesis-scheduler.sh
```

Use a separate label:

```text
com.codex-self-evolution.skill-synthesis
```

Default command:

```bash
codex-self-evolution skill-synthesize \
  --mode incremental \
  --lookback-hours 24
```

Default interval:

```bash
CSEP_SKILL_SYNTHESIS_INTERVAL=14400
```

Logs:

```text
~/.codex-self-evolution/logs/skill-synthesis.launchd.stdout.log
~/.codex-self-evolution/logs/skill-synthesis.launchd.stderr.log
```

The existing `com.codex-self-evolution.preflight` compiler scheduler remains
unchanged.

## Status Output

Extend `codex-self-evolution status` with synthesis-specific diagnostics:

- skill synthesis config status
- skill synthesis scheduler loaded/not loaded
- last synthesis receipt summary
- valid synthesized skill count
- invalid synthesized skill count
- retired synthesized skill count
- recent partial/error reasons

Keep these fields separate from compiler-managed skill counts.

## Implementation Plan

1. Add `SkillSynthesisConfig` and independent agent config to
   `config_file.py`.
2. Add `skill_synthesis/` modules for evidence collection, metadata inventory,
   snapshots, validation, receipts, and invalid markers.
3. Add an agent runner that reuses the Pi subprocess invocation pattern but has
   its own prompt, workspace, result parser, and timeout.
4. Add CLI subcommand `skill-synthesize` with:
   - `--mode incremental|full`
   - `--lookback-hours`
   - `--lookback-days`
   - `--dry-run`
   - optional `--home` for tests/debugging
5. Add independent scheduler install/uninstall scripts.
6. Extend diagnostics/status output.
7. Add tests for config, lookback filtering, dry-run, snapshot mismatch,
   invalid marker behavior, metadata-only inventory, namespace isolation, and
   scheduler plist generation.

## Test Plan

Required focused tests:

- unconfigured synthesis returns `skip_unconfigured`
- configured incremental mode applies hour lookback
- full mode applies day lookback with max 30-day default
- dry-run writes to a temporary skills root
- non-synth skill inventory reads frontmatter metadata only
- agent result and filesystem snapshot mismatch is recorded
- invalid skill writes `CSEP_INVALID.json` without deleting `SKILL.md`
- non-`csep-synth-*` directories are ignored and never modified
- retire is content-level and never removes directories
- status reports synthesis separately from compiler-managed skills
- scheduler script writes the expected label, command, interval, PATH, and logs

Final verification should run the full pytest suite and a manual dry-run smoke:

```bash
uv run pytest -q
codex-self-evolution skill-synthesize --mode full --lookback-days 30 --dry-run
codex-self-evolution status
```

## Open Risks

Invalid synthesized skills can still be loaded by Codex because the agent writes
directly under `~/.codex/skills`. This is accepted by design. CSEP mitigates it
with SOP constraints, post-run validation, visible invalid markers, and receipt
reporting.

Global synthesis can accidentally merge unrelated project workflows. Evidence
packets must preserve bucket/source metadata, and the SOP must require repeated
workflow evidence before writing a skill.

Provider quota and latency are now tied to a periodic agent run. Keeping the
normal scheduler at incremental 24-hour lookback, using manual full scans, and
storing receipts should make quota usage and failures observable.
