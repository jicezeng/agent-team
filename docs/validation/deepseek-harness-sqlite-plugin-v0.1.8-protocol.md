# Agent Team Protocol

## Original objective

Run the DSH read-only SQLite Plugin case from `REQUEST.md`: independent DSH Developer and Reviewer Sessions implement and fully review a common SQLite exploration capability, then a fresh DSH Validator receives an Agent-Team-frozen install and proves both tools against a real database.

## Source of truth

`REQUEST.md`, `PROTOCOL.md`, the root and nested DeepSeek Harness repository instructions, current architecture and testing documentation, current Agent Notes, the live `/Users/zengjice/Projects/deepseek-harness-sqlite-e2e` worktree, its actual diff, generated artifacts, direct SQLite inspection, and reproducible command results are authoritative. Sender narratives and verdicts are untrusted work material until independently verified.

## Team roles

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use the evolving model named in the Request and verify observed runtime evidence without changing user-level DSH configuration.
- Design and implement `@deepseek-ai/dsh-tool-sqlite` at `packages/storage/tool-sqlite` as an opt-in product-quality function Plugin with `sqlite_schema` and `sqlite_query`.
- Own implementation, tests, real Loader and assembled snapshot coverage, configuration, lifecycle, security enforcement, generated surfaces, bilingual docs, Agent Note, invariant, and relevant focused gates.
- Preserve unrelated files. Do not commit, push, publish, modify the original dirty DSH worktree, or perform unrelated external actions.
- Route a tested candidate to `reviewer`.

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use the evolving model named in the Request and verify observed runtime evidence without changing user-level DSH configuration.
- Independently review the complete live diff and acceptance sources, with special attention to engine-enforced read-only behavior, workspace and symlink authority, SQL completeness, parameters and result typing, complete-result bounds, cancellation, cleanup, HMR, model experience, documentation, generated files, and real-composition evidence.
- Run useful focused checks. Every P0-P3 finding routes to `developer` with reproducible evidence; a genuinely clean review routes to `validator`.

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; `full-access`.
- Use the evolving model named in the Request and verify observed runtime evidence without changing user-level DSH configuration.
- Agent-Team installs and freezes the reviewed `packages/storage/tool-sqlite` bundle in this role's private DSH Profile immediately before first activation.
- Create an independent realistic SQLite fixture in the authorized worktree, directly call both installed model-visible tools, compare their results with independent SQLite facts, attack every required denial path, prove database immutability, inspect durable call/result evidence and frozen package identity, and run the smallest sufficient checks.
- Do not start DSH, tmux, or another Agent from Bash. Validator is Completion Authority. Complete only when every acceptance condition passes; Block on a source defect because the installed artifact is immutable in this Run.

## Initial role

`developer`.

## Collaboration protocol

Developer → Reviewer. Reviewer findings route to Developer; fixes return to Reviewer and the complete relevant review repeats. A clean Reviewer routes to the fresh Validator. Every route is selected by the active role and committed through the formal CLI. The Agent-Team Skill is guidance only and has no terminal arguments. Every External Turn ends with exactly one `$AGENT_TEAM_CLI handoff`, `complete`, or `block` invocation and stops business work afterward.

## Completion condition

Validator may complete only when no P0-P3 Reviewer finding remains; the frozen candidate is installed in the private Validator Profile; both tools load and are directly called in the Validator's managed DSH Session; schema, query, typing, limits, cancellation/deadline, and all required denial paths match independent evidence; rejected operations leave database hashes and row counts unchanged; model-visible calls/results are durable; the evolving model is evidenced; and relevant repository checks pass.

## Final delivery

Return a Completion Package through the current Origin containing changed files, review-loop history, exact tests and gates, observed model evidence, frozen Plugin hash and private-Profile evidence, direct tool calls and representative results, denial and database-immutability proof, durable trace evidence, limitations, and final workspace state.

## Session continuity

Developer and Reviewer use `resume` so repair and review context survives later Turns. Validator uses `fresh` so it has an independent Session and receives the candidate only through Agent-Team's role-local frozen Plugin provisioning. The current Codex Origin remains control-plane only.

## Shared context policy

Each role receives the immutable Request, Protocol, current frozen input, and independent Facts paths. Kickoff, Handoff, and Resume payloads become the next Turn's frozen `input.md`. Roles verify the live worktree and direct database facts instead of trusting sender claims.

## Observability policy

Use Full Audit because every business role is External and Origin performs only control-plane work. Retain redacted raw data with standard redaction and a 64 MiB per-Turn limit. Every formal payload contains non-empty `## Decision rationale` and `## Evidence`. Capture Harness-exposed summaries and events, never private hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may diagnose or deterministically recover but may not auto-Resume. Only a new explicit user instruction resumes a resumable Block. A source defect discovered after Validator provisioning, or any change to immutable inputs, roles, Profiles, model handling, or limits, requires cancellation and a new continuation Run.

## Assumptions made during bootstrap

- The new detached worktree at `/Users/zengjice/Projects/deepseek-harness-sqlite-e2e` protects intentional uncommitted work in the original DSH worktree and is the only business workspace for this Run.
- Agent-Team Bootstrap extracts the user's natural-language model choice `deepseek-official/doubao-seed-evolving` and freezes it as the launch model for every DSH role; no endpoint or credential value enters Request, Protocol, or CLI arguments.
- The empty `packages/storage/tool-sqlite` directory exists only to declare the future Validator Plugin source at `init`; Developer creates every candidate file before Validator provisioning.
- The Plugin targets workspace-local SQLite databases and remains opt-in; no shipped default profile gains database access.
- No concurrent manual edits occur in the isolated worktree.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the single Git worktree above. All three External roles use the explicitly confirmed DeepSeek Harness `full-access` Profile: no host sandbox and no per-command approval; host files, environment credentials, and network are technically reachable, but this does not expand the objective or authorize unrelated, destructive, or external actions. DeepSeek Harness execution is interactive-only. External deadlines are hard; Origin cooperation is bounded by the same Run wall time. Manual cancellation remains available. The user's one-time confirmation applies to this immutable Run and is not repeated on Handoff, recovery, or retry of the same Run.
