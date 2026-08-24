# Four-Harness Interactive Validation Report

> **Result**: PASS  
> **Date**: 2026-08-20  
> **Candidate**: working tree later committed as `5092b4e` and released in v0.1.5  
> **Authoritative Run**: `at-four-harness-provider-env-regression-v4-20260820`

## Scope

This report records a real-machine, full-access relay across every supported
External Harness at the time: Codex, Claude Code, OpenCode, and DeepSeek
Harness. The immutable [request](four-harness-interactive-v0.1.5-request.md)
and [protocol](four-harness-interactive-v0.1.5-protocol.md) required five
managed business Turns in the exact route:

```text
Codex → Claude Code → OpenCode → DeepSeek Harness → resumed Codex
```

Each role had an independent private Harness home and Session. The final Codex
Turn had to resume the first Codex Session, and every role was restricted by
the business protocol to one append in `relay.md`, despite the explicitly
confirmed `full-access` launch profile.

## Frozen roles

| Role | Harness | Model | Session policy |
| --- | --- | --- | --- |
| `codex` | Codex | `gpt-5.6-sol`, high, Fast Mode | `resume` |
| `claude` | Claude Code | `doubao-seed-2.0-pro`, max | `resume` |
| `opencode` | OpenCode 1.18.18 | `volcengine/doubao-seed-evolving` | `resume` |
| `dsh` | DeepSeek Harness 0.1.0-rc.6 | `deepseek-official/doubao-seed-evolving`, high | `resume` |

OpenCode used a frozen custom Provider whose secret was referenced by
environment-variable name and injected only into the Worker process. The Run
record retained the Provider structure and environment reference, not the
credential value.

## Acceptance result

The final Journal contains exactly one Kickoff, four Handoffs, and one
Completion. Current read-only reconstruction reports `COMPLETED`, `health=ok`,
no active Turn, all roles stopped, and Workspace Ownership released.

| Turn | Role | Marker / action | Session continuity |
| --- | --- | --- | --- |
| `turn-0001` | Codex | append `CODEX-1`; hand off | `01a01f73-07be-70c1-af33-986bfd7eff99` |
| `turn-0002` | Claude Code | append `CLAUDE`; hand off | independent Claude Session |
| `turn-0003` | OpenCode | append `OPENCODE`; hand off | independent OpenCode Session |
| `turn-0004` | DeepSeek Harness | append `DSH`; hand off | independent DSH Session |
| `turn-0005` | Codex | append `CODEX-2`; complete | same Ref as `turn-0001` |

All five Runtime snapshots finalized with `outcome=success`,
`adapter_completed=true`, `termination_kind=action`, and
`group_quiescent=true`. The apparent signal exit codes for the interactive
Codex, Claude Code, and OpenCode processes are expected formal-action
termination after the durable action was recorded; they are not failed Turns.

The final artifact contained each marker exactly once and in order:

```text
CODEX-1 → CLAUDE → OPENCODE → DSH → CODEX-2
```

`git diff --check` passed, `git diff --name-only` returned only `relay.md`, no
non-Agent-Team untracked file was introduced, and the final artifact SHA-256
was `d645229da1384c54e216a1ea34109a065eb0517e2e2ce3298e00e1cab2141a7e`.

## Runtime and audit evidence

Full Audit used standard redaction, redacted raw retention, a 64 MiB per-Turn
limit, and mandatory `Decision rationale` and `Evidence` sections. No source
bytes were dropped and no trace was truncated.

| Turn | Stored chunks | Source bytes | Trace Manifest SHA-256 |
| --- | ---: | ---: | --- |
| `turn-0001` | 3,009 | 528,199 | `28ef093b4bd4a85110daec779067c3f212e98574d0d2432d1ce1769f50c11751` |
| `turn-0002` | 1,213 | 127,321 | `b3f162e8db0026117063857aaddf016bc4511ecd83a7469efce078d82ce215e6` |
| `turn-0003` | 32 | 16,854 | `c7403fad25a836ad4e05239e4e75dd48b3f8d4ea975751dc9b79e4dd2293d773` |
| `turn-0004` | 32 | 730 | `80d05c62b63929532f46db4f03f83ac03c70656da6f615c9476c413c2abce012` |
| `turn-0005` | 3,975 | 697,467 | `f3ea5c95719b6a3d43ad5f920551768533e42a9230d4158c20bb2a288d596385` |

The retained Protocol matches the authoritative Run copy byte for byte with
SHA-256
`2981435eb86b6fa4c22c30dde84228d42f3c28db6974cdd167037c699696bec6`.
The retained Request text normalizes only the Run copy's final blank line and
has SHA-256
`0ca0819ed7d0710571a375b84de6fa8e9ca56c1250e09ddf7698a38ab3bcbd21`;
the otherwise identical authoritative copy hashes to
`13c02dcd01ea48963ea7e53a451a76f0f165cf08239c20c4577c01017960d628`.

## Defects exposed before the authoritative Run

Earlier disposable iterations are not acceptance evidence. They exposed the
custom OpenCode Provider isolation/model-freezing defects, missing propagation
of Provider-referenced environment variables into tmux Workers, and Codex's
first-Turn write of `tui.model_availability_nux` being mistaken for immutable
profile drift. The final `v4` Run was started after those fixes and completed
without a Block.

## Acceptance boundary

This evidence proves a real serial collaboration across all four interactive
Harness adapters, independent role Sessions, same-Codex Session Resume,
Provider-backed OpenCode execution, formal actions, bounded Full Audit traces,
business-worktree confinement by protocol, process-group quiescence, terminal
Completion, and owner release on the recorded candidate.

The checked-in evidence deliberately excludes raw Run records and credentials.
It does not prove that current Harness versions or current Provider credentials
are identical to the 2026-08-20 environment, nor does it replace a new live
regression for later releases.
