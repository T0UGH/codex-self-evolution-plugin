# Codex Self-Evolution Plugin Implementation Plan v2

## Status Reframe

This document replaces the earlier skeleton-first implementation plan.

Current project status should be described as:

> **v1-alpha pipeline skeleton is implemented, but the design-aligned v1 runtime is not complete yet.**

The repository already has:
- package structure, CLI surface, plugin metadata, and tests
- append-only suggestion persistence
- rule-based compiler and final-asset writer
- recall read path and managed-skill manifest scaffolding
- a documented gap audit in `docs/2026-04-19-implementation-gap-audit.md`

This v2 plan exists to close the **8 confirmed implementation gaps** and converge the codebase back toward the design in `docs/specs/2026-04-18-codex-self-evolution-plugin-design.md`.

---

## Goal

Ship a design-aligned v1 Codex self-evolution plugin with four working primitives:
1. stable memory background (`USER.md` / `MEMORY.md`)
2. contextual recall with repo/cwd-first behavior and in-turn triggering
3. managed procedural skills with explicit ownership boundaries
4. real post-turn background review that feeds a scheduled compiler pipeline

The target is no longer just “a working skeleton”, but a **working runtime loop**:

```text
SessionStart
  -> inject stable background + recall policy
Turn runs normally
  -> Stop hook emits minimal trigger context
Review runner reconstructs snapshot
  -> real reviewer produces structured suggestions
Scheduler triggers compiler
  -> compiler promotes memory / recall / managed skills
Next session / next turn
  -> memory returns as background
  -> recall can trigger under policy
  -> managed skill can be reused
```

---

## The 8 Gaps This Plan Must Close

### Core runtime gaps
1. reviewer is still a stub, not a real reviewer runtime
2. compiler has no real scheduled runtime
3. compiler is still rule-only, not a cheap-agent-capable compiler runtime

### Supporting runtime gaps
4. `SessionStart` does not truly inject `USER.md` / `MEMORY.md`
5. suggestion/event storage is still a file queue, not a fuller state model
6. review snapshot reconstruction is not really implemented
7. recall has no condition-triggered in-turn workflow
8. managed-skill ownership boundary is still too weak

---

## Design Alignment Rules

The following rules are fixed and should guide every implementation decision in this plan:

- Foreground turn code never writes final memory / recall / managed-skill artifacts.
- `Stop` only emits trigger context and/or pending suggestions; final writes remain compiler-owned.
- `SessionStart` injects stable background and recall policy, but does not preload large recall material.
- Recall storage may be global, but runtime retrieval must prefer same-repo and same-cwd provenance.
- Managed skills must be isolated from user-authored and third-party skills.
- Reviewer and compiler may use restricted external model runtimes, but both must remain single-purpose and schema-bounded.
- The system must remain usable without a long-lived daemon; the scheduled compiler should be external-runner friendly.

---

## Execution Strategy

The implementation should proceed in three major phases, in dependency order.

### Phase A — Make review input real
**Covers gaps:** #4, #6, #1

Goal:
- make `SessionStart` inject real stable background
- make post-turn review reconstruct a real snapshot
- make reviewer call a real provider instead of consuming mocked JSON

Rationale:
- Without real background injection and real snapshot reconstruction, a “real reviewer runtime” would still be running on fake inputs.

### Phase B — Make the compiler a real background runtime
**Covers gaps:** #5, #2, #3

Goal:
- upgrade the suggestion store from a bare pending/processed queue into a stronger state model
- add real scheduler integration and graceful skip behavior
- define whether compiler remains rule-first with optional model assistance, or becomes a cheap-agent compiler for selected steps

Rationale:
- The compiler is the only final writer. If it remains single-shot, weakly stateful, and purely local-rule-based, the runtime loop is still only half-real.

### Phase C — Make recall and skills behave like runtime capabilities
**Covers gaps:** #7, #8

Goal:
- add in-turn recall triggering around the existing read path
- harden managed-skill boundaries so automated lifecycle actions only affect plugin-owned assets

Rationale:
- This is where the system becomes a reusable capability instead of just a background archive.

---

## Phase A — Real Review Input and Reviewer Runtime

### Objective
Turn `SessionStart` + `Stop` + reviewer into a real end-to-end review path grounded in stable memory and reconstructed turn context.

### Gaps Closed
- #4 `SessionStart` real memory injection
- #6 review snapshot reconstruction
- #1 real reviewer runtime

### Files to Create / Modify
- `src/codex_self_evolution/hooks/session_start.py`
- `src/codex_self_evolution/hooks/stop_review.py`
- `src/codex_self_evolution/review/runner.py`
- `src/codex_self_evolution/review/prompt.md`
- `src/codex_self_evolution/config.py`
- `src/codex_self_evolution/storage.py`
- `src/codex_self_evolution/schemas.py`
- `src/codex_self_evolution/cli.py`
- `src/codex_self_evolution/memory/` (new helper module package if needed)
- `tests/test_session_start.py`
- `tests/test_stop_review.py`
- `tests/test_reviewer_runner.py` (new)
- `tests/fixtures/session_start/`
- `tests/fixtures/stop_review/`
- `README.md`

### Implementation Tasks

#### A1. Implement stable background injection in `SessionStart`
- Read `USER.md` and `MEMORY.md` from the canonical state location.
- Keep recall policy preload, but change output shape from “policy only” to:
  - stable background section
  - recall policy section
  - minimal runtime metadata needed by the session
- Do **not** preload large recall content.
- Establish a minimal deterministic output template so tests can assert exact shape.

#### A2. Implement real review snapshot reconstruction
- Treat the `Stop` hook payload as a trigger envelope, not the full source of truth.
- Rebuild review input by resolving, in order:
  1. transcript payload or transcript path
  2. `thread/read(includeTurns=true)` output when available
  3. `USER.md`
  4. `MEMORY.md`
  5. managed-skills summary
  6. repo/cwd/session metadata
- Persist a normalized review-input artifact for debugging and deterministic tests.
- Keep source-authority precedence explicit and documented.

#### A3. Replace stub reviewer with provider-backed single-pass runtime
- Refactor `run_reviewer()` to support at least one real provider adapter.
- Provider interface should be narrow:
  - input: normalized review snapshot + fixed prompt
  - output: strict JSON matching schema
  - failure path: structured error / no suggestion, never silent malformed success
- Keep a test double for fixtures, but make it explicit test-only behavior.
- Add timeout, output-size cap, schema validation, and malformed-response handling.

#### A4. Add managed-skill summary read path for review context
- Summarize existing managed skills so reviewer can detect “new pattern” vs “already captured pattern”.
- Keep this lightweight; it is review context, not full skill body injection.

### Verification Steps
```bash
pytest tests/test_session_start.py tests/test_stop_review.py tests/test_reviewer_runner.py
python -m codex_self_evolution.cli session-start --cwd tests/fixtures/session_start/repo
python -m codex_self_evolution.cli stop-review --hook-payload tests/fixtures/stop_review/stop_payload.json
```

### Exit Criteria
- `SessionStart` injects `USER.md`, `MEMORY.md`, and recall policy in a deterministic structure.
- `Stop` no longer depends on pre-baked `reviewer_output` to behave correctly.
- Reviewer runtime can call a real provider and either return valid structured suggestions or a clearly handled failure result.
- Tests cover source-authority precedence, snapshot reconstruction, and malformed-model-output handling.

---

## Phase B — Real Compiler Runtime, Scheduler, and State Model

### Objective
Upgrade the compiler from a one-shot rule compiler into a real scheduled runtime with stronger state handling and a clear path for selective low-cost intelligence.

### Gaps Closed
- #5 stronger suggestion/event state model
- #2 real scheduler runtime
- #3 compiler intelligence runtime decision and implementation

### Files to Create / Modify
- `src/codex_self_evolution/storage.py`
- `src/codex_self_evolution/compiler/engine.py`
- `src/codex_self_evolution/compiler/memory.py`
- `src/codex_self_evolution/compiler/recall.py`
- `src/codex_self_evolution/compiler/skills.py`
- `src/codex_self_evolution/writer.py`
- `src/codex_self_evolution/cli.py`
- `src/codex_self_evolution/config.py`
- `src/codex_self_evolution/compiler/providers.py` (new, if agent-assisted compiler is adopted)
- `tests/test_storage_state_machine.py` (new)
- `tests/test_compiler_memory.py`
- `tests/test_compiler_recall.py`
- `tests/test_compiler_skills.py`
- `tests/test_writer.py`
- `tests/test_scheduler_integration.py` (new)
- `.codex-plugin/plugin.json` if command exposure changes
- `README.md`
- `docs/` scheduler installation examples

### Implementation Tasks

#### B1. Upgrade storage to an explicit state model
- Keep filesystem-backed v1 if desired, but model explicit states:
  - `pending`
  - `processing`
  - `done`
  - `failed`
  - `discarded`
- Add stable suggestion ids / idempotency keys.
- Add retry metadata and failure reason capture.
- Preserve append-only provenance even if storage remains file-based.
- Avoid forcing SQLite unless it materially simplifies correctness; filesystem state machine is acceptable if semantics are explicit.

#### B2. Add scheduler-facing compiler runtime
- Keep `compile --once`, but add documented scheduler integration as a first-class path.
- Define lock behavior:
  - if compile lock exists and is healthy, exit cleanly with skip semantics
  - if lock is stale, recover safely
- Ship at least one blessed scheduler example in docs and templates.
- Make outputs observable: receipts, logs, and exit codes should distinguish success / skip / failure.

#### B3. Decide and implement compiler intelligence mode
Two acceptable v1 outcomes:
1. **Rule-first compiler with optional cheap-model assistance** for merge/dedupe/refinement steps.
2. **Cheap-agent compiler** for narrowly bounded compile tasks.

Whichever path is chosen:
- keep final write ownership in `writer.py`
- bound inputs and outputs tightly
- require deterministic fallback when provider is unavailable
- preserve provenance from raw suggestions to final artifacts

#### B4. Harden compiler receipts and observability
- Record which suggestions were promoted, merged, failed, or discarded.
- Make re-runs idempotent and auditable.

### Verification Steps
```bash
pytest tests/test_storage_state_machine.py tests/test_compiler_memory.py tests/test_compiler_recall.py tests/test_compiler_skills.py tests/test_writer.py tests/test_scheduler_integration.py
python -m codex_self_evolution.cli compile --once --state-dir data
python -m codex_self_evolution.cli compile --once --state-dir data
```

### Exit Criteria
- Compiler storage semantics include explicit processing, done, failed, and discarded outcomes.
- Scheduler docs and runnable examples exist for at least one blessed scheduler.
- Lock contention produces graceful skip behavior instead of opaque failure.
- Compiler can run repeatedly without duplicate promotion.
- Compiler intelligence path is explicitly chosen, implemented, and documented.

---

## Phase C — Recall Trigger Runtime and Managed-Skill Boundaries

### Objective
Turn recall and managed skills from passive artifacts into governed runtime capabilities.

### Gaps Closed
- #7 recall trigger workflow
- #8 managed-skill ownership boundary

### Files to Create / Modify
- `src/codex_self_evolution/recall/search.py`
- `src/codex_self_evolution/recall/policy.md`
- `src/codex_self_evolution/hooks/session_start.py`
- `src/codex_self_evolution/managed_skills/manifest.py`
- `src/codex_self_evolution/compiler/skills.py`
- `src/codex_self_evolution/writer.py`
- `src/codex_self_evolution/schemas.py`
- `tests/test_recall_search.py`
- `tests/test_recall_trigger_policy.py` (new)
- `tests/test_managed_skill_boundaries.py` (new)
- `tests/test_end_to_end.py`
- `README.md`

### Implementation Tasks

#### C1. Add condition-triggered recall workflow
- Keep explicit `recall` CLI for debugging.
- Add a policy-driven bridge so recall can be triggered during a turn when conditions are met.
- Preserve same-repo, then same-cwd subtree, then global fallback ordering.
- Emit focused recall summaries rather than raw large history dumps.

#### C2. Strengthen managed-skill ownership metadata
- Add explicit manifest metadata such as:
  - `owner`
  - `managed`
  - `created_by`
  - `updated_at`
  - `retired_at` when applicable
- Ensure only plugin-owned, managed skills can be auto-created, patched, edited, or retired.
- Separate managed-skill storage path from any user/third-party path if not already isolated enough.

#### C3. Tighten end-to-end lifecycle rules
- Reviewer can propose skill actions.
- Compiler decides promotion.
- Writer mutates only managed-skill targets owned by the plugin.
- Recall may consult managed-skill summaries, but cannot mutate skills.

### Verification Steps
```bash
pytest tests/test_recall_search.py tests/test_recall_trigger_policy.py tests/test_managed_skill_boundaries.py tests/test_end_to_end.py
python -m codex_self_evolution.cli recall --query "topic" --cwd tests/fixtures/end_to_end/repo
```

### Exit Criteria
- Recall can be triggered through policy-driven in-turn conditions, not just manual CLI invocation.
- Managed skills have explicit ownership metadata and safe mutation boundaries.
- End-to-end tests verify reviewer -> pending suggestion -> compiler -> managed skill / recall / memory -> next-session reuse.

---

## Testing Strategy

The test matrix should now validate **runtime truth**, not just skeleton shape.

### Unit / component coverage
- schema validation and malformed JSON rejection
- `SessionStart` output template and memory injection behavior
- review snapshot reconstruction and source-authority order
- reviewer provider adapter behavior, timeout handling, malformed output handling
- explicit suggestion state transitions and idempotency behavior
- compiler merge / dedupe / promotion / retry / discard behavior
- recall ranking and trigger-policy behavior
- managed-skill boundary enforcement and manifest metadata rules

### End-to-end coverage
- session start injects stable background
- stop hook reconstructs review snapshot
- reviewer emits suggestions through a real or test provider adapter
- scheduler-triggered compile promotes artifacts
- next session and next turn can reuse the results correctly

### Concrete verification commands
```bash
pytest tests/test_schemas.py \
  tests/test_session_start.py \
  tests/test_stop_review.py \
  tests/test_reviewer_runner.py \
  tests/test_storage_state_machine.py \
  tests/test_compiler_memory.py \
  tests/test_compiler_recall.py \
  tests/test_compiler_skills.py \
  tests/test_writer.py \
  tests/test_recall_search.py \
  tests/test_recall_trigger_policy.py \
  tests/test_managed_skill_boundaries.py \
  tests/test_end_to_end.py
```

---

## Confirmed Decisions From 贵平

The following decisions are now fixed unless explicitly changed later.

### Decision 1 — Reviewer provider abstraction
- Reviewer runtime should be abstracted behind a provider interface.
- The implementation must support multiple providers over time.
- It must not be hard-bound to Minimax.
- The first concrete provider formats to support should be:
  - OpenAI-compatible / OpenAPI-style chat-completions format
  - Anthropic-style messages format

Implementation consequence:
- `review/runner.py` should depend on a narrow provider adapter contract.
- Provider-specific payload formatting must live behind adapters, not in the hook or runner core.
- Tests should cover provider-independent behavior plus adapter-specific serialization.

### Decision 2 — Scheduler route is `launchd`
- v1 scheduler path should use `launchd`, not `cron`.
- Scheduler flow should be demand-aware:
  1. first determine whether there is pending work
  2. only when work exists, launch an `opencode`-based processing step
- The scheduler layer should therefore separate:
  - lightweight wake/check logic
  - actual compile execution logic

Implementation consequence:
- add a cheap preflight check such as “any pending/process-worthy suggestion exists?”
- ensure launchd jobs do not spin up expensive runtime when the queue is empty
- document launchd plist generation / installation / reload flow in README or docs

### Decision 3 — Compiler should be pluggable
Compiler implementation should be abstracted behind multiple backends.

Initial backend family:
1. **script compiler** — pure local script / rule-based compiler
2. **agent compiler** — launches an `opencode` agent for bounded compile work

Implementation consequence:
- keep final write ownership in `writer.py`
- keep a shared compiler input/output contract across backends
- allow backend selection by config or CLI flag
- preserve deterministic fallback to script compiler when agent runtime is unavailable

---

## Decisions That Do Not Need 贵平 Right Now

These can be implemented with sensible defaults unless later evidence says otherwise:
- keep filesystem-backed storage for v1, as long as explicit state semantics are added
- use deterministic markdown sections for `SessionStart` injection format
- keep explicit `recall` CLI alongside the new trigger runtime for debugging
- add managed-skill metadata in manifest first, even before any deeper refactor of skill storage layout
- persist normalized review-input artifacts for debugging and auditability

---

## Definition of Done

The project can only be called **design-aligned v1 complete** when all of the following are true:
- `SessionStart` injects stable memory background plus recall policy
- `Stop` reconstructs real review input instead of consuming pre-baked reviewer output
- reviewer runtime calls a real provider and produces validated structured suggestions
- compiler runs under an actual scheduler-facing workflow with graceful lock skip behavior
- suggestion storage has explicit state semantics beyond pending/processed only
- recall can trigger under policy during a turn and still prefers same-repo / same-cwd provenance
- managed skills are clearly plugin-owned and safely isolated from user/third-party skills
- end-to-end tests cover the full loop and pass

Until then, the repository should continue to be described as:

> **self-evolution pipeline skeleton / v1-alpha**, not final v1.
