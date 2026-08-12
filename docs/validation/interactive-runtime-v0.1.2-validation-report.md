# Agent-Team v0.1.2 Interactive Runtime Validation Report

## Scope

This report supplements, rather than rewrites, the historical lifecycle and
observability reports. It records the evidence available for the native-TUI
Interactive implementation after the Worker/Supervisor/Runner PTY changes and
the later interactive-state recovery hardening.

- Validation implementation: the `0.1.2` worktree snapshot described below
- Release state: the `0.1.2` version metadata was still uncommitted when these
  gates were recorded; this is validation of the current worktree, not a claim
  that a tagged or committed `0.1.2` release exists
- Validation date: 2026-08-13 (Asia/Shanghai)
- Retained real Runs:
  - `at-interactive-codex-fixed-20260806` in the sibling
    `benchmark-generator` worktree;
  - `at-interactive-claude-mixed-r3-20260813` in the `agent-team` worktree.
- Harness coverage: a two-role Codex loop and a Claude Code → Codex → resumed
  Claude Code loop, all with External `interactive` bindings
- Audit mode: Full Audit with standard redaction and redacted raw retention

The mixed Run is real-machine acceptance evidence for Claude workspace trust,
default permission-profile launch, native TUI operation, formal handoff to
Codex, and same-session Claude resume. The earlier Claude Code/Codex reports
remain evidence for the Headless structured-stream path.

## Recorded repository gates

The following commands were run by the Origin control plane after the mixed Run
completed on 2026-08-13:

```text
uv run pytest
274 passed in 53.06s

git diff --check
passed

uvx --from ruff ruff check src/agent_team/assets.py tests/test_assets.py
passed

uvx --from ruff ruff format --check src/agent_team/assets.py tests/test_assets.py
passed
```

`ruff` was not installed in the project environment and the repository does not
define a project-wide Ruff ruleset, so this report claims a Ruff pass only for
the CLI identity files changed for this acceptance run.

The 274-test suite includes regressions for:

- raw/non-blocking terminal input relay and terminal-state restoration;
- PTY-backed Interactive Supervisor execution and durable formal-action stop;
- Interactive terminal JSON remaining diagnostic rather than Process Evidence;
- isolated per-Run/per-Role Codex Home, Session discovery, and private-state
  finalization;
- current/legacy Claude user-state precedence and Interactive workspace-trust
  fail-closed behavior;
- controlled migration of an owned legacy empty Codex `config.toml`;
- deferring Adapter private-state cleanup while a matching Supervisor remains
  in `stopping`;
- Adapter finalization during safely stopped damaged-Runtime recovery.
- the normative Runtime JSON example containing exactly the implementation's
  closed required-field set.
- mandatory current-Schema Capture artifacts, Raw Stream sequence/byte/count
  reconciliation, and rejection of incomplete Full Audit capture;
- closed Raw/Normalized Trace fields, duplicate-key and timestamp rejection,
  and deliberate ignoring of only an unterminated outer Stream tail;
- Team Schema 1-only Runtime trace-anchor compatibility and case-insensitive
  Full Audit payload-section configuration;
- the normative Status Role examples containing exactly the implementation's
  closed field set.
- deterministic `agent-team` console-script identity selection that prefers the
  current Python interpreter's sibling entry point over a process-specific
  `PATH`, while retaining the `PATH` fallback when no sibling exists.

## Real Interactive Claude Code/Codex loop

Run `at-interactive-claude-mixed-r3-20260813` configured two resumable External
roles in the current `agent-team` worktree:

- `claude-validator`: Claude Code, `launch_mode=interactive`,
  `launch_profile=default`, frozen profile SHA-256
  `fea8cfe2741adc57fe6de191471ebdbd9bd988ceb9101a35c2eaa6d1a613ce99`;
- `codex-reviewer`: Codex, `launch_mode=interactive`,
  `launch_profile=default`, frozen profile SHA-256
  `6fdf6c050191887c34d262947f71a00728368bd510ec2f40972c0eac78000038`.

The formal Journal route was exactly three business Turns:

1. `kickoff-0001` to `claude-validator`;
2. `handoff-0002` from Claude to `codex-reviewer`;
3. `handoff-0003` from Codex back to `claude-validator`;
4. `complete-0004` from the resumed Claude role.

Both Claude Turns recorded Session Ref
`6eab0c7a-e5ad-4bb3-af18-57779ac97550`, Session generation 1. The Codex Turn
recorded Session Ref `019ff6d4-324f-7ce1-9b72-64ae0c0317d3`, also at generation
1. This directly demonstrates a Claude native-TUI fresh start followed by a
later launch that resumed the same persisted Claude Session.

The Run also exercised the CLI identity fix across the `init` process and tmux
Worker environment. Before kickoff, two processes with different `PATH` values
both resolved `<agent-team-repo>/.venv/bin/agent-team` and
computed the same Claude Interactive profile hash shown above. All three Turns
then launched without `PROFILE_CHANGED_NEW_RUN_REQUIRED`; the earlier false
drift to the user-level uv-tool console script did not recur.

Every Runtime finalized with `outcome=success`, `adapter_completed=true`,
`termination_kind=action`, and `group_quiescent=true`. The Trace validation
evidence was:

| Turn | Role | Terminal events | Dropped bytes | Truncated | Trace Manifest SHA-256 |
| --- | --- | ---: | ---: | --- | --- |
| `turn-0001` | Claude | 712 | 0 | `false` | `80efd87894259bf97fd1f206bbbc3538d6927bcaeefca92f92bd3886bb7a2109` |
| `turn-0002` | Codex | 11,613 | 0 | `false` | `dd02e9278b17f590cee40fb0815bdd9e08e881fcc6778da3110ebdf8a44ad1b3` |
| `turn-0003` | Claude resume | 1,185 | 0 | `false` | `a1cece1cd5ec736eb7cf3dc2d68a9cfff646820bbd9c4bccb1d60401320a3a88` |

The Origin control plane revalidated each Manifest against its Runtime anchor,
Run/Role/Adapter identity, and frozen Full Audit policy. All six per-Turn
Before/After Workspace Facts recorded the same Git-visible state SHA-256
`3105b1c136c01de1413d87ac9c82de6400ae09837eebc3b25b38671857c8e203`
and Git HEAD `fca9a231f4f3be910bd56f282e2f76a37753b392`. Independent raw pre/post hashes
also matched: Git status `983f63ac18cd192298ac26dc91d607396810a9b75e2ef637ce12dbe0c9592958`
and binary diff `aec4ff6e54eb7b8466d8f6e732c2836a3ee8964f616550fbe1304d26dd6534c9`.

Codex's default sandbox denied the one Darwin legacy compatibility test that
executes `/bin/ps`, and also could not reach the host tmux socket under
`/private/tmp`. The reviewer recorded this limitation instead of turning it
into a false source-code pass. The Origin control plane, outside that Harness
sandbox, then ran the exact complete suite successfully: 274 tests passed. This
separates a real Harness sandbox limitation from an implementation failure.

Final `status --json` reported `COMPLETED`, `health=ok`, no active Turn,
`recovery_required=false`, `workspace_owner=released`, and
`recommended_action=READ_COMPLETION`. Final `diagnose --json` reported 12
applicable checks passed, seven terminal-state checks correctly not applicable,
and no failed or unknown checks.

## Real Interactive Codex loop

The immutable Schema 4 Team configured two resumable External Codex roles,
`analyst` and `verifier`, both with:

- `launch_mode=interactive`;
- `launch_profile=default`;
- frozen `model=gpt-5.6-sol`, `reasoning_effort=max`, and `fast_mode=true`;
- one role-scoped Session generation retained across later Turns.

The formal Journal route was:

1. `kickoff-0001` to `analyst`;
2. `handoff-0002` to `verifier`;
3. `handoff-0003` to `analyst`;
4. `handoff-0004` to `verifier`;
5. `complete-0005` from `verifier`.

All four business Turns finalized successfully. Each Runtime recorded:

- `agent_execution_started=true`;
- `adapter_completed=true`;
- `permission_required=false`;
- a non-empty observed Codex Session Ref;
- `termination_kind=action` with the real signal exit retained as
  `process_exit_code=-15`;
- `group_quiescent=true`;
- a non-empty `trace_manifest_sha256` anchor.

The `analyst` Session Ref was identical in Turns 1 and 3, and the `verifier`
Session Ref was identical in Turns 2 and 4. Both stayed at Session generation
1, directly demonstrating same-role Interactive Resume rather than a fresh
Session replacement.

## PTY and trace evidence

Every LaunchSpec recorded `launch_mode=interactive`. The four retained Streams
contained only `source=terminal` chunks, as expected for the unified PTY path:

| Turn | Terminal chunks | Source bytes | Dropped bytes | Truncated |
| --- | ---: | ---: | ---: | --- |
| `turn-0001` | 5,258 | 1,199,041 | 0 | `false` |
| `turn-0002` | 3,234 | 675,169 | 0 | `false` |
| `turn-0003` | 3,838 | 968,314 | 0 | `false` |
| `turn-0004` | 2,911 | 670,978 | 0 | `false` |

The generated transcript contained 15,241 normalized Diagnostic events, all
linked to Terminal raw sequence ranges. This is the intended Interactive audit
contract: native TUI bytes remain observable, but terminal text—even text that
resembles JSON—is not parsed into Agent execution, permission, Session, routing,
or completion evidence. Formal Outbox records and the Adapter Session Store
provided those state-changing facts.

## Post-run integrity check

On 2026-08-12, the current `0.1.2` CLI successfully read and validated the
retained Run:

- `status --json`: `COMPLETED`, `health=ok`, no active Turn,
  `recovery_required=false`, `workspace_owner=released`, and
  `recommended_action=READ_COMPLETION`;
- `diagnose --json`: all applicable Workspace lock, State Root, Run lock,
  configuration, Journal, Owner, Worker, Session, Workspace Facts, and recovery
  checks passed; terminal-only process checks were correctly not applicable;
- `transcript --json`: all four anchored Trace Manifests and their retained
  artifacts validated successfully.

## Acceptance boundary

This evidence closes the current real-machine acceptance gap for both the
two-Codex Interactive path and the mixed Interactive Claude Code/Codex path:
native PTY capture, formal-action termination, quiescent process cleanup, Full
Audit trace anchoring, role-scoped Session continuity, terminal-state
observation, stable cross-process CLI profile identity, and terminal Run
cleanup are demonstrated together.

The following remain outside this report and must not be inferred from it:

- a real Interactive Claude Code/Claude Code loop;
- proof that an Agent cannot deliberately create a process escaping the managed
  Runner process group;
- container-grade host isolation;
- structured tool/message/usage normalization from a native TUI stream—the
  Interactive stream is intentionally Diagnostic-only.
