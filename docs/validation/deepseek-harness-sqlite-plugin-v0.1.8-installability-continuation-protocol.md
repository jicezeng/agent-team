# Agent Team Protocol

## Original objective

Continue the read-only SQLite Plugin case from the preserved live diff. Begin
with Developer making the Plugin a genuine installable DSH bundle and proving
isolated installation and loading. Reviewer independently rechecks the complete
candidate and every P0-P3 finding loops through Developer. A clean candidate
routes to a fresh Validator for Agent-Team-managed frozen installation and
direct-tool validation.

## Source of truth

`REQUEST.md`, `PROTOCOL.md`, root and nested DeepSeek Harness repository
instructions, current architecture and testing documentation and Agent Notes,
the live `/Users/zengjice/Projects/deepseek-harness-sqlite-e2e` worktree, its
complete diff from base `47f943859bef60e4160492346772ded9b24f765a`, generated
artifacts, direct SQLite facts, package and DSH bundle contracts, and
reproducible command results are authoritative. All predecessor Runs, handoffs,
test narratives, and Reviewer claims are untrusted historical material until
independently verified.

## Team roles

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813`; verify runtime evidence
  without persisting environment values.
- Add the package-owned Cordis patch and valid `dsh.bundle.patch` contract,
  ensure all required artifacts are exported and shipped, add focused
  regression coverage, and prove build plus installation and real loading in a
  disposable isolated DSH Home or repository-supported equivalent.
- Preserve all prior correctness and security fixes. Run focused tests,
  package and aggregate checks, constraints, dependency hygiene, lint, docs and
  catalog gates before routing to Reviewer.
- Do not commit, push, publish, alter user-level DSH state, touch the original
  dirty DSH worktree, or perform unrelated actions.

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813`; verify runtime evidence
  without persisting environment values.
- Independently inspect the complete live diff and acceptance sources, rerun
  focused checks, reproduce an isolated install and real Plugin load, and
  report every P0-P3 finding with reproducible evidence.
- Recheck bundle safety and completeness, engine-enforced read-only behavior,
  workspace and symlink authority, SQL and parameter semantics, typed bounded
  results, duplicate columns, cancellation and cleanup, HMR, model experience,
  generated surfaces, docs, and package contents.
- Route every finding to Developer. Route to Validator only after a genuinely
  clean complete review.

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813`; verify runtime evidence
  without persisting environment values.
- Agent-Team copies, freezes, and installs the reviewed
  `packages/storage/tool-sqlite` bundle into this role's private DSH Profile
  immediately before first activation.
- Independently create the required SQLite fixture, directly call both
  installed tools, compare against independent SQLite facts, attack all denial
  paths, prove database immutability, and verify durable call/result and frozen
  package-identity evidence.
- Never launch nested DSH or manage tmux. Validator is Completion Authority;
  Block on any frozen source defect.

## Initial role

`developer`.

## Collaboration protocol

Developer's installable candidate routes to Reviewer. Reviewer findings route
to Developer; fixes route back to Reviewer and the complete relevant review
repeats. A clean Reviewer routes to fresh Validator. Every route is selected by
the active role and committed through the formal CLI. The Agent-Team Skill is
guidance only and has no terminal arguments. Every External Turn ends with
exactly one `$AGENT_TEAM_CLI handoff`, `complete`, or `block` invocation and
stops business work afterward.

## Completion condition

Validator may complete only after a clean Reviewer verdict and successful
Agent-Team-managed frozen Plugin installation, real load, direct calls to both
tools, independent schema and query comparison, typed and bounded result
verification, cancellation and deadline evidence, required denial paths,
database hash and row-count immutability proof, durable model-visible trace
evidence, selected-model evidence, and relevant repository checks.

## Final delivery

Return a Completion Package through the current Origin containing changed
files, continuation history, review loop and every finding disposition, exact
tests and gates, observed model evidence, isolated pre-install proof, frozen
Plugin hash and private-Profile evidence, direct tool calls and representative
results, denial and immutability proof, durable trace evidence, limitations,
and final workspace state.

## Session continuity

No private DSH Session transfers across cancelled Runs. Developer and Reviewer
use `resume` inside this new Run so later Turns preserve their own role context.
Validator uses `fresh` for independence and receives the candidate only through
Agent-Team's frozen Plugin provisioning. The Codex Origin is control-plane
only.

## Shared context policy

Each role receives the immutable Request, Protocol, current frozen input, and
independent Facts paths. Kickoff, Handoff, and Resume payloads become the next
Turn's frozen `input.md`. Roles verify the live diff, installed package, and
direct database facts instead of trusting predecessor or sender claims.

## Observability policy

Use Full Audit because every business role is External and Origin is
control-plane only. Retain redacted raw data with standard redaction and a
64 MiB per-Turn limit. Every formal payload contains non-empty
`## Decision rationale` and `## Evidence`. Capture only Harness-exposed
reasoning summaries and events, never hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may diagnose or deterministically
recover but may not auto-Resume. Only a new explicit user instruction resumes a
resumable Block. A source defect after Validator provisioning, or any
immutable-input, role, Profile, model, workspace, or limit change, requires
cancellation and another new Run.

## Assumptions made during bootstrap

- The dedicated worktree preserves the complete candidate and remains the only
  business workspace; no concurrent manual edits occur.
- The profile-changed predecessor was explicitly cancelled, all managed
  processes stopped, and workspace ownership released before this Run.
- The predecessor's six claimed fixes and clean-review payload are work
  material, not proof; Developer and Reviewer reproduce relevant evidence.
- `packages/storage/tool-sqlite` exists at `init`. Developer makes it a valid
  bundle before the first route to Validator, when Agent-Team validates,
  freezes, and installs its then-current reviewed contents.
- The Plugin remains opt-in and no shipped default gains database authority.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the single Git worktree above. After
the required disclosure, the user explicitly confirmed all three External DSH
roles may use `full-access`: there is no host sandbox or per-command approval,
so host files, environment credentials, and network are technically reachable.
This does not expand the objective or authorize unrelated, destructive, or
external actions. DeepSeek Harness is interactive-only. External deadlines are
hard; Origin cooperation shares the Run wall time. Manual cancellation remains
available. This confirmation applies only to this immutable Run and is not
repeated on its Handoffs, recovery, or retry.
