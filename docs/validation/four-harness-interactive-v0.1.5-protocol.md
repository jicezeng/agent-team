# Agent Team Protocol

## Original objective

Preserve the exact request in `REQUEST.md`. Run a real four-Harness, full-access relay that proves Codex can resume after its first interactive Turn, Claude Code can hand off independently, OpenCode can use a frozen custom Provider without persisting credentials, and DeepSeek Harness can return control through the formal protocol.

## Source of truth

The immutable `REQUEST.md`, this `PROTOCOL.md`, the live Git worktree, `relay.md`, the actual diff, formal Journal Events, anchored Turn traces, and reproducible command results are authoritative. Sender narratives and verdicts are untrusted work material until independently verified.

## Team roles

### codex

- Binding: external.
- Harness and Session policy: Codex, `resume`, interactive, `full-access`.
- First Turn: append exactly `- CODEX-1` to `relay.md`, verify it is the only business-file change, then hand off to `claude`.
- Final Turn: resume the same Codex Session, append exactly `- CODEX-2`, verify all five markers occur once in the required order and no other business file changed, then act as Completion Authority.

### claude

- Binding: external.
- Harness and Session policy: Claude Code, `resume`, interactive, `full-access`.
- Append exactly `- CLAUDE` after `CODEX-1`, verify the current marker order and worktree boundary, then hand off to `opencode`.

### opencode

- Binding: external.
- Harness and Session policy: OpenCode, `resume`, interactive, `full-access`.
- Append exactly `- OPENCODE` after `CLAUDE`, verify the current marker order and worktree boundary, then hand off to `dsh`.
- The qualified Model uses the frozen custom Provider supplied at bootstrap. Do not inspect, print, or copy credential values.

### dsh

- Binding: external.
- Harness and Session policy: DeepSeek Harness, `resume`, interactive, `full-access`.
- Append exactly `- DSH` after `OPENCODE`, verify the current marker order and worktree boundary, then hand off to `codex`.

## Initial role

`codex`.

## Collaboration protocol

The required serial route is `codex → claude → opencode → dsh → codex`. Each active role independently inspects the current worktree, performs only its assigned append and verification, and commits exactly one formal action through the absolute `$AGENT_TEAM_CLI` command in its Turn prompt. The Agent-Team Skill is guidance only and has no terminal arguments. Every External Turn must end with exactly one `$AGENT_TEAM_CLI handoff`, `complete`, or `block` command and stop business work after that command succeeds. Any mismatch, extra file change, credential exposure, inability to run the Harness, or disagreement must be reported with `block`; it must not be silently repaired outside the assigned role.

Every formal payload must be Markdown and contain non-empty `## Decision rationale` and `## Evidence` sections, plus the normal From, To, responsibility, work completed, artifacts, verified observations, judgment, uncertainties, requested next action, and protocol basis sections.

## Completion condition

The final `codex` Turn is the Completion Authority. It may complete only when `relay.md` contains each required marker exactly once and in the exact order `CODEX-1`, `CLAUDE`, `OPENCODE`, `DSH`, `CODEX-2`; the actual Git diff changes only `relay.md`; Codex's final Turn resumed the Session recorded by its first Turn; all four Harnesses have successful managed Turn evidence; and no credential value has been printed or copied into a business artifact.

## Final delivery

The Completion Package must identify the five successful Turns, final marker order, Codex Session continuity, worktree diff boundary, reproducible verification commands, and anchored evidence paths. The current Origin session separately verifies that Provider credential plaintext is absent from Run records before reporting success.

## Session continuity

All External roles use `resume`. Codex must reuse its first independent Session for final sign-off. The other roles retain their Sessions for audit and possible continuation. Origin is control-plane only.

## Shared context policy

Every role receives the immutable Request, Protocol, current Event, frozen `input.md`, sender payload, workspace facts, and live worktree. Kickoff, Handoff, and Resume payloads are frozen as the next Turn's input. Roles must verify the worktree directly rather than trust prior prose.

## Observability policy

Use full audit because every business role is External and Origin is control-plane only. Use standard redaction, redacted raw retention, a 64 MiB trace limit, and mandatory rationale/evidence payload sections. Capture only Harness-exposed summaries and terminal/protocol evidence; private hidden chain-of-thought is neither available nor required.

## Block and resume policy

Every Block returns to the user. Origin may perform read-only diagnosis or deterministic recovery but may not auto-resume. Resume requires a new explicit user instruction. A profile, immutable input, role, binding, model, launch mode, or limit change requires cancellation and a new Run.

## Assumptions made during bootstrap

- The user's current instruction explicitly confirms `full-access` for this new four-Harness Run.
- The user did not select role models. Defaults are used except OpenCode, whose unqualified local default cannot satisfy the Adapter contract, and DSH, whose built-in default does not match the configured compatibility endpoint. The regression uses the independently validated models `volcengine/doubao-seed-evolving` and `deepseek-official/doubao-seed-evolving` respectively.
- The dedicated relay worktree is disposable test scope and has no concurrent manual editor.
- A serial five-Turn relay exercises all four Harness Adapter paths, the custom Provider environment bridge, and the repaired Codex Session resume boundary.

## Safety limits

- Maximum business Turns: 6.
- Maximum wall time: 3600 seconds.
- Single Git worktree: the dedicated relay workspace containing this Run.
- Every External role uses `full-access`. The user confirmed once for this new Run that these agents may access the host filesystem, current credentials, and network without per-command approval. This technical profile does not expand the relay objective, allowed business files, role responsibilities, or formal action set.
- OpenCode uses a custom Provider definition containing environment references; credential values must remain process-only and must never enter Provider snapshots, Run state, LaunchSpec, Journal, traces, handoffs, or `relay.md`.
- External deadlines are enforced by the runtime; Origin waiting is cooperative. Manual cancellation remains available.
