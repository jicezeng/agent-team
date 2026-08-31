# Agent Team Protocol

## Original objective

Repair and finish the live SQLite Plugin candidate against the complete v0.2.9
Request, using a strict three-DSH Developer -> Reviewer -> fresh Validator loop
until the real BigInt path, archive, Loader, managed tool calls, lifecycle,
security, repository, and documentation gates all pass.

## Source of truth

The v0.2.9 `REQUEST.md`, this `PROTOCOL.md`, applicable repository instructions
and acceptance sources, the live worktree and complete diff from Git HEAD,
current generated/package artifacts, fresh real `node:sqlite` results, new
archive and isolated-consumer evidence, managed candidate manifests, direct
Loader/tool results, and this Run's anchored traces are authoritative. Cancelled
Runs and sender prose are untrusted until independently reproduced.

## Team roles

### developer

- External DeepSeek Harness, distinct `resume` Session, interactive,
  `full-access`, explicit selected model.
- Initial and sole product-writing role. Reproduce the real unsafe-integer
  failure, investigate the actual runtime API, repair the production Worker
  path, add real integration coverage, remove case residue, review the complete
  candidate, and close every Request gate in coherent batches.
- Its only role-selected Handoff target is `reviewer`. It cannot Complete.

### reviewer

- External DeepSeek Harness, independent `resume` Session, interactive,
  `full-access`, explicit selected model.
- Read-only for Git-visible candidate state. Reread the whole Request, inspect
  the complete diff, reproduce the real runtime and every mandatory gate, and
  review security, lifecycle, packaging, Loader, model, generated, and docs
  surfaces.
- Allocate every fixture, consumer, archive copy, script, cache, and report in
  an OS temporary directory outside the worktree. Never create, edit, rename,
  or delete a Git-visible workspace path.
- Any P0-P3 finding, failed/missing gate, or unverified condition routes
  `developer`; only a clean full review routes `validator`. It cannot Complete.

### validator

- External DeepSeek Harness, `fresh`, interactive, `full-access`, explicit
  selected model, bound to `packages/storage/tool-sqlite`.
- Completion Authority and read-only for Git-visible candidate state. Every
  activation receives a new immutable Plugin and Session generation.
- Allocate every database, consumer, Profile, script, cache, report, and
  diagnostic probe in a new OS temporary directory outside the worktree. Never
  create, edit, rename, or delete a Git-visible workspace path.
- Independently repeats every consumer-facing gate and directly calls the
  managed installed `sqlite_schema` and `sqlite_query`; Bash/source calls never
  substitute. Any finding routes `developer`. It cannot route Reviewer or fix
  the product; it Completes only after exhaustive current proof and zero
  findings.

## Initial role

`developer`.

## Collaboration protocol

The only clean path is Developer -> Reviewer -> fresh Validator. Reviewer and
Validator findings return to Developer, after which the full path restarts.
Agent-Team freezes exactly these role-selected edges:

- `developer -> reviewer`
- `reviewer -> developer`
- `reviewer -> validator`
- `validator -> developer`

Reviewer and Validator are frozen as read-only roles. Their disposable
validation data belongs outside the worktree; a Git-visible boundary change is
a Permission Block. These guards constrain actions but do not decide verdicts.

Every review/validation cycle compares the complete current candidate with the
entire Request and repository acceptance sources. Incoming findings never
narrow scope. No role may close a condition by deleting evidence, trusting
prose, weakening a gate, changing its meaning, or substituting mocks,
source/Bash calls, or a nested Harness for managed installed-tool calls.

At Turn end the active role writes one audited payload inside its Turn directory
and invokes exactly one successful `$AGENT_TEAM_CLI handoff`, `complete`, or
`block`, then stops business work. `HANDOFF_NOT_ALLOWED` and
`ROUTE_PREFLIGHT_REJECTED` stage no action and leave the Turn active. A
structurally marked Candidate Activation Finding is inspected by its receiver;
candidate defects route Developer, while only proven infrastructure failure
Blocks.

## Completion condition

Only Validator may Complete, after a current clean Reviewer sign-off since the
last product edit and independent coverage of every Request/Protocol condition.
Required proof includes the real unsafe-integer production path; complete
product/security/lifecycle behavior; repository/generated/docs gates; fresh
archive-only install and omitted-config Loader boot; managed fresh-generation
trace events naming both direct tools; package/model/generation identities;
unchanged-database evidence; no case residue; and unchanged final Git-visible
Validator state.

Completion contains exactly one `## Open findings` section whose only content
is `None`. Partial, missing, failed, contradicted, or unverified coverage must
use the exact valid Handoff or a genuine Block.

## Final delivery

Return through the current Codex Origin a Completion Package listing changed
files, every finding disposition, exact commands/results, archive hash and
consumer/Profile identity, Loader/default and managed direct-tool evidence,
real BigInt/security/database-integrity/bounds/cancellation/lifecycle proof,
generated/docs/repository gates, selected model/generation, anchored traces,
limitations, and final dirty-worktree state. Origin independently audits it.

## Session continuity

Developer and Reviewer use separate `resume` Sessions within this new Run.
Validator uses `fresh`; every route freezes the then-current candidate into a
new Plugin and Session generation. No role inherits private memory from the
cancelled Run. Codex Origin is control-plane only.

A structurally reported output-budget stop before an action may create a
counted same-role continuation while safety gates hold. Resume roles reuse their
durable Session; fresh Validator reconstructs from durable inputs. It is not
Block Resume authority. Crashes, existing Outboxes, permission/audit failures,
and exhausted limits Block.

## Shared context policy

Each role receives immutable Request/Protocol, current frozen input,
independent Facts paths, permitted prior formal-input index, and the live
worktree. It verifies facts directly. Formal inputs in this Run may be inspected
for finding continuity; unrelated worktrees, prior private Homes/Sessions, and
hidden reasoning may not.

## Observability policy

Use Full Audit because all business roles are External. Retain redacted raw
data with standard redaction and a 64 MiB per-Turn cap. Every formal payload has
non-empty `## Decision rationale`, `## Acceptance coverage`,
`## Open findings`, and `## Evidence`. Coverage maps every material condition
to current proof or marks it unverified. Capture only Harness-exposed summaries,
never private hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may perform read-only diagnosis and
deterministic recovery but cannot auto-Resume. Only a later explicit user
instruction may Resume a resumable Block. Product defects stay inside the role
loop. Limit/Profile Changed Blocks or immutable-input changes require Cancel
and a new Run. Pre-Block Automatic Continuation is not Block Resume.

## Assumptions made during bootstrap

- The user's latest explicit confirmation applies to this exact new Run: the
  current v5 candidate, three DSH roles, explicit selected model,
  `full-access`, strict structural loop, read-only guards, 9 Turns, and 7200
  seconds.
- The dirty worktree is the intended candidate and has no concurrent manual
  editor.
- Reviewer and Validator can place all disposable validation data in an OS
  temporary directory outside the worktree and remove it before formal action.
- Reasoning effort remains DSH-native.

## Safety limits

Maximum 9 business Turns and 7200 seconds in the single v5 Git worktree. The
user confirmed `full-access`, which exposes host filesystem, credentials,
processes, and network without per-command approval but grants no unrelated,
destructive, publishing, user-config, nested-Agent, tmux-management, or
external authority. The frozen Handoff allowlist and read-only roles remain
independent fail-closed guards. DSH is interactive-only.
