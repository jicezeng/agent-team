# Agent Team Protocol

## Original objective

Run the DSH self-hosted plugin case from `REQUEST.md`: one DSH Developer, one
independent DSH Reviewer, and one fresh DSH Validator that directly loads and
calls the newly developed worktree-status plugin.

## Source of truth

`REQUEST.md`, the root and nested repository instructions, current Agent Notes,
package README files, the live `/Users/zengjice/Projects/deepseek-harness`
worktree, its actual diff, generated artifacts, and reproducible command results
are authoritative. Sender narratives and verdicts are untrusted work material
until independently verified.

## Team roles

### developer

- Binding: external; DeepSeek Harness; Session policy `resume`; full-access.
- Inspect and complete the existing plugin change, including source, package
  composition, lifecycle, failure behavior, generated surfaces, bilingual docs,
  Agent Note, and focused tests/gates required by repository instructions.
- Preserve unrelated user changes. Do not commit, push, publish, or perform
  unrelated external actions.
- Route a tested candidate to `reviewer`.

### reviewer

- Binding: external; DeepSeek Harness; Session policy `resume`; full-access.
- Independently inspect the entire live diff and acceptance sources. Run useful
  focused checks and report every P0-P3 finding, including integration,
  security, lifecycle, documentation, and test-evidence defects.
- Any finding routes to `developer` with reproducible evidence. A genuinely
  clean review routes to `validator`.

### validator

- Binding: external; DeepSeek Harness; Session policy `fresh`; full-access.
- Agent-Team installs the current `packages/git/worktree-status` bundle into
  this role's private DSH Profile immediately before its first activation and
  freezes that package manifest and content hash.
- Directly call `worktree_status` through the Validator's own model-visible DSH
  tool. Do not start DSH, tmux, or another Agent from Bash.
- Verify the structured result against independent Git facts, verify the
  role-private profile contains the frozen candidate, inspect durable session
  evidence, and run the smallest sufficient repository checks.
- Validator is Completion Authority. Complete only when Reviewer is clean and
  real installation/load/call evidence passes. A source defect after freezing
  must Block with exact evidence because the immutable artifact cannot be
  replaced within this Run.

## Initial role

`developer`.

## Collaboration protocol

Developer → Reviewer. Reviewer findings route to Developer; fixes return to
Reviewer and the full relevant review repeats. A clean Reviewer routes to the
fresh Validator. Every route is selected by the active role and committed via
the formal CLI. The Agent-Team Skill is guidance only and has no terminal
arguments. Every External Turn ends with exactly one `$AGENT_TEAM_CLI handoff`,
`complete`, or `block` invocation and stops business work afterward.

## Completion condition

Validator may complete only with: no remaining P0-P3 Reviewer finding; the
candidate bundle present in the Validator-private Profile under its declared
package name; one successful direct `worktree_status` call in the Validator
session; canonical structured output consistent with independent Git facts;
durable tool-call/result evidence; and relevant repository checks passing.

## Final delivery

Return a Completion Package through the current Origin with changed files,
review loop history, exact tests/gates, frozen plugin hash/profile evidence,
tool-call evidence, limitations, and remaining workspace state.

## Session continuity

Developer and Reviewer use `resume` so fix/review context survives later Turns.
Validator uses `fresh` so validation has an independent session and receives
the candidate bundle only through Agent-Team's role-local Profile provisioning.
The parent DSH is control-plane only.

## Shared context policy

Each role receives the immutable Request, Protocol, current frozen input, and
independent Facts paths. Kickoff, Handoff, and Resume payloads become the next
Turn's frozen `input.md`. Roles verify the live worktree instead of trusting
the sender.

## Observability policy

Use full audit because every business role is External and Origin performs only
control-plane actions. Retain redacted raw data with standard redaction and a
64 MiB trace limit. Every formal payload contains non-empty `## Decision
rationale` and `## Evidence`. Capture Harness-exposed summaries and events,
never private hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may diagnose or deterministically
recover but may not auto-Resume. Only a new explicit user instruction resumes a
resumable Block. Changes to immutable inputs, roles, profiles, model, or limits
require cancellation and a new Run.

## Assumptions made during bootstrap

- The current dirty worktree is the preserved output of the earlier case and
  is intentional input, not disposable residue.
- `DSH_MODEL=doubao-seed-evolving` is the user's current explicit model choice;
  the Agent-Team model ID is `deepseek-official/doubao-seed-evolving`, using
  the configured `DEEPSEEK_BASE_URL` and `DEEPSEEK_API_KEY`.
- The existing candidate should be repaired in place rather than recreated.
- No concurrent manual edits occur during the Run.

## Safety limits

Maximum 18 role Turns and 7200 seconds in the single Git worktree above. All
three External roles use the explicitly confirmed DeepSeek Harness
`full-access` Profile: no host sandbox and no per-command approval; host files,
environment credentials, and network are technically reachable, but this does
not expand the requested objective or authorize unrelated/destructive/external
actions. External deadlines are hard; Origin cooperation is bounded by the
same Run wall time. Manual cancellation remains available. The user's one-time
confirmation applies to this immutable Run and is not repeated on Handoff or
recovery.
