# Agent Team Protocol

## Original objective

Run a genuine from-zero three-DSH self-development case in the clean detached
worktree. Developer creates a complete SQLite Plugin from an empty directory,
Reviewer independently drives every P0-P3 finding to closure, and fresh
Validator generations perform Agent-Team-managed installation and real tool
validation until completion.

## Source of truth

`REQUEST.md`, this `PROTOCOL.md`, root and nested repository instructions in
the clean target worktree, current repository architecture/testing/package
contracts, the live target worktree diff from
`47f943859bef60e4160492346772ded9b24f765a`, generated artifacts, frozen
generation manifests, direct SQLite facts, managed DSH tool events, and
reproducible command results are authoritative. Sender narratives and verdicts
are untrusted work material until independently verified. Prior SQLite Plugin
worktrees, Runs, reports, installed artifacts, and generation Homes are outside
the allowed evidence set and must not be inspected.

## Team roles

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/glm-5-2-260617`; verify runtime evidence
  without persisting endpoint or credential values.
- Verify that `packages/storage/tool-sqlite` is empty at kickoff, then design
  and implement the entire opt-in `@deepseek-ai/dsh-tool-sqlite` package from
  clean-repository patterns only.
- Own product code, package/bundle wiring, engine and filesystem security,
  typed/bounded model output, cancellation/lifecycle handling, tests, real
  Loader composition, built artifacts, documentation, Agent Note, invariant,
  and required generated surfaces/gates.
- Fix every Reviewer or Validator finding, run sufficient checks, and route a
  coherent candidate to Reviewer. Never inspect or copy a prior candidate.

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/glm-5-2-260617`; verify runtime evidence
  without persisting endpoint or credential values.
- Independently inspect the complete greenfield diff and acceptance sources;
  rerun focused tests and installation/composition evidence; review every
  product, security, model-experience, lifecycle, packaging, generated-surface,
  test, and documentation boundary named in the Request.
- Route every P0-P3 finding to Developer with reproducible evidence. Route to
  Validator only after a genuinely clean complete review.

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; `full-access`.
- Use `deepseek-official/glm-5-2-260617`; verify runtime evidence
  without persisting endpoint or credential values.
- Use only the Plugin snapshot Agent-Team freezes into the current
  generation-private Profile. Directly call both installed tools; never launch
  nested DSH or manage tmux.
- Independently create the required SQLite fixture, compare schema/query
  results to independent facts, attack denial paths, prove database
  immutability, verify cancellation/lifecycle evidence, package identity,
  private Profile composition, durable call/results, and relevant checks.
- Validator is Completion Authority. Every P0-P3 source/test finding routes to
  Developer; complete only when every acceptance condition passes. Block only
  for a genuine runtime/prerequisite failure or hard limit.

## Initial role

`developer`.

## Collaboration protocol

Developer → Reviewer. Reviewer findings → Developer; Developer fixes →
Reviewer for complete relevant re-review. Clean Reviewer → fresh Validator.
Validator findings → Developer, then Developer → Reviewer → a later fresh
Validator generation. Each Validator route freezes the current package into a
new immutable Profile and preserves prior generations. Source findings do not
Block merely because an earlier generation is frozen.

Every route is selected by the active role and committed through the formal
CLI. The Agent-Team Skill is guidance only and has no terminal arguments.
Every External Turn ends with exactly one `$AGENT_TEAM_CLI handoff`,
`complete`, or `block` invocation and stops business work afterward.

## Completion condition

Validator may complete only after independently verifying a clean Reviewer
verdict; a from-zero complete package diff; successful frozen installation and
real DSH loading; direct calls to both tools; accurate model-visible schema and
typed/bounded query results; required denial/path attacks and unchanged
database hash/row counts; cancellation/deadline and lifecycle evidence; valid
package/bundle/generated/docs surfaces; selected-model evidence; durable trace
evidence; and relevant repository tests/gates with no remaining P0-P3 finding.

## Final delivery

Return a Completion Package through the current Codex Origin with initial-empty
evidence, created/modified files, role-loop history and finding dispositions,
exact tests/gates, observed model evidence, frozen generation identities,
private-Profile and direct-tool evidence, denial/immutability proof, durable
trace evidence, limitations, and final workspace state.

## Session continuity

Developer and Reviewer use separate `resume` Sessions so their repair/review
context survives later Turns. Validator uses `fresh` so every activation is
independent and receives only an Agent-Team-frozen Plugin generation. The
current Codex Origin is control-plane only.

## Shared context policy

Every role receives the immutable Request, Protocol, current frozen input, and
independent Facts paths. Kickoff and Handoff payloads become the next Turn's
immutable `input.md`. Roles verify the clean target worktree directly. No role
may inspect prior candidates, prior Run state, other DSH worktrees, or prior
generation Homes except that Validator may inspect the current managed Profile
and Agent-Team-provided generation identity evidence.

## Observability policy

Use Full Audit because every business role is External and Origin is
control-plane only. Retain redacted raw data with standard redaction and a
64 MiB per-Turn limit. Every formal payload contains concrete, non-empty
`## Decision rationale` and `## Evidence` sections. Capture only
Harness-exposed summaries/events, never hidden chain-of-thought.

## Block and resume policy

Every genuine Block returns to the user. Origin may perform read-only diagnosis
or deterministic recovery but may not auto-Resume. Only a new explicit user
instruction may Resume a resumable Block. Source/test findings route through
the declared repair/review loop and are not Blocks. Immutable-input, role,
Profile, model, workspace, or limit changes require Cancel plus a new Run.

## Assumptions made during bootstrap

- The user's phrase “这个从无到有的run” means the accepted SQLite Plugin case
  must be re-executed from an empty package directory, not continued from any
  prior implementation.
- The dedicated worktree was created from baseline
  `47f943859bef60e4160492346772ded9b24f765a`; Git status is clean and the
  package directory is empty at bootstrap.
- The model and limits are those explicitly selected for this new Run:
  `deepseek-official/glm-5-2-260617`, 18 Turns, and 7200 seconds.
- The empty package directory exists only to satisfy immutable Validator bundle
  declaration at `init`; no source or metadata has been pre-seeded.
- Repository-supported dependency bootstrap may create ignored local state but
  must not import prior Plugin content.
- No concurrent manual edits occur in the target worktree, and the Plugin
  remains opt-in.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the single target Git worktree. The
user explicitly confirmed all three External DSH roles may use `full-access`:
there is no host sandbox or per-command approval, so host files, environment
credentials, and network are technically reachable. The protocol nevertheless
forbids accessing prior candidates and unrelated host state. DSH execution is
interactive-only. External deadlines are hard; Origin cooperation shares the
Run wall time. Manual cancellation remains available. This confirmation
applies only to this immutable Run and is not repeated on its Handoffs,
recovery, or retry.
