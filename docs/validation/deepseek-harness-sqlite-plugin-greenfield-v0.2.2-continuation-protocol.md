# Agent Team Protocol

## Original objective

Continue the preserved greenfield SQLite Plugin candidate in the v4 worktree.
Developer fixes all product defects, Reviewer independently drives every P0-P3
finding to closure without editing the candidate or narrowing the Request, and
fresh Validator generations perform Agent-Team-managed loading and direct tool
validation until completion. Use
`deepseek-official/doubao-seed-2-0-pro-260215` with the temporary validated DSH
capacity override.

## Source of truth

The v0.2.2 continuation `REQUEST.md`, this `PROTOCOL.md`, repository
instructions, the live worktree and complete diff from
`47f943859bef60e4160492346772ded9b24f765a`, generated artifacts, frozen current
generation manifests, direct SQLite/DSH tool facts, and reproducible commands
are authoritative. Sender narratives and earlier verdicts are untrusted. Old
Run stores, other worktrees/candidates, and previous generation Homes are not
business evidence.

## Team roles

### developer

- External DeepSeek Harness, `resume`, interactive, confirmed `full-access`.
- Use `deepseek-official/doubao-seed-2-0-pro-260215`; retain non-secret
  `contextWindow=256000` and `maxTokens=131072` evidence.
- Own all candidate changes. Fix the known invalid top-level bundle patch, then
  re-evaluate and implement the entire Request, especially real engine-level
  authorization, package isolation, security, lifecycle, tests, artifacts and
  documentation.
- Pass build, archive inspection, isolated archive-only install, patch
  activation, and real Loader composition before Reviewer.
- Repair every Reviewer or Validator P0-P3 finding and return to Reviewer.

### reviewer

- External DeepSeek Harness, independent `resume` Session, interactive,
  confirmed `full-access`, same model/capacity evidence.
- Never edit candidate product/test files. Reread the complete Request and
  inspect the entire current candidate every Turn.
- Independently repeat the archive-only gate and verify every requirement,
  especially the non-negotiable engine-level authorizer/opcode policy.
- Route every P0-P3 finding to Developer. Route Validator only after a clean
  full review and reproducible passing gate.

### validator

- External DeepSeek Harness, `fresh`, interactive, confirmed `full-access`,
  same model/capacity evidence; Completion Authority.
- Do not modify candidate product/test files. Use only the Plugin snapshot in
  the current private generation and invoke both installed tools directly.
- Build the independent disposable fixture; verify all positive, typed/bounded,
  security, immutability, cancellation, cleanup, package/profile and
  model-visible requirements.
- Route every P0-P3 finding to Developer; complete only when every condition is
  directly proven.

## Initial role

`developer`.

## Collaboration protocol

Developer → Reviewer. Reviewer findings → Developer → Reviewer. Clean full
Reviewer review plus independent archive-only gate → fresh Validator. Validator
findings → Developer → Reviewer → later fresh Validator. Every review cycle
compares the complete candidate to the entire Request; no finding may be closed
by deleting evidence, weakening acceptance, or trusting sender prose.

Every route is selected by the active role and committed through exactly one
successful `$AGENT_TEAM_CLI handoff`, `complete`, or `block` action. The Skill
is guidance only and has no terminal arguments. A rejected CLI call is not a
formal action.

`ROUTE_PREFLIGHT_REJECTED` occurs before Outbox/Event acceptance; the same Turn
routes that product finding to Developer with a new payload. When a frozen
candidate reaches the real loader but exits before the Fresh Validator Session
is durably initialized, Agent-Team consumes that failed generation and returns
an `Agent-Team Candidate Activation Finding` to the Reviewer that routed it.
Reviewer inspects the preserved candidate/trace and routes a confirmed product
defect to Developer; it Blocks only if evidence instead proves an infrastructure
failure. Agent-Team does not parse loader prose or duplicate DSH plugin rules.
The next Validator route receives a new immutable generation.

## Completion condition

Validator may Complete only after a clean full Reviewer verdict; independently
passing Developer and Reviewer archive-only gates; valid bundle activation and
real Loader composition; Agent-Team-managed frozen install and direct calls to
both tools; engine-level authorization and database-immutability proof; all
positive, typed/bounded, path, cancellation/deadline/lifecycle checks; valid
package, generated, bilingual and Agent Note surfaces; exact model/capacity and
durable trace evidence; applicable repository gates; and zero open P0-P3.

## Final delivery

Return a Completion Package through this Codex Origin with changed files,
finding dispositions, exact test/archive/Loader commands, selected model and
capacity, frozen generation identity, direct-tool evidence, engine authorization
and immutability proof, trace evidence, limitations and final worktree state.
Origin restores the temporary managed DSH capacity override after terminal
completion or cancellation when safe.

## Session continuity

Developer and Reviewer use distinct `resume` Sessions. Validator uses `fresh`,
and every later route gets a new frozen Plugin/Session generation. Origin is
control-plane only.

A structurally verified output-budget stop may create a counted same-role Turn
only for an available Developer/Reviewer Resume Session before any Block and
with all runtime gates satisfied. It grants no new authority. Candidate
Activation Findings are separate system Handoffs and do not resume failed
Validator Sessions. Ordinary post-Session crashes, permissions, audit failures,
existing Outboxes, exhausted limits, or repeated no-progress must Block.

## Shared context policy

Every role receives immutable Request, Protocol, current `input.md`, and trusted
Facts paths, and verifies the live worktree. No role may use another worktree,
old Run state, previous candidate, or previous generation Home as product
evidence. Validator sees only its current managed generation plus current Run
evidence.

## Observability policy

Use Full Audit because every business role is External and Origin is
control-plane only. Retain redacted raw data with standard redaction and a
64 MiB per-Turn limit. Every formal payload has concrete non-empty
`## Decision rationale` and `## Evidence`. Capture only Harness-exposed events
and summaries, never hidden chain-of-thought.

## Block and resume policy

Every committed Block returns to the user. Origin may perform read-only
diagnosis or deterministic recovery but never auto-Resume. Business findings,
pre-Outbox route rejection, Candidate Activation Findings, and valid pre-Block
output-limit continuation stay in the normal loop. Immutable input/profile/
model/role/workspace/limit changes require Cancel and a new Run.

## Assumptions made during bootstrap

- “继续” and the later explicit confirmation authorize a new continuation Run
  because the prior Run's immutable deadline expired; they do not authorize a
  new greenfield reset or reuse of old role Sessions.
- The current v4 worktree is the intended preserved candidate and has no
  concurrent manual edits.
- The explicit model remains
  `deepseek-official/doubao-seed-2-0-pro-260215`; reasoning effort remains DSH's
  native default.
- Temporary managed DSH capacity remains `256000/131072` for this validation.

## Safety limits

Maximum 18 business Turns and 7200 seconds in this single Git worktree. The user
explicitly confirmed all three DSH roles may use `full-access` for this Run.
That disables Harness host sandbox/per-command approval and permits host file,
credential, process and network access, but grants no unrelated, destructive,
publishing, old-artifact or external authority. DSH is interactive-only. The
confirmation is not repeated for Handoffs or safe automatic continuations.
