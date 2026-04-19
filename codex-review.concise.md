## Summary
The design is directionally strong, but it is not implementation-ready. The main problem is that several platform assumptions are treated as available Codex primitives before they are verified, and the docs overstate both the immediacy of the loop and the fidelity of the Hermes comparison.

## Strengths
- The scope is disciplined: v1 limits itself to `memory / recall / skills / background review` and explicitly defers heavier runtime work such as compression and caching. `(design:12-28; brainstorm:20-36)`
- The recall split is sound: Codex thread history is positioned as source-of-truth, while the plugin keeps only a lightweight recall index rather than a second archive. `(design:101-129, 278-293; brainstorm:352-392, 468-499)`
- The managed-skill boundary is sensible: automatic edits are limited to system-owned skills, and review output is structured and separated from persistence. `(design:140-149, 244-252, 324-339; brainstorm:572-601)`

## Risks
- Core lifecycle steps rely on unverified Codex capabilities: `SessionStart`, `Stop`, `thread/read(includeTurns=true)`, `transcript_path`, and auto-triggered internal workflows are assumed to exist with specific payloads and timing, but the hook/app-server boundary is still unresolved. `(design:166-205, 225-240, 343-354; brainstorm:188-207, 262-291, 605-613)`
- The loop timing is internally inconsistent: the headline says results come back on the “next turn,” but memory is only reinjected on `SessionStart`, while mid-session updates are explicitly not pushed back into the active prompt. `(design:21-28, 89-95, 166-173, 207-217, 358-362; brainstorm:55-61, 201-207, 234-238, 620-622)`
- The Hermes comparison is still too strong: prompt-prefix injection plus post-turn reconstruction is materially weaker than a live in-agent snapshot fork, so “frozen snapshot equivalent” and “same capability goal” overstate what Codex can actually reproduce. `(design:56-72, 166-173; brainstorm:258-268, 293-315)`
- Global-only storage creates cross-project contamination risk: the design rejects project/workspace isolation, yet recall ranking already wants `cwd` and source-type signals, which means wrong-context retrieval and private-context leakage are likely unless scope is carried into every artifact. `(design:34-44, 270-293; brainstorm:116-130, 500-511)`
- The persistence model is underspecified: memory is described both as “write immediately when discovered” and as a post-turn review output, but there is no single write authority, no conflict rule when transcript and `thread/read` disagree, and no concurrency story for overlapping sessions. `(design:87-95, 188-205, 244-252, 347-353; brainstorm:53-61, 228-238, 285-291, 607-613)`

## Open Questions
- Which Codex extension points are verified in practice, and which are still assumptions? `(design:166-205, 343-354; brainstorm:262-267, 605-613)`
- What actually executes the “review runner,” and what are its latency, isolation, and failure semantics? `(design:225-252; brainstorm:278-291)`
- Which source is authoritative when `transcript`, `thread/read`, and `last_assistant_message` disagree or persist at different times? `(design:190-205, 235-252; brainstorm:280-291)`
- What provenance fields are mandatory on `memory`, `recall_entry`, and managed skills: repo, workspace, branch, commit, path, timestamp? `(design:270-293, 347-353; brainstorm:433-456, 500-511, 607-613)`
- How are recall and skill triggers budgeted and conflict-resolved when memory, recall, and a managed skill suggest different actions? `(design:207-211, 301-316, 347-354; brainstorm:553-566, 607-614)`

## Recommended Changes
- Add a short capability matrix and mark each dependency as `verified`, `assumed`, or `fallback`: `SessionStart`, `Stop` payload fields, transcript access, `thread/read` timing, background execution, and internal recall triggering.
- Rewrite the lifecycle claims to match actual timing: memory is “next session” unless explicit mid-session reinjection is added; recall and skills are best-effort async, not guaranteed “next turn.”
- Replace Hermes-equivalence language with approximation language, and list the fidelity gaps explicitly: no live agent state, no hidden reasoning, no exact in-memory snapshot.
- Keep storage global if needed, but make provenance and retrieval scope mandatory in v1: same-repo/default-first retrieval, plus repo/workspace/branch/path/time anchors on every persisted artifact.
- Define the write contract before implementation: one memory ingestion path, source-of-truth precedence, idempotent `turn_id` processing, locking/transactions, and promotion or rollback rules for managed skills.