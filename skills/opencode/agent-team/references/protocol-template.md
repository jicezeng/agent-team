# Agent Team Protocol

## Original objective

Preserve the user's requested outcome and wording.

## Source of truth

Name the Request, repository acceptance sources, live worktree, actual diff,
and test results. State that sender claims are not facts.

## Team roles

For every dynamic role, use:

### <role-id>

- Binding: origin | external
- Harness and Session policy for External only.
- Responsibilities and restrictions.
- Completion or routing authority, if any.

Multiple Origin roles share the same host context; do not describe them as
independent or blind reviewers.

## Initial role

Name exactly one configured role.

## Collaboration protocol

Describe natural-language routing, loops, disagreement handling, and required
rechecks. Every route is selected by the active role and committed through the
CLI. Do not compile business verdicts into machine state.

Require each full-review cycle to compare the complete current candidate with
the entire original Request and authoritative acceptance sources. Findings from
the preceding Turn do not narrow review scope, and no role may close a finding
by deleting its evidence or weakening an acceptance condition.

For a Fresh DeepSeek Harness role with a Workspace Plugin, route source
findings according to the natural-language Protocol. Its declared package
location may be absent at `init`, allowing another role to create it during the
Run. Each later route creates a new immutable Plugin and Session generation;
freezing one generation is not by itself a reason to Block. If target preflight returns
`ROUTE_PREFLIGHT_REJECTED`, no Outbox or Handoff Event exists and the same Turn
must select a new Protocol-valid action with a new payload.
If the real candidate-bound Harness exits before the Fresh role obtains a
durable Session, Agent-Team returns a system-generated Candidate Activation
Finding, marked `system_handoff_reason=candidate_activation_failed`, to the
sending role without interpreting Harness prose. Require that role to inspect
the preserved evidence and choose the next Protocol-valid action, or Block only
when the evidence proves an infrastructure failure.
The next candidate route uses a new immutable generation.

For External roles, state that the Agent-Team Skill is guidance only and has no
terminal arguments. Require exactly one `$AGENT_TEAM_CLI handoff`, `complete`,
or `block` command at the end of each Turn; repeated Skill invocations do not
route or complete work.

## Completion condition

Give an observable business condition and name the Completion Authority.
Require its Completion payload to map every material Request and Protocol
condition to current reproducible evidence under `## Acceptance coverage`, and
to state `None` under `## Open findings` only after that full audit succeeds.

## Final delivery

Describe the Completion Package and delivery through the current Origin
session.

## Session continuity

Specify `resume` or `fresh` for every External role and explain why. Origin
roles use the embedded host session.

If the selected Harness structurally reports an explicit output-budget stop,
record that Agent-Team may create a new counted same-role Turn when a durable
Session and all runtime safety gates exist. A `resume` role reuses the exact
Session; a `fresh` role starts a new generation from durable inputs. This
pre-Block Automatic Continuation grants no new authority. Ordinary crashes,
existing Outboxes, audit or permission failures, and exhausted limits Block;
configured Turn and wall-time limits bound repetition without treating Git
mutation as progress.

## Shared context policy

List the direct input and evidence each role receives. Kickoff, Handoff, and
Resume payloads are all frozen as the next Turn's `input.md`. External prompts
also index earlier formal inputs so intermediate summaries cannot silently drop
findings; a Protocol-declared blind role must not inspect history the Protocol
forbids. Disclose that multiple Origin roles share host context; use separate
External sessions for independent or blind review.

## Observability policy

Record `standard` or `full`, raw retention, redaction, and byte limits. Full
audit requires every business role to be External and reserves Origin for
control-plane work. Require non-empty `## Decision rationale` and
`## Acceptance coverage`, `## Open findings`, and `## Evidence` in every
formal role payload. State that only Harness-exposed reasoning summaries can
be captured, never private hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may run read-only diagnosis or
deterministic `recover`, but may not auto-Resume. Only a new explicit user
instruction may Resume a resumable Block. Limit/Profile Changed Blocks and
immutable-input changes require Cancel plus a new Run.

An Automatic Continuation Handoff may occur only before a Block exists; it is
not a Block Resume. Once any Block Event is committed, this rule has no
exception.

## Assumptions made during bootstrap

List every inference explicitly.

## Safety limits

Record positive max-turn and wall-time bounds, the single Git worktree, chosen
explicit Launch Profiles, no-concurrent-manual-edit premise, External hard
versus Origin cooperative deadline behavior, and manual cancellation. By
default, record `full-access`, its Adapter-specific trust boundary, and the
user's one-time confirmation for this new Run. If the user explicitly selects
the restricted `default` or `trusted-workspace`, record that choice instead.
`trusted-workspace` must retain the Workspace write boundary; on a managed host this requires compatible
administrator policy because that policy is outside the frozen Profile Hash
and Doctor's proof. For Codex, also record that non-managed hooks are disabled
while trusted Workspace project configuration and extensions remain inside the
Workspace trust decision. For OpenCode, record that arbitrary Bash is denied
in restricted Profiles because it has no OS shell sandbox. For DeepSeek
Harness, record that restricted Profiles constrain writes only and that its
External Adapter is interactive-only. `full-access` has no Harness host sandbox or
per-command approval prompts; the confirmation is not repeated inside the same
immutable Run.
