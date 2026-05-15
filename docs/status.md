# Codex Self-Evolution Plugin Status

## 2026-05-06 Enablement Baseline

- Recorded at: 2026-05-06 13:30:11 CST (+0800)
- Status timestamp: 2026-05-06T05:30:11Z
- Repository: `/Users/bytedance/code/github/codex-self-evolution-plugin`
- Runtime home: `/Users/bytedance/.codex-self-evolution`
- Conclusion: installed and enabled on this machine.

## Current Installation State

- CLI installed through uv tool: `codex-self-evolution-plugin v0.7.1`
- `codex-self-evolution` path: `/Users/bytedance/.local/bin/codex-self-evolution`
- `csep` path: `/Users/bytedance/.local/bin/csep`
- Codex CLI: `/opt/homebrew/bin/codex`, `codex-cli 0.128.0`
- opencode: `/opt/homebrew/bin/opencode`, `1.4.10`
- Provider env file exists: `/Users/bytedance/.codex-self-evolution/.env.provider`
- Config validation: `codex-self-evolution config validate` returned `status: ok`

## Codex Feature Flags

`/Users/bytedance/.codex/config.toml` currently has:

```toml
[features]
plugins = true
hooks = true
plugin_hooks = true
memories = true
```

## Hook And Scheduler State

- Plugin hook manifest exists: true
- Plugin hook file exists: true
- `SessionStart` declared: true
- `Stop` declared: true
- Hook commands use local CLI: true
- Hook commands use `uvx`: false
- Legacy user-level hooks in `~/.codex/hooks.json`: not installed, expected after plugin-hook migration
- launchd scheduler loaded: true
- scheduler label: `com.codex-self-evolution.preflight`
- scheduler plist: `/Users/bytedance/Library/LaunchAgents/com.codex-self-evolution.preflight.plist`

## Smoke Verification

After enabling `plugins`, `hooks`, and `plugin_hooks`, a normal `codex exec` run without temporary `--enable` flags triggered:

- `kind: session-start`
- `kind: stop-review`

The smoke review emitted no suggestions:

- `suggestion_count = 0`
- `memory_updates = 0`
- `recall_candidate = 0`
- `skill_action = 0`

Smoke-created empty pending suggestion and snapshot files were removed after verification.

## Recent Runtime Activity

From `codex-self-evolution status` at the recorded time:

- log available: true
- log path: `/Users/bytedance/.codex-self-evolution/logs/plugin.log`
- recent scan total in 24h window: 28
- recent scan failures/fallbacks: 0 reported in the summarized fields
- recent stop-review total in 24h window: 4
- recent stop-review succeeded: 4
- recent stop-review failed: 0
- reviewer retries in window: 0

## Known Residue

Current repo bucket still has 3 old `suggestions/processing/*.json` files from 2026-04-30 17:14:53:

- `9bed0ec1f6307e1a.json`
- `370362794a3ddd35.json`
- `14751c84e5ae007b.json`

These were not created by today's enablement smoke. Leave them untouched during the three-day trial unless they interfere with scheduler behavior.

## Three-Day Trial Plan

- Trial window starts: 2026-05-06 13:30 CST
- Trial window ends: 2026-05-09 13:30 CST
- Use Codex normally during the trial.
- Do not tune prompts, reviewer rules, recall rules, or skill promotion rules during the trial unless a blocker appears.
- After the trial, analyze the runtime trace before changing behavior.

Suggested analysis inputs after the trial:

```bash
codex-self-evolution status | python3 -m json.tool
tail -n 500 ~/.codex-self-evolution/logs/plugin.log
find ~/.codex-self-evolution/projects -path '*/suggestions/*/*.json' -mtime -4 -print
find ~/.codex-self-evolution/projects -path '*/compiler/last_receipt.json' -mtime -4 -print
```

Questions to answer after the trial:

- Did `SessionStart` and `Stop` trigger consistently in real sessions?
- Did reviewer suggestions have usable signal, or mostly empty/noisy output?
- Did scheduler compile pending suggestions without leaving new stuck processing files?
- Did recall get used in live turns, or does triggering still rely too much on manual/model behavior?
- Did generated skills become useful in practice, or remain mostly write-only assets?
