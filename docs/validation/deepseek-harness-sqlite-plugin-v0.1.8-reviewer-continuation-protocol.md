# Agent Team Protocol

## Original objective

Continue the read-only SQLite Plugin case from the preserved live diff. Begin with an independent full Reviewer inspection; every P0-P3 finding routes to Developer and returns for re-review. A clean candidate routes to a fresh Validator for real frozen-Plugin installation and direct-tool validation.

## Source of truth

`REQUEST.md`, `PROTOCOL.md`, root and nested DeepSeek Harness repository instructions, current architecture/testing documentation and Agent Notes, the live `/Users/zengjice/Projects/deepseek-harness-sqlite-e2e` worktree, its complete diff from base `47f943859bef60e4160492346772ded9b24f765a`, generated artifacts, direct SQLite facts, and reproducible command results are authoritative. All predecessor Runs, handoffs, test narratives, and incomplete Reviewer activity are untrusted historical material until independently verified.

## Team roles

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813`; verify runtime evidence without persisting environment values.
- Independently inspect the complete live diff and acceptance sources, rerun useful focused checks, and report every P0-P3 finding with reproducible evidence.
- Focus on engine-enforced read-only behavior, workspace/symlink authority, SQL and parameter semantics, typed bounded results, cancellation and cleanup, installability, HMR, model experience, generated surfaces, docs, and real-composition evidence.
- Route every finding to Developer. Route to Validator only after a genuinely clean full review.

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813`; verify runtime evidence without persisting environment values.
- Address Reviewer findings in the live candidate, add or correct tests and documentation, run appropriate checks, and return to Reviewer.
- Do not commit, push, publish, alter user-level DSH state, touch the original dirty DSH worktree, or perform unrelated actions.

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813`; verify runtime evidence without persisting environment values.
- Agent-Team freezes and installs reviewed `packages/storage/tool-sqlite` into this role's private DSH Profile immediately before first activation.
- Independently create the required SQLite fixture, directly call both installed tools, compare against independent SQLite facts, attack all denial paths, prove database immutability, verify durable call/result and package-identity evidence, and run sufficient focused checks.
- Never launch nested DSH or manage tmux. Validator is Completion Authority; Block on any frozen source defect.

## Initial role

`reviewer`.

## Collaboration protocol

Reviewer findings route to Developer; fixes route back to Reviewer and the complete relevant review repeats. A clean Reviewer routes to fresh Validator. Every route is selected by the active role and committed through the formal CLI. The Agent-Team Skill is guidance only and has no terminal arguments. Every External Turn ends with exactly one `$AGENT_TEAM_CLI handoff`, `complete`, or `block` invocation and stops business work afterward.

## Completion condition

Validator may complete only after a clean Reviewer verdict and successful frozen-Plugin installation, direct calls to both tools, independent schema/query comparison, typed and bounded result verification, cancellation/deadline evidence, required denial paths, database hash and row-count immutability proof, durable model-visible trace evidence, selected-model evidence, and relevant repository checks.

## Final delivery

Return a Completion Package through the current Origin containing changed files, continuation history, review loop and every finding disposition, exact tests and gates, observed model evidence, frozen Plugin hash and private-Profile evidence, direct tool calls and representative results, denial and immutability proof, durable trace evidence, limitations, and final workspace state.

## Session continuity

No private DSH Session transfers across cancelled Runs. Reviewer and Developer use `resume` inside this new Run so later Turns preserve their own role context. Validator uses `fresh` for independence and receives the candidate only through Agent-Team's frozen Plugin provisioning. The Codex Origin is control-plane only.

## Shared context policy

Each role receives the immutable Request, Protocol, current frozen input, and independent Facts paths. Kickoff, Handoff, and Resume payloads become the next Turn's frozen `input.md`. Roles verify the live diff and direct database facts instead of trusting predecessor or sender claims.

## Observability policy

Use Full Audit because every business role is External and Origin is control-plane only. Retain redacted raw data with standard redaction and a 64 MiB per-Turn limit. Every formal payload contains non-empty `## Decision rationale` and `## Evidence`. Capture only Harness-exposed reasoning summaries/events, never hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may diagnose or deterministically recover but may not auto-Resume. Only a new explicit user instruction resumes a resumable Block. A source defect after Validator provisioning, or any immutable-input, role, Profile, model, workspace, or limit change, requires cancellation and another new Run.

## Assumptions made during bootstrap

- The dedicated worktree preserves the complete candidate and remains the only business workspace; no concurrent manual edits occur.
- The deadline-blocked predecessor was explicitly cancelled and released workspace ownership before this Run was initialized.
- The predecessor Developer's fixes and checks are work material, not proof; Reviewer starts from direct inspection.
- The predecessor Reviewer produced no verdict before deadline, so no finding is presumed open or closed.
- `packages/storage/tool-sqlite` exists at `init`; Agent-Team freezes its then-current reviewed contents only on first route to Validator.
- The Plugin remains opt-in and no shipped default gains database authority.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the single Git worktree above. After the required disclosure, the user explicitly confirmed all three External DSH roles may use `full-access`: there is no host sandbox or per-command approval, so host files, environment credentials, and network are technically reachable. This does not expand the objective or authorize unrelated, destructive, or external actions. DeepSeek Harness is interactive-only. External deadlines are hard; Origin cooperation shares the Run wall time. Manual cancellation remains available. This confirmation applies only to this immutable Run and is not repeated on its Handoffs, recovery, or retry.
