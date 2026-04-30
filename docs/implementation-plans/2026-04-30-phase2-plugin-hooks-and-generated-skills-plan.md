# Phase 2: Plugin Hooks and Generated Skills Plan

## Goal

Phase 2 makes the runtime loop feel native to Codex in two places:

1. Codex loads self-evolution hooks from the plugin manifest instead of from
   user-level hook injection.
2. Generated skills are promoted and projected so Codex can load them like
   normal skills and trigger them from their own `description` metadata.

This phase should keep the system automatic. The user should not manually move
generated skills into `~/.codex/skills`, manually edit `~/.codex/hooks.json`, or
manually approve every generated skill before it becomes active.

## Decisions

- Keep `install.sh`, but change its role.
- `install.sh` must use `uv` and install/update the local CLI.
- `install.sh` must not be the primary hook wiring mechanism anymore.
- Plugin hooks are declared through the Codex plugin manifest and plugin
  `hooks.json`.
- Hook commands call the local CLI by name, relying on PATH.
- `install.sh` must check that the local CLI and `csep` are available on the
  PATH visible to Codex, and print a concrete fix if not.
- `install.sh` may clean old csep entries from `~/.codex/hooks.json`, but only
  entries it previously owned.
- Generated skills are promoted automatically, with strict quality gates.
- Generated skills are globally visible by default.
- Generated skills project directly to
  `~/.codex/skills/csep-<skill-id>/SKILL.md`, which Codex loads like any
  normal skill directory.
- Stable skill ids update in place. A changed `skill_id` is a new skill.
- Phase 2 validation includes project tests, local Codex loader smoke, and a
  real Codex CLI manual smoke checklist.

## Runtime Shape

```text
Codex plugin enabled
  -> plugin manifest points to hooks.json
  -> SessionStart / Stop hooks run local CLI commands

SessionStart
  -> injects stable context and recall contract

Stop
  -> stores lightweight suggestions / review input

Scheduled scan
  -> codex-self-evolution scan --backend agent:opencode
  -> compile backend promotes memory, recall candidates, and generated skills

Generated skill promotion
  -> validate SKILL.md metadata and body quality
  -> write source of truth under plugin state
  -> project active skills into ~/.codex/skills/csep-*/SKILL.md

Next Codex session
  -> Codex skill loader sees csep-* skills
  -> model triggers them from normal skill metadata
```

## Plugin Hook Migration

The existing install script is still useful, but it should become runtime
preparation rather than hook injection.

Implementation tasks:

1. Move plugin hook definitions into the real plugin bundle layout.
2. Ensure the manifest `hooks` path points to the bundled `hooks.json` that
   Codex actually loads.
3. Replace placeholder hook commands with local CLI calls:

   ```bash
   codex-self-evolution session-start --from-stdin
   codex-self-evolution stop-review --from-stdin
   ```

4. Keep the hook commands static and PATH-based.
5. Update `install.sh` to install or update the local CLI with `uv`.
6. Update `install.sh` to verify:
   - `uv` exists
   - `codex-self-evolution --help` works
   - `csep --help` works
   - the uv tool bin directory is on PATH
7. Remove only old marker-managed user-level hook entries from
   `~/.codex/hooks.json`.
8. Update diagnostics so it can report both old user-level hook state and new
   plugin hook readiness.

## Generated Skill Promotion

Generated skills remain compiler-owned. Hooks do not synthesize skills.

The existing compile backend is the right place for Phase 2 synthesis. It
already receives pending suggestions and has the context needed to write final
assets. Do not add a separate `skill-synthesis` job in this phase.

Sources may include:

- explicit `skill_action` suggestions
- durable memory records
- recall candidates

Promotion is automatic, but a candidate must pass two hard gates:

1. **Complete skill structure**
   - valid `SKILL.md` frontmatter
   - `name` exists
   - `description` exists
   - `description` contains concrete trigger semantics
   - body contains procedural instructions or clear operating boundaries
2. **Signal quality**
   - not just a fact or preference
   - not too short
   - not a generic summary
   - has reusable value beyond the current turn

Other checks, such as provenance, conflicts, and dangerous-operation warnings,
can be recorded as metadata or warnings, but they are not Phase 2 hard
blockers.

## Projection Layout

The desired projection is:

```text
~/.codex/skills/
└── csep-<skill-id>/
    └── SKILL.md
```

This replaces the current nested layout if loader smoke shows that nested
directories are not treated like normal skills.

The source of truth remains under the self-evolution state bucket:

```text
~/.codex-self-evolution/projects/<bucket>/skills/
├── managed/
└── manifest.json
```

Publishing rules:

- active generated skills are written to the global Codex skill tree
- retired generated skills remove their projected directory
- updates keep the same `csep-<skill-id>` directory
- all generated skill directories must use the `csep-` prefix
- user-authored skills and third-party skills are never modified

## Validation

Automated tests in this repo:

- compile can discover a skill from memory / recall / skill candidates
- invalid or low-signal candidates are not promoted
- active skills project to `~/.codex/skills/csep-*/SKILL.md`
- retired skills remove their projection
- hook manifest and bundled `hooks.json` are internally consistent
- install script no longer injects new csep entries into `~/.codex/hooks.json`

Local Codex loader smoke:

- create a temporary `csep-smoke-*` skill under the target projection layout
- run the latest local Codex skill loader or a targeted Codex test
- confirm the generated skill is visible as a normal skill

Manual Codex CLI smoke:

1. Install/update the local CLI with `uv` through `install.sh`.
2. Enable the Codex plugin with `plugins`, `codex_hooks`, and `plugin_hooks`.
3. Start a new Codex session.
4. Confirm SessionStart / Stop plugin hooks run.
5. Run a task whose wording should trigger a generated `csep-*` skill.
6. Confirm Codex loads the skill like a normal skill.

## Non-Goals

- No manual skill moving.
- No bridge skill in Phase 2.
- No repo-scoped generated skill routing in Phase 2.
- No separate skill synthesis daemon or job in Phase 2.
- No broad multi-user migration support.
- No full deprecation of `install.sh`; only remove hook injection as its main
  responsibility.

## Implementation Order

1. Fix plugin bundle hook layout and manifest references.
2. Convert hook commands to local CLI calls.
3. Rework `install.sh` around `uv` local CLI install and old-hook cleanup.
4. Add generated-skill validation and full `SKILL.md` rendering.
5. Change projection to `~/.codex/skills/csep-*` if loader smoke requires it.
6. Add automated tests.
7. Run local Codex loader smoke.
8. Run manual Codex CLI smoke.
