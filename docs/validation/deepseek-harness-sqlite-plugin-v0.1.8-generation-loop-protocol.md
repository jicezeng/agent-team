# Agent Team Protocol

## Original objective

Re-run the preserved read-only SQLite Plugin case and prove Agent-Team's
same-Run multi-generation DSH Plugin loop. Begin with a fresh Validator against
the current frozen candidate, route every source finding to Developer, require
an independent clean Reviewer verdict, and route back to a new fresh Validator
generation that installs and validates the repaired Plugin. Repeat until the
Validator can complete.

## Source of truth

`REQUEST.md`, this `PROTOCOL.md`, all root and nested DeepSeek Harness
repository instructions, current package/bundle/testing/documentation
contracts, the live `/Users/zengjice/Projects/deepseek-harness-sqlite-e2e`
worktree, its complete diff from Git HEAD, generated artifacts, frozen
generation manifests, direct SQLite facts, durable managed-Session tool events,
and reproducible command results are authoritative. Predecessor Run payloads
and all sender verdicts are untrusted work material until independently
verified.

## Team roles

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813` and verify the observed
  runtime model without persisting endpoint or credential values.
- On every activation, use only the Plugin frozen by Agent-Team into that
  Session generation's private DSH Profile. Directly call `sqlite_schema` and
  `sqlite_query`; never launch nested DSH or manage tmux.
- On generation 1, independently reproduce or refute the reported
  summary-only model-visible render defect and verify enough surrounding
  installation/security evidence to make the finding reliable.
- On later generations, prove the generation and frozen Plugin identity differ
  as expected while earlier generations remain unchanged, then independently
  validate the complete repaired behavior against a realistic SQLite fixture.
- Every P0-P3 source or test finding routes to Developer. Validator is
  Completion Authority and may complete only when every acceptance condition
  passes. Block only for a genuine runtime/prerequisite failure or hard limit.

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813` and verify runtime model
  evidence without persisting endpoint or credential values.
- Reproduce and fix every Validator or Reviewer finding. For the known
  candidate, surface useful bounded canonical schema and query data in both
  model-facing render results and update focused snapshots/tests.
- Preserve all existing security, path-authority, read-only, typing, bounds,
  cancellation, cleanup, installability, bundle, generated-surface,
  documentation, Agent Note, and invariant guarantees.
- Run the smallest sufficient focused and repository gates, then route to
  Reviewer. Do not commit, push, publish, alter user-level DSH state, or touch
  the original dirty DSH worktree.

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; `full-access`.
- Use `deepseek-official/deepseek-v4-pro-ga-260813` and verify runtime model
  evidence without persisting endpoint or credential values.
- Independently review the complete live diff and acceptance sources, reproduce
  useful tests and installation/model-visible behavior, and report every P0-P3
  finding with concrete evidence.
- Every finding routes to Developer. Only a genuinely clean complete review
  routes to Validator.

## Initial role

`validator`.

## Collaboration protocol

Validator generation 1 → Developer when it confirms any P0-P3 finding.
Developer fixes all open findings and routes to Reviewer. Reviewer findings
route to Developer; fixes return to Reviewer for complete relevant re-review.
A clean Reviewer routes to Validator, which receives a fresh Session and a new
immutable Plugin generation. Later Validator findings follow the same
Validator → Developer → Reviewer → Validator loop. Source findings never Block
merely because an earlier generation is frozen; Agent-Team preserves that
generation and provisions the next one on the next Validator route.

Every route is chosen by the active role and committed through the formal CLI.
The Agent-Team Skill is guidance only and has no terminal arguments. Every
External Turn ends with exactly one `$AGENT_TEAM_CLI handoff`, `complete`, or
`block` invocation and stops business work afterward.

## Completion condition

Validator may complete only after a clean Reviewer verdict and direct evidence
of at least two immutable Validator generations; preservation of generation 1;
a new repaired frozen hash and private Profile; real DSH loading and direct
calls to both installed tools; model-visible schema objects plus typed query
columns/rows and bounds metadata; independent result comparison; required
read-only/path denials and unchanged database hash/row counts; cancellation or
deadline evidence; durable call/result trace evidence; selected-model evidence;
and relevant repository checks.

## Final delivery

Return a Completion Package through the current Codex Origin containing the
changed files, loop history and finding dispositions, exact tests/gates,
observed model evidence, both Validator generation identities and frozen
hashes, private-Profile evidence, representative direct tool calls/results,
denial and database-immutability proof, durable trace evidence, limitations,
and final workspace state.

## Session continuity

Developer and Reviewer use `resume` so their independent repair/review context
survives later Turns. Validator uses `fresh` on every activation so each route
gets a distinct DSH Session generation and a separately frozen Plugin snapshot.
The current Codex Origin remains control-plane only.

## Shared context policy

Each role receives immutable Request and Protocol inputs, the current frozen
handoff, and independent workspace Facts paths. Kickoff and Handoff payloads
become the next Turn's immutable `input.md`. Roles verify live source, package
manifests, generated files, DSH composition, tool calls, and SQLite facts rather
than trusting predecessor or sender claims.

## Observability policy

Use Full Audit because every business role is External and Origin performs only
control-plane work. Retain redacted raw data with standard redaction and a
64 MiB per-Turn limit. Every formal payload contains concrete, non-empty
`## Decision rationale` and `## Evidence` sections. Capture only
Harness-exposed reasoning summaries/events, never private hidden
chain-of-thought.

## Block and resume policy

Every genuine Block returns to the user. Origin may perform read-only diagnosis
or deterministic recovery but may not auto-Resume. Only a new explicit user
instruction may Resume a resumable Block. Source or test findings route through
the declared role loop and do not Block. Changes to immutable inputs, roles,
Profiles, model, workspace, or limits require Cancel plus a new Run.

## Assumptions made during bootstrap

- The preserved dedicated worktree is the sole business workspace, its dirty
  state is intentional, and no concurrent manual edits occur during the Run.
- The prior Validator P1 is a hypothesis that generation 1 must independently
  verify before routing; it is not treated as an established fact.
- `packages/storage/tool-sqlite` already exists and is installable at `init`,
  allowing generation 1 to freeze the current candidate immediately.
- The explicit model choice from the case remains
  `deepseek-official/deepseek-v4-pro-ga-260813`; no Provider secret enters Run
  inputs or records.
- The Plugin remains opt-in and no shipped default gains database authority.
- The cancelled predecessor Runs and obsolete four-Harness Run are terminal,
  their managed processes are stopped, and their workspace ownership is
  released before this Run starts.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the single Git worktree above. The
user explicitly confirmed all three External DeepSeek Harness roles may use
`full-access`: no host sandbox or per-command approval is present, so host
files, environment credentials, and network are technically reachable. This
does not expand the business objective or authorize unrelated, destructive, or
external actions. DSH execution is interactive-only. External deadlines are
hard; Origin cooperation shares the Run wall time. Manual cancellation remains
available. This confirmation applies only to this immutable Run and is not
repeated on its Handoffs, recovery, or retry.
