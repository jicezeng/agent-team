# Agent Team Protocol

## Original objective

Run a genuine from-zero three-DSH self-development case in the clean detached
v4 worktree. Developer creates a complete SQLite Plugin from an empty directory,
Reviewer drives every P0-P3 finding to closure without narrowing the immutable
Request, and fresh Validator generations perform Agent-Team-managed installation
and real tool validation until completion. Use
`deepseek-official/doubao-seed-2-0-pro-260215` with the temporary validated DSH
capacity override.

## Source of truth

The v0.2.1 `REQUEST.md`, this `PROTOCOL.md`, repository instructions in the
clean target worktree, the complete live diff from
`47f943859bef60e4160492346772ded9b24f765a`, generated artifacts, frozen
generation manifests, direct SQLite facts, DSH request/tool events, and
reproducible command results are authoritative. Sender narratives and verdicts
are untrusted until independently verified. Earlier Plugin candidates, Runs,
reports, generation Homes and other worktrees are forbidden evidence.

## Team roles

### developer

- External DeepSeek Harness, `resume`, `full-access`, interactive.
- Use `deepseek-official/doubao-seed-2-0-pro-260215`; record durable
  `contextWindow=256000` and `maxTokens=131072` evidence without credentials.
- Confirm `packages/storage/tool-sqlite` is empty at kickoff and independently
  own the whole implementation, package/bundle, engine-level SQLite policy,
  path security, typed/bounded output, lifecycle, tests, Loader composition,
  artifacts, bilingual docs, Agent Note, invariant, generated surfaces and
  gates.
- Before Reviewer, pass build → archive inspection → isolated archive-only
  install → package patch activation → real Loader composition, resolving code
  from the archive and observing both tools. Do not launch another Agent or
  manage tmux.
- Repair every Reviewer or Validator P0-P3 finding and return to Reviewer.

### reviewer

- External DeepSeek Harness, `resume`, `full-access`, interactive, same model
  and durable capacity evidence.
- On every Reviewer Turn, reread the complete original Request and inspect the
  entire current diff. The preceding finding list does not narrow scope.
- Independently repeat the archive-only install/activation/Loader gate, prove
  runtime resolution uses only the packed artifact, and observe both tools.
- Verify every product boundary, especially an engine-level authorizer/opcode
  policy. Text parsing alone is insufficient. Missing binding support requires
  a different implementation or an open finding; it never authorizes weakening
  the Request, removing tests, or accepting a narrower claim.
- Route every P0-P3 finding to Developer. Route Validator only after a clean
  full review and passing independent product gate.

### validator

- External DeepSeek Harness, `fresh`, `full-access`, interactive, same model
  and durable capacity evidence.
- Use only the Plugin snapshot frozen into the current private generation.
  Invoke both installed tools directly; never start nested DSH or manage tmux.
- Create the required independent SQLite fixture, verify positive and typed/
  bounded behavior, attack every denial/path boundary, prove database
  immutability and cleanup, verify generation/package identity and durable
  trace, and rerun relevant gates.
- Validator is Completion Authority. Every P0-P3 finding routes Developer →
  Reviewer → a later fresh Validator; complete only when every condition passes.

## Initial role

`developer`.

## Collaboration protocol

Developer → Reviewer. Reviewer findings → Developer → Reviewer. A clean full
Reviewer verdict and independently passing product gate → fresh Validator.
Validator findings → Developer → Reviewer → later fresh Validator. Each
Validator route freezes a new immutable Plugin/Profile generation and preserves
earlier generations. Source, test, packaging and installability defects are
normal findings, not Blocks.

Every route is selected by the active role and committed through the formal
CLI. Each External Turn ends with one successful `$AGENT_TEAM_CLI handoff`,
`complete`, or `block` action and stops business work afterward. A rejected CLI
invocation is not a successful action. Specifically,
`ROUTE_PREFLIGHT_REJECTED` occurs before Outbox/Event acceptance; the same Turn
must turn the reported product defect into a new payload and Handoff to the
repair role. Profile drift or post-Outbox change remains fail-closed.

The Agent-Team Skill is guidance only and has no terminal arguments. Roles do
their own in-scope implementation, test, packaging, review and validation work
rather than asking Origin to do it.

## Completion condition

Validator may complete only after independently verifying: a clean full
Reviewer verdict; immutable engine-level authorization and all denial/path
boundaries; packed contents, archive-only isolated install, activation and real
Loader smoke; a complete greenfield diff; Agent-Team-managed frozen installation
and real DSH loading; direct calls to both tools; correct schema and typed/
bounded query output; unchanged database hash/rows after attacks; cancellation,
deadline and lifecycle behavior; valid package, bundle, generated and bilingual
surfaces; exact model/capacity evidence; durable traces; applicable gates; and
no remaining P0-P3 finding.

## Final delivery

Return a Completion Package through this Codex Origin containing initial-empty
evidence, changed files, loop/finding dispositions, exact tests and product
gates, selected model/capacity, frozen generation identities, direct-tool and
private-Profile evidence, engine authorization and immutability proof, trace
evidence, limitations and final worktree state. Origin restores the temporary
managed DSH capacity override after terminal completion or cancellation when
safe.

## Session continuity

Developer and Reviewer use distinct `resume` Sessions. Validator uses `fresh`;
each activation gets a newly frozen Plugin generation. Codex Origin is
control-plane only.

A structurally reported output-budget stop may create a new counted same-role
Turn only for an available Developer/Reviewer resume Session and only before a
Block, with all runtime gates satisfied. It grants no new authority. Fresh
Validator, ordinary crashes, permission/audit failures, existing Outboxes,
exhausted limits, stale Sessions, or repeated no-progress must Block.

## Shared context policy

Every role receives the immutable Request, Protocol, current frozen `input.md`
and independent Facts paths. Roles directly verify the clean worktree. No role
may inspect forbidden prior candidates, Run state outside supplied facts, other
worktrees or earlier generation Homes; Validator may inspect only its current
managed Profile and supplied generation identity.

## Observability policy

Use Full Audit because all business roles are External and Origin is
control-plane only. Retain redacted raw data with standard redaction and a
64 MiB per-Turn limit. Every formal payload has concrete non-empty
`## Decision rationale` and `## Evidence` sections. Capture Harness-exposed
events and summaries, never hidden chain-of-thought.

## Block and resume policy

Every committed Block returns to the user. Origin may perform read-only
diagnosis or deterministic recovery but cannot auto-Resume. Only a later
explicit user instruction may Resume. Business findings remain in the
Developer/Reviewer/Validator loop. A pre-Outbox `ROUTE_PREFLIGHT_REJECTED` and
a pre-Block output-limit continuation need no Origin action because neither is
a Block. Immutable model, workspace, Profile, role, launch mode or limit changes
require Cancel plus a new Run.

## Assumptions made during bootstrap

- “继续” means install the confirmed Agent-Team repair and start a new clean
  Run, not reuse business output from the cancelled v3 Run.
- Worktree v4 is detached at the stated baseline, is clean, and contains only
  the intentionally empty Plugin directory at bootstrap.
- Temporary DSH capacity remains `256000/131072`; reasoning effort stays native.
- Repository-supported dependency bootstrap may create ignored local state but
  may not import prior Plugin content. No concurrent manual edits occur.

## Safety limits

Maximum 18 role Turns and 7200 seconds in one worktree. The user accepted this
Run's three roles using `full-access`, so DSH can access host files, environment
credentials, processes and network without per-command approval. That technical
access grants no unrelated, destructive, publishing, prior-artifact or external
authority. DSH is interactive-only. This confirmation is specific to this
immutable Run and is not repeated for Handoffs or safe automatic continuations.
