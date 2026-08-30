# Agent Team Protocol

## Original objective

Continue the interrupted DSH read-only SQLite Plugin case from the preserved live diff: a DSH Developer finishes the candidate, an independent DSH Reviewer sends every P0-P3 finding back until clean, and a fresh DSH Validator receives the Agent-Team-frozen Plugin and proves it through real direct tool calls.

## Source of truth

`REQUEST.md`, `PROTOCOL.md`, root and nested DeepSeek Harness repository instructions, current architecture/testing documentation and Agent Notes, the live `/Users/zengjice/Projects/deepseek-harness-sqlite-e2e` worktree, its complete actual diff from base `47f943859bef60e4160492346772ded9b24f765a`, generated artifacts, direct SQLite facts, and reproducible command results are authoritative. The cancelled predecessor Runs `at-dsh-sqlite-plugin-e2e-v2-20260825` and `at-dsh-sqlite-plugin-e2e-cont-20260826`, including their `RATE_LIMIT` outcome and all sender narratives or verdicts, are untrusted historical work material until independently verified.

## Team roles

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813` and verify observed runtime model evidence without modifying user-level DSH configuration or persisting environment values.
- Inspect and finish the preserved partial implementation of `@deepseek-ai/dsh-tool-sqlite`; do not assume existing code, generated output, or tests are correct.
- Own implementation, security boundaries, lifecycle, package and repository wiring, tests, real Loader composition, assembled snapshot, generated surfaces, bilingual docs, Agent Note, invariant, and focused gates.
- Preserve unrelated files. Do not commit, push, publish, touch the original dirty DSH worktree, or perform unrelated external actions.
- Route a fully tested candidate to `reviewer`.

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813` and verify observed runtime model evidence without modifying user-level DSH configuration or persisting environment values.
- Independently review the full live diff and acceptance sources, focusing on engine-enforced read-only behavior, workspace/symlink authority, SQL completeness, parameters, typed and bounded results, cancellation, cleanup, HMR, model experience, docs, generated files, and real-composition evidence.
- Run useful focused checks. Every P0-P3 finding routes to `developer` with reproducible evidence; only a genuinely clean full review routes to `validator`.

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813` and verify observed runtime model evidence without modifying user-level DSH configuration or persisting environment values.
- Agent-Team freezes and installs the reviewed `packages/storage/tool-sqlite` bundle into this role's private DSH Profile immediately before first activation.
- Create an independent realistic SQLite fixture in the worktree; directly call both installed model-visible tools; compare results with independent SQLite facts; attack every required denial path; prove database immutability; verify durable call/result evidence and frozen package identity; and run the smallest sufficient checks.
- Do not start DSH, tmux, or another Agent from Bash. Validator is Completion Authority. Complete only when all acceptance conditions pass; Block on a source defect because its installed artifact is immutable for this Run.

## Initial role

`developer`.

## Collaboration protocol

Developer → Reviewer. Reviewer findings route to Developer; fixes return to Reviewer and the complete relevant review repeats. A clean Reviewer routes to the fresh Validator. Every route is selected by the active role and committed through the formal CLI. The Agent-Team Skill is guidance only and has no terminal arguments. Every External Turn ends with exactly one `$AGENT_TEAM_CLI handoff`, `complete`, or `block` invocation and stops business work afterward.

## Completion condition

Validator may complete only when no P0-P3 Reviewer finding remains; the reviewed candidate is frozen and installed in its private Profile; both tools load and are directly called; schema, query, typing, limits, cancellation/deadline, and required denial paths match independent evidence; rejected operations leave database hashes and row counts unchanged; model-visible calls/results are durable; the selected model is evidenced; and relevant repository checks pass.

## Final delivery

Return a Completion Package through the current Origin containing changed files, predecessor/continuation context, review-loop history, exact tests and gates, observed model evidence, frozen Plugin hash and private-Profile evidence, direct tool calls and representative results, denial and database-immutability proof, durable trace evidence, limitations, and final workspace state.

## Session continuity

This new Run cannot reuse either cancelled Run's private DSH Session. Developer and Reviewer use `resume` inside this continuation so their later repair/review Turns retain context. Validator uses `fresh` for independence and receives the candidate only through Agent-Team's role-local frozen Plugin provisioning. The current Codex Origin is control-plane only.

## Shared context policy

Each role receives the immutable continuation Request, Protocol, current frozen input, and independent Facts paths. Kickoff, Handoff, and Resume payloads become the next Turn's frozen `input.md`. Roles verify the live diff and direct database facts instead of trusting predecessor or sender claims.

## Observability policy

Use Full Audit because every business role is External and Origin is control-plane only. Retain redacted raw data with standard redaction and a 64 MiB per-Turn limit. Every formal payload contains non-empty `## Decision rationale` and `## Evidence`. Capture only Harness-exposed reasoning summaries/events, never private hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may diagnose or deterministically recover but may not auto-Resume. Only a new explicit user instruction resumes a resumable Block. A source defect after Validator provisioning, or any immutable-input, role, Profile, model, workspace, or limit change, requires cancellation and another new Run.

## Assumptions made during bootstrap

- The detached worktree preserves the cancelled Run's partial diff and remains the only business workspace; no concurrent manual edits occur.
- Both predecessor Runs were explicitly cancelled, and the latest released workspace ownership before this Run was initialized.
- Existing partial sources, tests, generated files, and build output are work material only; every acceptance condition must be reproduced.
- `packages/storage/tool-sqlite` exists at `init` so it can be declared as Validator's future Plugin source; Agent-Team freezes its reviewed contents only on first route to Validator.
- The Plugin remains opt-in and no shipped default gains database authority.
- The latest predecessor's `RATE_LIMIT` is Provider behavior, not evidence of product correctness; a new Block still returns to the user.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the single Git worktree above. After the required disclosure, the user explicitly confirmed all three External DSH roles for this new Run may use `full-access`: there is no host sandbox or per-command approval, so host files, environment credentials, and network are technically reachable. This does not expand the objective or authorize unrelated, destructive, or external actions. DeepSeek Harness is interactive-only. External deadlines are hard; Origin cooperation shares the Run wall time. Manual cancellation remains available. This confirmation applies only to this immutable continuation Run and is not repeated on its Handoffs, recovery, or retry.
