# DeepSeek Harness Self-Hosted Plugin Validation Report

> **Result**: PASS for installation, load, and real tool invocation; documented comment-only P3 below  
> **Date**: 2026-08-20  
> **Agent-Team candidate**: working tree later committed as `5092b4e` and released in v0.1.5  
> **Authoritative Run**: `at-dsh-self-plugin-kiss-20260820`

## Scope

This report records a real self-hosted DeepSeek Harness collaboration in the
`deepseek-harness` repository. Three independent Agent-Team-managed DSH
External Agents completed the immutable
[request](deepseek-harness-self-plugin-v0.1.5-request.md) and
[protocol](deepseek-harness-self-plugin-v0.1.5-protocol.md):

```text
DSH Developer → independent DSH Reviewer → fresh DSH Validator
```

The candidate was the new `@deepseek-ai/dsh-worktree-status` package. It adds
a model-visible, read-only `worktree_status` tool that reports canonical Git
branch, HEAD, root, staged, unstaged, untracked, and conflicted state with
bounded path lists.

The parent DSH acted only as Origin. Agent-Team lazily created each role's
tmux Worker and private DSH Profile on first activation; the Validator was not
started until the Reviewer handed off to it. The Validator called the plugin
inside its own managed DSH Session and did not launch nested DSH or tmux from
Bash.

## Development and continuation history

The successful case was the end of a continuation chain. Cancelled or Blocked
predecessors are diagnostic and development evidence, not acceptance Runs:

| Run | Outcome | Useful result |
| --- | --- | --- |
| `at-20260820-044441-1f01ef` | cancelled | initial partial candidate preserved |
| `at-20260820-052822-1a7360` | blocked, then cancelled | DSH Developer completed the package, tests, catalogs, translations, and initial snapshot; Reviewer process crashed |
| `at-20260820-074053-59b556` | blocked, then cancelled | Reviewer process again exposed a runtime launch failure |
| `at-20260820-084419-0a8da1` | blocked, then cancelled | Reviewer found one P1 and one P2; Developer fixed both; re-review cleared P0-P2; Validator exposed missing credential inheritance |
| `at-dsh-self-plugin-kiss-20260820` | completed | clean re-review followed by Agent-Team-managed frozen install, real load, and direct tool invocation |

The substantive findings fixed in the predecessor loop were:

1. **P1**: add an assembled, keyless ACP transcript scenario rather than
   relying only on package tests;
2. **P2**: count dual-state Git entries such as `MM`, `AM`, `AD`, and `MD` in
   both staged and unstaged categories, while using the exact seven unmerged
   conflict pairs.

## Authoritative Run

All three roles used managed DeepSeek Harness 0.1.0-rc.6,
`deepseek-official/doubao-seed-evolving`, high reasoning effort, Interactive
Mode, and the explicitly confirmed `full-access` profile.

| Turn | Role | Session policy | Result |
| --- | --- | --- | --- |
| `turn-0001` | Developer | `resume` | inspected the preserved candidate and passed focused gates; handoff |
| `turn-0002` | Reviewer | `resume` | independent full review and verification; handoff |
| `turn-0003` | Validator | `fresh` | frozen install, direct `worktree_status` call, independent comparison; complete |

The three distinct Session Refs were:

- Developer: `agent-team-1a7cd20f-5c16-56b0-b8fe-9e54c136a761`;
- Reviewer: `agent-team-b771f789-71cf-53dd-aa54-076dfc66e382`;
- Validator: `agent-team-8837444a-c3ea-5ff1-944c-b9bc1467abea`.

Every Runtime finalized with `outcome=success`, `process_exit_code=0`,
`adapter_completed=true`, `termination_kind=action`, and
`group_quiescent=true`. Current read-only reconstruction reports `COMPLETED`,
`health=ok`, all roles stopped, no active Turn, and Workspace Ownership
released.

## Frozen plugin installation

Before first Validator activation, Agent-Team copied the workspace bundle into
the Validator-private Profile and froze its manifest:

| Field | Evidence |
| --- | --- |
| Source | `packages/git/worktree-status` |
| Package | `@deepseek-ai/dsh-worktree-status` |
| Version | `0.1.0-rc.5` |
| Bundle patch | `cordis.patch.yml` |
| Content SHA-256 | `a9bd992c392bd172e9400381d4944db78f1303430f349a51af6a26f03d80a9b7` |
| Installed files | 31, with a manifest identical to the frozen source manifest |
| Profile row | `tool-worktree-status`, `maxPaths: 100` |

The candidate hash was provided to the Validator process as
`AGENT_TEAM_DSH_PLUGIN_SHA256`; the private Profile dependency and installed
copy matched it independently.

## Direct model-visible invocation

The fresh Validator's own durable DSH Session records:

- sequence 526: `tool/call`, name `worktree_status`, arguments `{}`;
- sequence 527: matching `tool/result`, `isError=false`, with the canonical
  structured value in `data.meta`.

The result reported branch `master`, HEAD
`47f943859bef60e4160492346772ded9b24f765a`, repository root
`/Users/zengjice/Projects/deepseek-harness`, 0 staged, 16 unstaged, 28
untracked, and 0 conflicted paths. Independent `git rev-parse`, branch, root,
and NUL-delimited porcelain-status checks matched every field. No source file
was changed by the Validator.

## Repository verification

The final Validator reran the smallest sufficient gates:

```text
pnpm exec vitest run packages/git/worktree-status/tests
5 files, 64 tests passed

pnpm run verify-cordis-config
123 config files passed
```

The independent Reviewer also passed the authored ACP snapshot, package
TypeScript build, constraints, tool/config catalog freshness, source and built
invariant checks, export JSDoc, Agent Note format, README policy checks,
translation pairing, Markdown links, oxlint, and duplicate detection.

## Runtime and audit evidence

Full Audit used standard redaction, redacted raw retention, a 64 MiB per-Turn
limit, and required `Decision rationale` and `Evidence` sections. No source
bytes were dropped and no trace was truncated.

| Turn | Stored chunks | Source bytes | Trace Manifest SHA-256 |
| --- | ---: | ---: | --- |
| `turn-0001` | 239 | 5,001 | `c97b407270b1656268d67ba0f8bffb93b435b871a738aeab3036a81d874e5d4a` |
| `turn-0002` | 177 | 3,258 | `3f51fba9080c2dd7943a1cd279103c68cbf179fbd31c1504abad1f3af151a60c` |
| `turn-0003` | 114 | 2,287 | `e924a2f7937a554092a2902e7c3db844f35bfa3dcd40ad0e2b1f14d464d7856c` |

The retained Request and Protocol match the authoritative Run copies with
SHA-256 values
`99bcd950c28b22fe1d9fbe3fc3f88fd3e1a235672db28d47099fb203b8c7f253`
and
`1140886ce3ca0b7a04b8751e7902258f972cc080690c823f1200a866ef24dfd1`.

## Known review discrepancy

The predecessor re-review recorded one non-functional P3: an ACP fixture
comment calls a dual-state tracked file `AM`, while the fixture actually
produces `MM`. The behavior, tests, and durable result are correct, but that
comment remained in the preserved candidate. The authoritative continuation
Reviewer reported no P0-P3 findings and the Validator completed, so the
literal "no P0-P3 finding remains" wording was not perfectly satisfied.

This report therefore treats the functional installation/load/invocation E2E
as PASS while retaining the comment-only review discrepancy instead of
rewriting the historical verdict.

## Acceptance boundary

This evidence proves three independent DSH role Sessions, lazy role
activation, a role-local immutable plugin snapshot, installation into a fresh
private Profile, native DSH load, a direct model-visible tool call, durable
tool evidence, independent Git-result verification, formal Handoffs and
Completion, Full Audit traces, process quiescence, and owner release.

The DeepSeek Harness candidate was intentionally uncommitted at the recorded
HEAD, and this report does not claim it was later published. It also does not
claim current DSH, model endpoint, or repository state still matches the
2026-08-20 environment.
