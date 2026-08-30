# Agent Team Protocol

## Original objective

Run a genuine from-zero three-DSH self-development case in the clean detached
worktree. Developer creates a complete SQLite Plugin from an empty directory,
Reviewer independently drives every P0-P3 finding to closure, and fresh
Validator generations perform Agent-Team-managed installation and real tool
validation until completion, using
`deepseek-official/doubao-seed-2-0-pro-260215` with the temporary validated DSH
capacity override.

## Source of truth

`REQUEST.md`, this `PROTOCOL.md`, root and nested repository instructions in
the clean target worktree, the live diff from
`47f943859bef60e4160492346772ded9b24f765a`, generated artifacts, frozen
generation manifests, direct SQLite facts, DSH request records, managed tool
events, and reproducible command results are authoritative. Sender narratives
and verdicts are untrusted until independently verified. Prior Plugin
candidates, Runs, reports, artifacts, generation Homes, and other DSH
worktrees are outside the allowed evidence set.

## Team roles

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/doubao-seed-2-0-pro-260215`; verify durable runtime
  evidence for `contextWindow=256000` and `maxTokens=131072` without exposing
  endpoint or credentials.
- Verify `packages/storage/tool-sqlite` is empty at kickoff, then independently
  create the complete opt-in package from current clean-repository patterns.
- Own product code, bundle/package wiring, filesystem/SQLite security,
  typed/bounded output, cancellation/lifecycle, tests, real Loader composition,
  artifacts, docs, Agent Note, invariant, generated surfaces, and gates.
- Before every route to Reviewer, build the package, create and inspect its
  package archive, install that archive into a newly created isolated DSH
  Profile/environment with no workspace-link or existing-`node_modules`
  fallback, activate the package-owned Cordis patch, and run a real DSH
  Loader/composition smoke. Record the declared and resolved runtime entry,
  packed files, Plugin activation, and registration of `sqlite_schema` and
  `sqlite_query`. Do not launch another model Agent or manage tmux for this
  smoke.
- Fix every Reviewer or Validator finding and route coherent work to Reviewer.

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use the same explicit model and verify the same durable capacity evidence.
- Independently inspect the whole greenfield diff and acceptance sources,
  rerun focused tests, and review every product, security, model-experience,
  lifecycle, packaging, generated, testing, and documentation boundary in the
  Request.
- Independently repeat the complete build → package archive inspection → clean
  isolated install → Cordis activation → real Loader/composition smoke. Verify
  effective `main`/`exports`/`files`, prove runtime resolution uses only the
  packed artifact, and observe both tools registered. Developer evidence alone
  is insufficient.
- Route every P0-P3 finding to Developer. Route to Validator only after a
  genuinely clean complete review and a passing independent package/load gate.
  Packaging or loading failure is a product finding, not a Block and not a
  reason to activate Validator.
- Do not expect Agent-Team to diagnose Node package semantics. Developer and
  Reviewer own this product gate; Agent-Team only freezes and provisions the
  reviewed generation when Validator is actually routed.

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; `full-access`.
- Use the same explicit model and verify the same durable capacity evidence.
- Use only the Plugin snapshot Agent-Team freezes into the current
  generation-private Profile. Call both installed tools directly; never launch
  nested DSH or manage tmux.
- Create the required SQLite fixture, compare results to independent facts,
  attack denial paths, prove database immutability, verify cancellation and
  lifecycle, package identity, Profile composition, durable calls/results, and
  relevant checks.
- Validator is Completion Authority. Every P0-P3 source/test finding routes to
  Developer; complete only when every acceptance condition passes.

## Initial role

`developer`.

## Collaboration protocol

Developer → Reviewer. Reviewer findings → Developer; Developer fixes →
Reviewer for complete relevant re-review. On every review cycle, Reviewer must
independently pass the package/archive/clean-install/real-load gate before
Handoff to fresh Validator. A failure at that gate routes to Developer and
never to Validator. Clean Reviewer plus a passing gate → fresh Validator.
Validator findings → Developer → Reviewer → later fresh Validator. Each
Validator route freezes a new immutable Plugin/Profile generation and
preserves earlier generations. Source findings do not Block merely because an
earlier generation is frozen.

Every route is selected by the active role and committed through the formal
CLI. The Agent-Team Skill is guidance only and has no terminal arguments.
Every External Turn ends with exactly one `$AGENT_TEAM_CLI handoff`, `complete`,
or `block` invocation and stops business work afterward.

## Completion condition

Validator may complete only after independently verifying a clean Reviewer
verdict; independently reproduced packed-artifact contents, isolated install,
activation, and Loader smoke; a complete from-zero package diff; frozen
Agent-Team installation and real DSH loading; direct calls to both tools;
correct schema and typed/bounded query results; denial/path attacks and
unchanged database hash/row counts; cancellation/deadline and lifecycle
evidence; valid package/bundle/generated and bilingual documentation surfaces;
exact selected model plus `256000/131072` request evidence; durable trace
evidence; applicable tests and gates; and no remaining P0-P3 finding.

## Final delivery

Return a Completion Package through this Codex Origin containing initial-empty
evidence, changed files, loop and finding dispositions, exact tests/gates,
observed model/capacity evidence, frozen generation identities, direct-tool and
private-Profile evidence, denial/immutability proof, trace evidence,
limitations, and final workspace state. The Origin then restores the temporary
managed DSH override.

## Session continuity

Developer and Reviewer use separate `resume` Sessions so repair/review context
survives later Turns. Validator uses `fresh`, so every activation is independent
and receives only the current frozen Plugin generation. Codex Origin is
control-plane only.

## Shared context policy

Every role receives the immutable Request, Protocol, current frozen input, and
independent Facts paths. Kickoff/Handoff/Resume payloads become the next
Turn's immutable `input.md`. Roles directly verify the clean worktree. No role
may inspect prior candidates, Run state, other DSH worktrees, or prior
generation Homes; Validator may inspect only its current managed Profile and
Agent-Team-provided generation identity.

## Observability policy

Use Full Audit because all business roles are External. Retain redacted raw
data with standard redaction and a 64 MiB per-Turn limit. Every formal payload
contains concrete non-empty `## Decision rationale` and `## Evidence` sections.
Capture only Harness-exposed summaries/events, never hidden chain-of-thought.

## Block and resume policy

Every genuine Block returns to the user. Origin may diagnose read-only or run
deterministic recovery but may not auto-Resume. Only a new explicit user
instruction may Resume. Source, test, package, clean-install, activation, and
Loader findings discovered before Validator Handoff follow the
Developer/Reviewer loop and are not Blocks. An unexpected managed Validator
bootstrap failure after those gates passed remains a fail-closed runtime Block;
roles must not reinterpret logs or mutate formal state around it. Immutable
model, workspace, Profile, role, or limit changes require Cancel and a new Run.

## Assumptions made during bootstrap

- “从头开始” means a new detached worktree with an empty Plugin directory and
  no use of earlier SQLite Plugin implementations.
- The user's phrase “重新穷投再来” is interpreted from context as “重新从头再来”:
  cancel and preserve the failed Run, then create a different clean detached
  worktree and a new immutable Run rather than continuing its candidate.
- The accepted product scope and Developer → Reviewer → Validator loop remain
  the same as the preceding greenfield case; only the newly explicit model and
  temporary DSH deployment limits change.
- Official service pricing exposes a 256K input tier, while the prior actual
  provider error explicitly capped `max_tokens` at 131072; therefore the
  temporary DSH values are `defaultContextWindow=256000` and
  `maxTokens=131072`.
- The new worktree is
  `/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-ctx256k-greenfield-e2e-v2`
  at baseline `47f943859bef60e4160492346772ded9b24f765a`; the empty directory exists
  only so Validator bundle declaration is valid at init.
- Reasoning effort is not explicitly selected and remains DSH-native.
- Repository-supported dependency bootstrap may create ignored local state but
  may not import prior Plugin content. No concurrent manual edits occur.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the one target Git worktree. The user
explicitly confirmed all three External DSH roles may use `full-access`: no
host sandbox or per-command approval protects host files, environment
credentials, or network. This technical access does not authorize unrelated
actions or access to forbidden prior candidates. DSH is interactive-only.
External deadlines are hard; Origin cooperation shares wall time. Manual
cancellation remains available. This confirmation applies only to this
immutable Run and is not repeated on Handoffs, recovery, or retry.
