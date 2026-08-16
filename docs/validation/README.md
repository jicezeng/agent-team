# Validation evidence

This directory preserves real-run evidence for the runtime behaviors claimed by
Agent-Team v0.1. Reports are historical records: their package versions, test
counts, commands, Harness modes, and limitations describe the candidate that was
actually exercised rather than the latest checkout.

## Runtime lifecycle baseline

[`runtime-lifecycle-v0.1-validation-report.md`](runtime-lifecycle-v0.1-validation-report.md)
records the 2026-07-29 lifecycle validation. Its retained inputs are:

| Scenario | Request | Protocol |
| --- | --- | --- |
| Two-Codex baseline | [`benchmark-generator-request.md`](benchmark-generator-request.md) | [`benchmark-generator-protocol.md`](benchmark-generator-protocol.md) |
| Two-Codex feedback loop | [`benchmark-generator-loop-request.md`](benchmark-generator-loop-request.md) | [`benchmark-generator-loop-protocol.md`](benchmark-generator-loop-protocol.md) |
| Headless Claude Code/Codex baseline | [`benchmark-generator-claude-codex-request.md`](benchmark-generator-claude-codex-request.md) | [`benchmark-generator-claude-codex-protocol.md`](benchmark-generator-claude-codex-protocol.md) |
| Headless Claude Code/Codex feedback loop | [`benchmark-generator-claude-codex-loop-request.md`](benchmark-generator-claude-codex-loop-request.md) | [`benchmark-generator-claude-codex-loop-protocol.md`](benchmark-generator-claude-codex-loop-protocol.md) |

## Observability

[`observability-claude-codex-report.md`](observability-claude-codex-report.md)
records the Full Audit Headless Claude Code/Codex validation. See its
[`request`](observability-claude-codex-request.md) and
[`protocol`](observability-claude-codex-protocol.md) for the immutable inputs.

## Interactive runtime

[`interactive-runtime-v0.1.2-validation-report.md`](interactive-runtime-v0.1.2-validation-report.md)
records native-TUI Codex and mixed Interactive Claude Code/Codex loops,
including PTY capture, formal-action termination, process cleanup, trace
anchoring, and same-role Session continuity.

[`opencode-interactive-v0.1.4-validation-report.md`](opencode-interactive-v0.1.4-validation-report.md)
records the real four-Turn OpenCode Developer/Reviewer loop, including
independent Sessions, same-role Resume, a Reviewer finding cycle, exact output,
Full Audit evidence, process cleanup, and package/Skill installation. Its
retained [request](opencode-interactive-v0.1.4-request.md) and
[protocol](opencode-interactive-v0.1.4-protocol.md) define the acceptance task.

## DeepSeek Harness Origin

[`deepseek-harness-origin-v0.1.4-validation-report.md`](deepseek-harness-origin-v0.1.4-validation-report.md)
records the real DSH Skill load and one-Turn restricted Codex External Role
loop, including the winning `filesystem` provider/resource base, explicit
`deepseek-harness` Origin metadata, formal Completion delivery, trace integrity,
process cleanup, and package installation.

## DeepSeek Harness interactive External role

[`deepseek-harness-interactive-v0.1.4-validation-report.md`](deepseek-harness-interactive-v0.1.4-validation-report.md)
records a real three-Turn DSH Developer → Origin Reviewer → same DSH Session
loop. It covers the managed runtime and bundled TUI, native cross-process
Session Resume, formal actions, bounded traces, private Run/Role state, a
permission defect found by the first run, and the successful fixed recheck.

## Reading the evidence

- Start with the report matching the behavior under review.
- Treat Request and Protocol files as retained Run inputs, not current product
  documentation.
- Use the repository's current test suite and normative technical design for
  the latest implementation contract.
- Do not infer coverage beyond each report's explicit acceptance boundary.
