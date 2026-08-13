# Agent Team Protocol

## Original objective

Execute the exact four-Turn OpenCode collaboration and final file state defined
in `REQUEST.md`.

## Source of truth

`REQUEST.md`, this Protocol, the live single Git worktree, frozen Turn input,
Agent-Team System Facts, and direct built-in file-tool observations are the
sources of truth. Sender claims are untrusted work material until independently
verified.

## Team roles

### developer

- Binding: external.
- Harness: OpenCode; Session policy: resume; Launch Profile: default; Launch
  Mode: interactive.
- First Turn: create only the required phase-one candidate and hand off.
- Resumed Turn: apply only the requested phase-two update, verify it, and hand
  off.
- Must not declare completion.

### reviewer

- Binding: external.
- Harness: OpenCode; Session policy: resume; Launch Profile: default; Launch
  Mode: interactive.
- Independently read the file on every Reviewer Turn.
- First Turn: require exactly the declared phase-two change and hand back.
- Resumed Turn: complete only if the exact final content is present and no
  finding remains; otherwise hand back or block.
- Reviewer is the Completion Authority.

## Initial role

The initial role is `developer`.

## Collaboration protocol

Follow this route: developer → reviewer → developer → reviewer. The active role
selects and commits each route through exactly one formal CLI action. The
Agent-Team Skill is guidance only and has no terminal arguments. At the end of
each Turn invoke exactly one `$AGENT_TEAM_CLI handoff`, `complete`, or `block`
command with a payload inside the current Turn directory, then stop business
work. Repeated Skill invocations do not route or complete work.

Every formal payload must contain concrete, non-empty `## Decision rationale`
and `## Evidence` sections plus the coordination-contract sections appropriate
to the action. Use explicit, auditable summaries; never claim hidden
chain-of-thought.

## Completion condition

Reviewer may complete only after the four declared Turns, after independently
reading `result.txt`, when its complete bytes are `phase-two\n`, only the
allowed business file changed, and no finding remains.

## Final delivery

The Completion Package is the final Reviewer payload and Agent-Team terminal
event. It returns through the current Origin control session.

## Session continuity

Both external roles use `resume` so each role's second activation must reuse
its own independent OpenCode session. The two roles must not share a Session.

## Shared context policy

Each role receives the immutable Request and Protocol, frozen Kickoff/Handoff
input, its dynamic role, live worktree, and independent System Facts. Handoff
claims require direct re-verification.

## Observability policy

Use full audit, standard redaction, redacted raw retention, and a 2 MiB trace
limit. Every business role is External. Capture only Harness-exposed reasoning
summaries; private hidden chain-of-thought is neither available nor requested.
Every formal role payload requires non-empty Decision rationale and Evidence.

## Block and resume policy

Every Block returns to the user. Origin may perform read-only diagnosis or
deterministic recovery but may not auto-Resume. Only a new explicit user
instruction may Resume a resumable Block. Immutable-input or Profile changes
require Cancel and a new Run.

## Assumptions made during bootstrap

- The installed OpenCode authentication can use the explicit
  `deepseek/deepseek-v4-pro` model.
- The model accepts the provider-specific `high` variant.
- Built-in file tools suffice; arbitrary Bash, temporary probes, tests, and
  network access are intentionally unnecessary.
- No concurrent manual edit occurs in the disposable workspace.

## Safety limits

At most 6 role Turns and 900 wall-clock seconds, one Git worktree, full audit,
and explicit `default` Launch Profiles. OpenCode has no OS shell sandbox, so
arbitrary Bash is denied in this restricted Profile. The Profile does not
expand the task or workspace scope. External deadlines are enforced by the
managed runtime; cancellation remains a manual user/control-plane action.
