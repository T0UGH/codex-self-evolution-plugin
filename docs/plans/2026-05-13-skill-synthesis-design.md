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
- The compiler no longer creates, edits, retires, or publishes skills. It only
  promotes memory and recall. Existing compiler-managed skills are treated as
  legacy read-only artifacts.

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

During dry-run, CSEP also snapshots the real `~/.codex/skills/csep-synth-*`
tree before and after the run. If the real root changes, the command must write
a receipt with `status=error`, `dry_run_leak=true`, and
`changed_real_paths`, but it still must not delete or roll back files.

## State Layout

Global synthesis state lives outside project buckets:

```text
~/.codex-self-evolution/
  skill_synthesis/
    lock
    last_receipt.json
    evidence_index.json
    published_index.json
    receipts/
    runs/
    discarded/
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

Namespace ownership is split into three groups:

- user or third-party skills: anything not under a `csep-*` prefix; never
  touched by this system
- compiler legacy skills: `csep-*` except `csep-synth-*`; read-only legacy
  artifacts
- synthesized skills: `csep-synth-*`; only the skill synthesis subsystem may
  mark them invalid or retired

Any future cleanup logic that matches `csep-*` must explicitly exclude
`csep-synth-*` unless the code is inside the skill synthesis subsystem.

Existing compiler-managed skills are not deleted or migrated:

- bucket `skills/managed/` state is legacy read-only state
- global `~/.codex/skills/csep-*` projections are legacy read-only state
- the compiler no longer creates, edits, retires, or publishes these skills
- skill synthesis may read their frontmatter metadata for duplicate avoidance
  only

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

In v1, only `backend = "agent:pi"` is supported. If synthesis is enabled and
another backend is configured, `config validate` should report a warning or
non-zero validation result, and `skill-synthesize` should return
`unsupported_backend`.

The default config template should enable synthesis and use Minimax defaults,
so the feature is runnable after config initialization when the matching API
key is available.

## Agent Contract

CSEP prepares a materialized run workspace and starts a synthesis agent with an
internal SOP. The agent receives the workspace path and writable skills root;
it is not expected to discover arbitrary local context.

The run workspace shape is fixed:

```text
~/.codex-self-evolution/skill_synthesis/runs/<run_id>/
  input/
    MANIFEST.json
    README.md
    buckets/
    skills_inventory.json
    synth_inventory.json
    evidence_index_snapshot.json
  output/
    result.json
    notes.md
```

The input manifest should include:

- run id and mode
- lookback window
- source bucket
- source path
- original path
- event time when available
- event time source
- family (`memory_updates`, `recall_candidate`, or `suggestions_done`)
- summary/content excerpt
- stable evidence key
- content hash
- redaction count
- current `csep-synth-*` inventory
- non-synth skill metadata inventory

The materialized input should contain enough text for the agent to summarize
workflow patterns, but it should not copy unbounded transcripts. Suggested
limits:

- memory and recall entries: full entry content, capped at 8 KiB per item
- suggestions done: reviewer suggestions and details, capped at 16 KiB per
  envelope
- review snapshots and full transcripts: excluded in v1

`original_path` is retained for audit and manual debugging. The SOP must forbid
the agent from opening files outside the materialized workspace, except for
writing allowed synthesized skill files under the provided skills root.

Input materialization must redact obvious secret-like strings before writing
them into `runs/<run_id>/input/`. Redaction count is recorded in the manifest.

The SOP should instruct the agent to:

- create skills only for reusable workflows, not one-off facts
- prefer edit over duplicate create when a synthesized skill already covers the
  workflow
- use retire only inside `csep-synth-*`
- read only `input/`
- write `result.json` only under `output/`
- write `SKILL.md` only under the provided skills root and allowed prefix
- avoid secrets, tokens, account identifiers, and transient local state
- make descriptions trigger-oriented, for example "Use when ..."
- include concrete steps, commands, checks, and failure-handling guidance

The agent result schema is:

```json
{
  "schema_version": 1,
  "run_id": "...",
  "actions": [
    {
      "action": "create|edit|retire|skip",
      "skill_id": "csep-synth-foo",
      "path": "~/.codex/skills/csep-synth-foo/SKILL.md",
      "evidence_keys": ["..."],
      "reason": "..."
    }
  ],
  "notes": "optional"
}
```

Missing or invalid `result.json` should not prevent filesystem audit. It should
make the receipt `status=partial` with `result_missing=true` or
`result_invalid=true`.

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
  "new_evidence_count": 12,
  "seen_before_count": 30,
  "reported_written": [],
  "detected_changed": [],
  "valid": [],
  "invalid": [],
  "retired": [],
  "skipped": [],
  "mismatch": false,
  "dry_run_leak": false,
  "started_at": "...",
  "finished_at": "..."
}
```

`reported_written` comes from the agent's `result.json`.
`detected_changed` comes from the before/after filesystem snapshot.

If they disagree, keep the files in place and set `mismatch=true`. For launchd
runs, invalid output or mismatch should produce `status=partial` and still exit
0, so the scheduler does not enter noisy retry loops.

Every run writes both:

```text
~/.codex-self-evolution/skill_synthesis/receipts/<timestamp>-<run_id>.json
~/.codex-self-evolution/skill_synthesis/last_receipt.json
```

`last_receipt.json` is the newest receipt copy used by `status`. Historical
receipts are retained for recent debugging.

`published_index.json` is a rebuildable cache, not the source of truth. The
source of truth is the actual `SKILL.md` files plus receipt history. If the
index is missing or corrupt, CSEP should rebuild it by scanning
`~/.codex/skills/csep-synth-*`.

`evidence_index.json` tracks evidence across runs for overlap handling and
debugging:

```json
{
  "schema_version": 1,
  "updated_at": "...",
  "evidence": {
    "<evidence_key>": {
      "first_seen_at": "...",
      "last_seen_at": "...",
      "seen_count": 3,
      "last_run_id": "...",
      "last_outcome": "used|skipped|ignored|invalid"
    }
  }
}
```

Repeated evidence may still be materialized in the next workspace, but it
should be marked `seen_before=true`.

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

Output containing obvious secret-like strings is invalid with
`reason=secret_like_content`.

Invalid output is marked with `CSEP_INVALID.json`, listed in receipt, and left
on disk. If a previously invalid skill becomes valid later, CSEP may remove its
own `CSEP_INVALID.json` marker. This is marker maintenance, not rollback of
agent output.

Retired skills must use a standard low-trigger format:

```markdown
---
name: csep-synth-foo
description: "Retired generated skill. Do not use."
csep_status: retired
---

# Retired: Foo

This generated skill has been retired by codex-self-evolution skill synthesis.

Do not use this skill. It is kept only for audit history.
```

A retired skill whose description still looks like a normal trigger should be
marked invalid.

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

The synthesis scheduler should use `RunAtLoad=false`. Installation should not
immediately run a real synthesis pass. The install script should print a first
dry-run smoke command and a manual `launchctl kickstart` command.

## Status Output

Extend `codex-self-evolution status` with synthesis-specific diagnostics:

- skill synthesis config status
- skill synthesis scheduler loaded/not loaded
- last synthesis receipt summary
- valid synthesized skill count
- invalid synthesized skill count
- retired synthesized skill count
- recent partial/error reasons
- compiler legacy skill counts, clearly separated from synthesized skill counts

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
8. Remove skill production from the compiler path:
   - remove skill guidance from the reviewer prompt
   - stop compiling or publishing `skill_action`
   - discard historical `skill_action` as `skill_action_disabled`
   - keep legacy skill modules disconnected until a later cleanup

## Test Plan

Required focused tests:

- unconfigured synthesis returns `skip_unconfigured`
- configured incremental mode applies hour lookback
- full mode applies day lookback with max 30-day default
- dry-run writes to a temporary skills root
- dry-run detects and errors on writes to the real skills root
- non-synth skill inventory reads frontmatter metadata only
- agent result and filesystem snapshot mismatch is recorded
- missing or invalid `result.json` produces partial receipt
- invalid skill writes `CSEP_INVALID.json` without deleting `SKILL.md`
- valid skill removes old `CSEP_INVALID.json`
- non-`csep-synth-*` directories are ignored and never modified
- retire is content-level and never removes directories
- retired skill must use standard retired format
- status reports synthesis separately from compiler-managed skills
- compiler no longer publishes generated skills
- historical `skill_action` is discarded without retry
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

This design does not claim complete read auditing for the synthesis agent. CSEP
controls the materialized input workspace and audits write results, but v1 does
not sandbox all possible reads by the local agent process.
