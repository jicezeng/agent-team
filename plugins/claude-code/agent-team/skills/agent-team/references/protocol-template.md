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
versus Origin cooperative deadline behavior, and manual cancellation.
