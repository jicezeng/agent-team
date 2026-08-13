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

For External roles, state that the Agent-Team Skill is guidance only and has no
terminal arguments. Require exactly one `$AGENT_TEAM_CLI handoff`, `complete`,
or `block` command at the end of each Turn; repeated Skill invocations do not
route or complete work.

## Completion condition

Give an observable business condition and name the Completion Authority.

## Final delivery

Describe the Completion Package and delivery through the current Origin
session.

## Session continuity

Specify `resume` or `fresh` for every External role and explain why. Origin
roles use the embedded host session.

## Shared context policy

List the direct input and evidence each role receives. Kickoff, Handoff, and
Resume payloads are all frozen as the next Turn's `input.md`. Disclose that
multiple Origin roles share host context; use separate External sessions for
independent or blind review.

## Observability policy

Record `standard` or `full`, raw retention, redaction, and byte limits. Full
audit requires every business role to be External and reserves Origin for
control-plane work. Require non-empty `## Decision rationale` and
`## Evidence` in every formal role payload. State that only Harness-exposed
reasoning summaries can be captured, never private hidden chain-of-thought.

## Block and resume policy

Every Block returns to the user. Origin may run read-only diagnosis or
deterministic `recover`, but may not auto-Resume. Only a new explicit user
instruction may Resume a resumable Block. Limit/Profile Changed Blocks and
immutable-input changes require Cancel plus a new Run.

## Assumptions made during bootstrap

List every inference explicitly.

## Safety limits

Record positive max-turn and wall-time bounds, the single Git worktree, chosen
explicit Launch Profiles, no-concurrent-manual-edit premise, External hard
versus Origin cooperative deadline behavior, and manual cancellation. For
`trusted-workspace` or `full-access`, record the user's explicit selection and
the Adapter-specific trust boundary. `trusted-workspace` must retain the
Workspace write boundary; on a managed host this requires compatible
administrator policy because that policy is outside the frozen Profile Hash
and Doctor's proof. For Codex, also record that non-managed hooks are disabled
while trusted Workspace project configuration and extensions remain inside the
Workspace trust decision. `full-access` has no Harness host sandbox.
