# Agent Team Protocol

## Original objective

Finish and independently validate the six observability acceptance areas in
`docs/validation/observability-claude-codex-request.md`.

## Source of truth

The immutable Request, this Protocol, repository instructions, the live
worktree and complete diff, source code, tests, build output, and anchored
Agent-Team trace artifacts are authoritative. Sender claims are not facts and
must be independently checked.

## Team roles

### developer

- Binding: external
- Harness: Claude Code
- Session policy: resume
- Inspect the complete current implementation and diff.
- Fix correctness, compatibility, security, usability, test, or documentation
  defects found during implementation and review.
- Run the required verification commands.
- Do not commit and do not stage `.agent-team/`.
- Hand off to `reviewer` with a complete evidence-backed account.

### reviewer

- Binding: external
- Harness: Codex
- Session policy: resume
- Independently review the complete diff and live implementation against all
  six acceptance areas.
- Review only; do not modify business files.
- Classify actionable findings P0 through P3 and provide exact evidence.
- If any P0-P3 finding remains, hand off all findings to `developer`.
- After every Developer response, re-review the complete implementation, not
  only the latest patch.
- Reviewer is the sole Completion Authority.

## Initial role

developer

## Collaboration protocol

1. Developer audits and finishes the implementation, runs verification, and
   hands off to Reviewer.
2. Reviewer independently inspects all six acceptance areas, integrity and
   recovery invariants, CLI behavior, tests, docs, and packaging.
3. Every P0-P3 finding is handed to Developer with reproducible evidence.
4. Developer explicitly accepts and fixes each finding, or rejects it with
   concrete counter-evidence, then reruns relevant verification and hands back.
5. The same resumable Reviewer session performs a complete re-review.
6. Repeat until Reviewer finds no open P0-P3 issue.
7. P4 suggestions may be recorded as residual risk but do not block.

Every formal payload must include all normal handoff material plus non-empty
`## Decision rationale` and `## Evidence` sections. These sections contain
explicit audit rationale and reproducible evidence, never claimed hidden
chain-of-thought.

For External Turns, the Agent-Team Skill is guidance only and has no terminal
arguments. Do not invoke it with `--complete`, `--summary`, or similar
arguments. Route or finish only through exactly one `$AGENT_TEAM_CLI handoff`,
`complete`, or `block` command shown in the frozen Turn prompt.

## Completion condition

The Reviewer may complete only after independently confirming:

- all six acceptance areas are implemented coherently;
- no open P0-P3 finding remains;
- compileall, the full test suite, diff check, and package build pass;
- both Claude Code and Codex participated through External Turns;
- Full Audit trace manifests and normalized events for the completed Run can
  be inspected by the Origin control plane.

## Final delivery

The Reviewer Completion must summarize six-area coverage, all loop findings
and resolutions, exact verification commands/results, residual P4 risks, and
the key trace evidence the Origin should inspect.

## Session continuity

Both External roles use `resume`, so findings and responses remain in the same
role-specific Harness session across Turns.

## Shared context policy

Each role receives its frozen direct input, the original Request and Protocol,
the live worktree, independent workspace facts, formal prior payloads, and its
own Harness session. Roles must verify the current tree directly.

## Observability policy

Use Full Audit mode. Every business role is External and Origin performs only
Bootstrap, waiting, final trace audit, and user delivery. Use standard
redaction, a 64 MiB per-Turn cap, redacted raw retention, and the audited
formal-payload contract. Only Harness-exposed reasoning summaries may appear
in the trace.

## Block and resume policy

Every Block returns to the user. No role may auto-resume a Block. Limit,
profile, immutable configuration, role, workspace, or protocol changes require
a new Run.

## Assumptions made during bootstrap

- The installed candidate wheel is the implementation under validation.
- Claude authentication is usable based on the immediately preceding
  successful mixed-Harness validation history; `doctor` can report it only as
  `unknown` without a model call.
- Full serial Developer/Reviewer iteration is sufficient; no parallel
  fan-out/join is requested.
- No manual edits occur while the Run is active.

## Safety limits

- One Git worktree:
  `<agent-team-repo>`
- Maximum 12 business Turns.
- Maximum wall time 7200 seconds.
- Explicit `default` Launch Profile for both Harnesses.
- No concurrent manual edits, no escaping daemon, no `.agent-team/` staging,
  and no Git commit.
- Repository pytest runs use the dedicated ignored `.pytest-tmp/` base
  directory so Harness sandbox restrictions on the host `TMPDIR` cannot
  create Git-visible `pytest-of-*` workspaces or nested test repositories.
