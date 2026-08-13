# Agent-Team v0.1.4 OpenCode Interactive Validation Report

## Scope

This report records real-machine end-to-end acceptance of the OpenCode adapter
before the v0.1.4 implementation was committed.

- Validation date: 2026-08-14 (Asia/Shanghai)
- OpenCode: 1.18.18
- Model and variant: `deepseek/deepseek-v4-pro`, `high`
- Retained Run ID: `at-opencode-e2e-20260814-d`
- Roles: independent resumable OpenCode `developer` and `reviewer` sessions
- Launch mode: Interactive `opencode run --interactive` through managed PTYs
- Launch profile: explicit restricted `default`; no full-access confirmation
- Audit: Full, standard redaction, redacted raw retention, 2 MiB per Turn
- Immutable inputs: [request](opencode-interactive-v0.1.4-request.md) and
  [protocol](opencode-interactive-v0.1.4-protocol.md)

The disposable Git worktree started with one committed `README.md`. The only
business artifact produced by the Run was untracked `result.txt`.

## Acceptance result

The durable Journal contains exactly the required route:

1. `kickoff-0001` → `developer`;
2. `handoff-0002`: Developer → Reviewer;
3. `handoff-0003`: Reviewer finding → Developer;
4. `handoff-0004`: resumed Developer → Reviewer;
5. `complete-0005`: resumed Reviewer completion.

The final file contained exactly the ten bytes `phase-two\n`. Status reported
`COMPLETED`, `health=ok`, no active Turn, released Workspace Ownership, and two
stopped roles. Diagnose returned no failed or unknown checks.

Every Handoff and Completion payload contained non-empty `Decision rationale`
and `Evidence` sections. The Reviewer, not the Developer, issued the terminal
Completion.

## Session continuity and isolation

The first and resumed Turn for each role used one stable role-local Session:

| Turn | Role | Session | Launch evidence |
| --- | --- | --- | --- |
| `turn-0001` | developer | `ses_00386a2d7ffec7YQU9OmWwH6Dh` | Fresh; no `--session` |
| `turn-0002` | reviewer | `ses_00385f225ffenwXiRqNu8xD8hp` | Fresh; no `--session` |
| `turn-0003` | developer | `ses_00386a2d7ffec7YQU9OmWwH6Dh` | Same Ref passed with `--session` |
| `turn-0004` | reviewer | `ses_00385f225ffenwXiRqNu8xD8hp` | Same Ref passed with `--session` |

All four LaunchSpecs began with `opencode run --interactive`. The two Session
Refs differ, proving that the roles did not share a Session; both resumed Turns
retained generation 1 and the exact Ref from their first Turn.

## Runtime and audit evidence

All four Runtime snapshots finalized with `outcome=success`,
`agent_execution_started=true`, `adapter_completed=true`,
`group_quiescent=true`, a finished Supervisor snapshot, and an anchored Trace
Manifest:

| Turn | Captured chunks | Source bytes | Dropped | Truncated | Trace Manifest SHA-256 |
| --- | ---: | ---: | ---: | --- | --- |
| `turn-0001` | 25 | 10,098 | 0 | `false` | `86731717462deb5f3dc83aeaf8d34fe23dd966e4c07b400fd4743c2b32afe76e` |
| `turn-0002` | 7 | 646 | 0 | `false` | `d105d4d6b9113c3f840fec41ac988228f4c9e7af9bc326a15bd7380691ff7d04` |
| `turn-0003` | 8 | 596 | 0 | `false` | `b42b336564f63f0c401262caf6b8f024f3e762dd3ad76d5663111d86524fdb67` |
| `turn-0004` | 7 | 1,027 | 0 | `false` | `22a5db222c2798c965ac7081cece75a4b7710ccd1dea7d91b1ec30407467cde8` |

Interactive OpenCode provides terminal bytes rather than its Headless JSON
event stream, so these normalized events are intentionally diagnostic. The
adapter's structured message/tool/usage normalization is exercised by the
Headless adapter tests; this Run proves the managed Interactive lifecycle,
formal actions, and Session continuity.

## Defects found by the real loop

Pre-acceptance Runs `a` through `c` were explicitly recovered/cancelled and are
not acceptance evidence. They exposed four gaps that were fixed before Run `d`:

1. an empty OpenCode Session list is represented by empty stdout, not `[]`;
2. pre-Supervisor Interactive artifacts legitimately include `prompt.md`;
3. a TUI prompt must follow the generic Runner's immutable-prompt pointer
   contract instead of being supplied twice;
4. OpenCode 1.18.x full-screen Resume restores history but ignores a new
   `--prompt`, while `run --interactive --session` submits the new Turn to the
   same Session.

Post-Run Doctor also exposed that OpenCode's private Config Home contains
package-manager `.bin` symlinks. Doctor now accepts only symlinks whose resolved
ordinary target remains inside the checked private root; broken, special, or
escaping links still fail.

## Repository and installation gates

The final candidate was rebuilt as
`dist/agent_team-0.1.4-py3-none-any.whl`, force-installed with `uv tool`, and
installed the Codex Skill, Claude Code Plugin, and OpenCode Skill. Doctor then
reported Pass for the OpenCode executable, adapter, authentication, all three
Headless and Interactive Profile mappings, Session Resume, OpenCode Skill,
fixed state directory, Workspace state permissions, state consistency, and
released owner. Claude Code's non-model authentication probe remained the one
expected `unknown` check and is outside this acceptance boundary.

The final repository gates were:

```text
uv run pytest
351 passed

uv run ruff check --select F src tests
passed

uv run python -m compileall -q src tests
passed

git diff --check
passed

uv build --wheel
agent_team-0.1.4-py3-none-any.whl built successfully
```

## Acceptance boundary

This evidence proves real OpenCode Interactive Start, independent role
Sessions, formal bidirectional Handoffs, same-role Resume, exact task output,
Reviewer-authorized Completion, Full Audit capture, process-group quiescence,
private runtime-state finalization, and package/Skill installation on the
recorded versions. It does not claim that OpenCode exposes structured tool or
reasoning events in Interactive mode, nor that any restricted Profile can run
arbitrary shell commands.
