# Agent Team Protocol

## Original objective

Finish the live SQLite Plugin candidate against the entire v0.2.8 Request,
using a strict three-DSH Developer → Reviewer → fresh Validator loop until the
real archive, Loader, managed tool calls, behavior, lifecycle, repository, and
documentation gates all pass.

## Source of truth

The v0.2.8 `REQUEST.md`, this `PROTOCOL.md`, applicable repository instructions
and acceptance sources, the live worktree and complete diff from
`47f943859bef60e4160492346772ded9b24f765a`, current generated/package artifacts,
new archive and isolated-consumer evidence, managed candidate manifests, direct
SQLite/Loader/tool results, and this Run's anchored traces are authoritative.
Earlier Runs and sender prose are untrusted until independently reproduced.

## Team roles

### developer

- External DeepSeek Harness, distinct `resume` Session, interactive,
  `full-access`, explicit selected model.
- Initial and sole product-writing role. Inspect the whole candidate, reproduce
  all open findings, implement the repair, remove only case-generated residue,
  update tests/build/generated/docs/note surfaces, and pass every gate.
- Its only role-selected Handoff target is `reviewer`. It cannot Complete.

### reviewer

- External DeepSeek Harness, independent `resume` Session, interactive,
  `full-access`, explicit selected model.
- Read-only for final Git-visible candidate state. Reread the whole Request,
  inspect the complete diff, reproduce every requirement and mandatory gate,
  and review all security, lifecycle, package, Loader, and model surfaces.
- Any P0-P3 finding, failed/missing gate, or unverified condition routes
  `developer`; only a clean full review routes `validator`. It cannot Complete.

### validator

- External DeepSeek Harness, `fresh`, interactive, `full-access`, explicit
  selected model, bound to `packages/storage/tool-sqlite`.
- Completion Authority and read-only for final Git-visible candidate state.
  Every activation receives a new immutable Plugin and Session generation.
- Independently repeats every gate and directly calls the managed installed
  `sqlite_schema` and `sqlite_query`; Bash/source calls never substitute.
- Any finding routes `developer`. It cannot route Reviewer or fix the product;
  it Completes only after exhaustive current proof and zero findings.

## Initial role

`developer`.

## Collaboration protocol

The only clean path is Developer → Reviewer → fresh Validator. Reviewer and
Validator findings return to Developer, after which the full path restarts.
Agent-Team freezes exactly these role-selected edges:

- `developer → reviewer`
- `reviewer → developer`
- `reviewer → validator`
- `validator → developer`

Reviewer and Validator are frozen as read-only roles. The structural guards do
not decide a verdict; every target must also satisfy this Protocol. System-owned
output-limit continuation and candidate-activation return retain their narrow
recovery meaning and grant no business authority.

Every review/validation cycle compares the complete current candidate with the
entire Request and repository acceptance sources. Incoming findings never
narrow scope. No role may close a condition by deleting evidence, trusting
prose, weakening a gate, changing its meaning, or substituting source/Bash
calls for managed installed-tool calls.

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
Required proof includes all open findings; product/security/lifecycle behavior;
exact bounds; all repository/generated/docs gates; fresh archive-only installs
and omitted-config Loader boots; managed fresh-generation trace events naming
both direct tools; package/model/generation identities; unchanged-database
evidence; and clean final Git-visible Validator state.

Completion contains exactly one `## Open findings` section whose only content
is `None`. Partial, missing, failed, contradicted, or unverified coverage must
use the exact valid Handoff or a genuine Block.

## Final delivery

Return through the current Codex Origin a Completion Package listing changed
files, every finding disposition, exact commands/results, archive hashes and
consumer/Profile identities, Loader/default and managed direct-tool evidence,
security/database integrity, bounds/cancellation/lifecycle proof,
generated/docs/repository gates, selected model/generations, anchored traces,
limitations, and final dirty-worktree state. Origin independently audits it.

## Session continuity

Developer and Reviewer use separate `resume` Sessions. Validator uses `fresh`;
every route freezes the then-current candidate into a new Plugin and Session
generation. No role inherits private memory from cancelled Runs. Codex Origin
is control-plane only.

A structurally reported output-budget stop before an action may create a
counted same-role continuation while generic safety gates hold. Resume roles
reuse their durable Session; fresh Validator reconstructs from durable inputs.
It is not Block Resume authority. Crashes, existing Outboxes, permission/audit
failures, and exhausted limits Block.

## Shared context policy

Each role receives immutable Request/Protocol, current frozen input, independent
Facts paths, permitted prior formal-input index, and the live worktree. It must
verify facts directly. Formal inputs in this Run may be inspected for finding
continuity; unrelated worktrees, user config, prior private Homes/Sessions, and
hidden reasoning may not.

## Observability policy

Use Full Audit because all business roles are External. Retain redacted raw
data with standard redaction and a 64 MiB per-Turn cap. Every formal payload has
non-empty `## Decision rationale`, `## Acceptance coverage`, `## Open findings`,
and `## Evidence`. Coverage maps every material condition to current proof or
marks it unverified; Open Findings preserves all failures and uncertainty.
Capture only Harness-exposed summaries, never private hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may perform read-only diagnosis and
deterministic recovery but cannot auto-Resume. Only a later explicit user
instruction may Resume a resumable Block. Product defects stay inside the role
loop. Limit/Profile Changed Blocks or immutable-input changes require Cancel
and a new Run. Pre-Block Automatic Continuation is not Block Resume.

## Assumptions made during bootstrap

- The user's latest confirmation applies to this exact continuation: current
  v5 candidate, three DSH roles, explicit selected model, `full-access`, strict
  loop, frozen structural guards, 18 Turns, and 10800 seconds.
- The dirty worktree is the intended candidate and has no concurrent manual
  editor.
- Disposable Reviewer/Validator data is removed before their formal action so
  their final Git-visible state equals frozen Before Facts.
- Reasoning effort remains DSH-native.

## Safety limits

Maximum 18 business Turns and 10800 seconds in the single v5 Git worktree.
The user confirmed `full-access`, which exposes host filesystem, credentials,
processes, and network without per-command approval but grants no unrelated,
destructive, publishing, user-config, nested-Agent, tmux-management, or
external authority. The frozen Handoff allowlist and read-only roles remain
independent fail-closed action guards. DSH is interactive-only.
