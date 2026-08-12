# Observability Claude Code/Codex Validation Report

> Historical Headless structured-stream validation from 2026-07-29. Native-TUI
> Interactive Codex evidence added later is recorded separately in
> [`interactive-runtime-v0.1.2-validation-report.md`](interactive-runtime-v0.1.2-validation-report.md).
> This report preserves the package version, commands, counts, and Harness mode
> that were actually validated at the time.

## Result

The six observability acceptance areas in
`observability-claude-codex-request.md` are implemented and passed a real
Full Audit Developer/Reviewer loop. The successful Run ended `COMPLETED` with
`health=ok`, released its Workspace owner, and had no failing diagnostic
check.

- Validation date: 2026-07-29 (Asia/Shanghai)
- Run: `at-observability-claude-codex-r3`
- Developer: external Claude Code, resumable Session
  `a0891be0-7c6b-4c7b-bd35-8cce65ea0ab8`
- Reviewer: independent external Codex, resumable Session
  `019fadd1-ff27-7cb0-ae7e-651f148385dc`
- Reviewer was the sole Completion Authority.
- Formal route: kickoff, seven handoffs, then reviewer completion
- Business Turns: 8
- Audit policy: Full Audit, standard redaction, 64 MiB per-Turn limits,
  redacted raw retention, required rationale/evidence sections

## Six-area evidence

| Area | Result | Evidence |
| --- | --- | --- |
| Anchored manifests | Pass | All 8 External Turns have a manifest containing capture counts and retained-artifact byte sizes/SHA-256 hashes. Every manifest hash matches the set-once Runtime anchor. Status, diagnose, transcript, and recovery validate the anchor and artifacts. |
| Normalized events | Pass | Claude Code and Codex records produced 332 normalized events with raw stdout/stderr sequence references. Supported kinds include messages, tools, file changes, usage, errors, and explicit summaries; unknown/non-JSON records use fallback events. Private Claude `thinking` and generic `reasoning` content is excluded by regression tests. |
| Transcript and tail | Pass | Both commands support Role/Turn filters and JSON/JSONL output. The completed Run's transcript aggregated event/tool counts plus available token, cost, and duration fields. |
| Full Audit | Pass | Both business roles were External and Origin remained the control plane. All raw and normalized captures were non-truncated. Tests cover technical Block creation for either truncation path. |
| Formal payload contract | Pass | Every audited Handoff, Completion, and Agent Block requires non-empty `Decision rationale` and `Evidence` sections. Prompts and documentation explicitly reject claimed or reconstructed hidden chain-of-thought. |
| Policy and privacy | Pass | Configuration provides heuristic secret redaction, independent per-Turn source/normalized byte limits, and `redacted`, `keep`, and `delete` raw-retention modes with Full Audit restrictions. README and the design state the retained-raw privacy boundary explicitly. |

## Trace inventory

| Turn | Role | Events | Retained artifacts | Source bytes | Dropped source bytes | Normalized omitted | Truncated |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 0001 | developer / Claude Code | 150 | 8 | 1,075,693 | 0 | 0 | no |
| 0002 | reviewer / Codex | 26 | 9 | 240,945 | 0 | 0 | no |
| 0003 | developer / Claude Code | 44 | 8 | 201,653 | 0 | 0 | no |
| 0004 | reviewer / Codex | 17 | 9 | 55,983 | 0 | 0 | no |
| 0005 | developer / Claude Code | 27 | 8 | 111,414 | 0 | 0 | no |
| 0006 | reviewer / Codex | 17 | 9 | 34,966 | 0 | 0 | no |
| 0007 | developer / Claude Code | 23 | 8 | 65,110 | 0 | 0 | no |
| 0008 | reviewer / Codex | 28 | 9 | 49,023 | 0 | 0 | no |
| **Total** |  | **332** | **68** | **1,834,787** | **0** | **0** | **no** |

Completed-Run transcript summary:

- 22 agent messages
- 140 tool calls and 139 tool results
- 8 usage events and 8 Session events
- 8 file-change events
- 11,307,870 input tokens and 57,433 output tokens reported by the Harnesses
- 5,341 reasoning-output tokens reported as usage metadata
- USD 7.4466326 reported cost
- 902,120 ms reported aggregate duration

Token and cost values are Harness-reported accounting fields; they are useful
for audit and comparison but are not portable billing guarantees.

## Reviewer loop

The same Reviewer Session performed a complete re-review after each correction:

1. P1: Claude `thinking` content was being copied into a normalized
   `reasoning_summary`.
2. P1: generic Claude `reasoning` text was also being treated as an exposed
   summary.
3. P2: the content-free diagnostic hardcoded `block_type=thinking`, so generic
   reasoning was mislabeled.

The Developer accepted and fixed all three findings. The final adapter retains
content only for an explicit `reasoning_summary` block. Private `thinking` and
generic `reasoning` blocks produce only a content-free diagnostic with the
source sequence reference and actual block type. No P0-P3 finding remained.

## Privacy qualification

This implementation does not and cannot guarantee access to a model's hidden
chain-of-thought. It records explicit formal rationale/evidence and any
summary the Harness deliberately exposes.

The four real Claude Code Turns did not emit a `thinking`, `reasoning`, or
`reasoning_summary` content block, so the mixed-Harness Run proves trace
capture, normalization, manifest integrity, completeness, Session continuity,
and policy enforcement but does not live-exercise that provider branch.
Synthetic adapter regressions verify that explicit summaries are retained and
private thinking/reasoning sentinels do not enter `trace.jsonl`.

Retained raw output is deliberately treated separately. Standard redaction is
heuristic secret substitution, not a privacy or confidentiality boundary; if a
Harness emits private text in its raw stream, `redacted` may still retain it
and `keep` certainly does. Full Audit therefore favors completeness over
minimal retention and should be used only with an appropriately protected Run
Store.

## Final independent verification

After the mixed-Harness Completion, the Origin session verified the final live
source and rebuilt/reinstalled the exact candidate:

```text
uv run pytest
176 passed in 38.14s

uv run python -m compileall -q src tests
passed

git diff --check
passed

uv build
Successfully built dist/agent_team-0.1.0.tar.gz
Successfully built dist/agent_team-0.1.0-py3-none-any.whl

uv tool install --force dist/agent_team-0.1.0-py3-none-any.whl
agent-team install
passed
```

Post-install `agent-team doctor --workspace ... --json` confirmed version
`0.1.0`, Git/tmux/Codex/Claude availability, equivalent Start/Resume profiles,
Codex authentication, both matching installed integration trees, private state
permissions, supported filesystem primitives, an ignored/untracked
`.agent-team/`, and an available Workspace. Claude authentication remained
`unknown` because Doctor does not make an interactive/model call; the eight
successful real External Turns provide the direct execution evidence.

The optional Ruff invocation could not run because Ruff is not installed in
the project environment. It is not one of the request's minimum gates and is
not reported as passed.
