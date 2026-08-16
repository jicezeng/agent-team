# Interactive DeepSeek Harness validation report

> **Result**: PASS  
> **Date**: 2026-08-16  
> **Agent-Team**: v0.1.4 working tree based on `c1429e3c3ec74974b174d3830b7501e1c889b73b`  
> **DeepSeek Harness**: managed `@deepseek-ai/dsh@0.1.0-rc.6`  
> **Authoritative Run**: `at-dsh-interactive-recheck`

## Scope

This report validates DeepSeek Harness (DSH) as a production Agent-Team
External role, not only as an Origin. The acceptance path required:

1. a managed DSH runtime and bundled TUI;
2. native interactive execution in the role's tmux Pane;
3. one DSH role invoked in two separate Agent-Team Turns;
4. the second process resuming the first process's native DSH Session;
5. formal Handoff and Completion through the Agent-Team Journal;
6. bounded, anchored traces and private retained state;
7. terminal process, tmux, Origin, and Workspace Ownership cleanup.

## Environment

| Component | Version / state |
| --- | --- |
| macOS | 26.5.2 |
| Python used by Agent-Team | 3.12.9 |
| uv | 0.11.28 |
| Node.js | 26.5.0 |
| pnpm | 11.11.0 |
| tmux | 3.7b |
| DSH runtime | 0.1.0-rc.6 |
| DSH authentication | `DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL` set; values not printed |
| Model | `deepseek-official/deepseek-v4-flash`, effort `high` |
| Launch Profile | `default` (`workspace-write`, approval `never`) |
| Launch Mode | `interactive` |

`agent-team install` reused and revalidated the managed DSH package with npm
integrity
`sha512-brpZfED7ieRa2PQ5tUxMhHrM1pb2CmKFVM/f6yMULBDMicahk+Z2OsHgTwTDnoiZm23Ftu9rQz0NN4pflaoJcg==`.
The selected Doctor checks for the DSH Adapter, DSH authentication visibility,
native Resume contract, installed Origin Skill, and final Run Store permissions
all passed. The aggregate Doctor result remained non-green only because the
unrelated Claude Code authentication probe is intentionally `unknown`.

## Scenario

The temporary Git worktree contained immutable Request and Protocol files. The
three business Turns were:

```text
turn-0001  DSH developer  create "turn-one\n"  → handoff
turn-0002  Origin reviewer  byte-exact read-only verification  → handoff
turn-0003  same DSH developer Session  append "turn-two\n"  → complete
```

The final file was exactly 18 bytes:

```text
7475726e2d6f6e650a7475726e2d74776f0a
```

That is `turn-one\nturn-two\n`; `git diff --check` exited 0.

## Interactive and Session evidence

Both External Turn Runtime records were finalized with:

- `adapter_completed=true`;
- `termination_kind=action`;
- `group_quiescent=true`;
- `session_generation=1`;
- the same observed Session Ref:
  `agent-team-294e2846-5fad-5e2c-b7da-f4a460ca5c93`.

The first LaunchSpec used:

```text
--profile agent-team
--session-id agent-team-294e2846-5fad-5e2c-b7da-f4a460ca5c93
```

The second LaunchSpec was created by a different Runner process and used:

```text
--profile agent-team
--resume agent-team-294e2846-5fad-5e2c-b7da-f4a460ca5c93
```

Both LaunchSpecs had `launch_mode=interactive`, the same private `DSH_HOME`, and
the same frozen Profile SHA-256. The private DSH Session header contained the
same ID and exact normalized worktree path. Its event log contained two
`turn/start` events, proving that native DSH persisted and resumed the Session
across processes rather than replaying a one-shot prompt.

The captured terminal began with the DSH Agent-Team interactive banner. Public
assistant text and bounded Tool state were visible; private reasoning was
represented only by `[thinking]` markers. No reasoning-delta text was rendered.

## Trace and lifecycle evidence

| Turn | Stored terminal chunks | Source bytes | Truncated | Trace SHA-256 |
| --- | ---: | ---: | --- | --- |
| `turn-0001` | 39 | 743 | no | `76a0730e8be0463d4bf16b10ef27e5d74bad663d1a1e9db82aa708c6506fd634` |
| `turn-0003` | 35 | 605 | no | `728ea4a75353bc162f0030e9c030b1110c814acc1dcb811ae31d73b31874a9ec` |

Both normalized traces were anchored by their finalized Turn Runtime and had
`normalized_trace_truncated=false`. Final `status --json` reported:

- `run_status=COMPLETED` and `health=ok`;
- three business Turns used;
- Developer state `stopped` and Origin state `finalized`;
- Workspace Owner `released`;
- no current role or active Turn;
- the role tmux Session absent after cleanup.

## Defect found and recheck

The first otherwise-successful run exposed a Run Store permission defect: an
Origin payload source created with ordinary mode `0644` remained in its Turn
directory, causing `doctor` to fail `workspace_state_permissions` after
Completion.

The implementation was changed so External actions, Origin actions, and Origin
Resume atomically read their Run-owned source through a non-symlink regular-file
descriptor, reject multiple hard links, and set mode `0600`. The authoritative
recheck deliberately created the Reviewer source as `0644`; immediately after
`origin-handoff` it was `0600`, and the final
`workspace_state_permissions` Doctor check passed.

## Deterministic regression and package evidence

- `uv run pytest`: **388 passed** after the permission fix.
- Focused Origin/Turn/integrity regression after the fix: **84 passed**.
- Managed DSH Runtime/TUI focused regression: **6 passed**.
- `ruff --select F` and Python bytecode compilation passed.
- `uv build --wheel` produced `agent_team-0.1.4-py3-none-any.whl`.
- Wheel inspection confirmed `dsh_runtime.py`,
  `adapters/deepseek_harness.py`, and all three bundled TUI files.

## Explicit limitations

- This was Standard Audit because the Origin Reviewer was a business role;
  Origin internals are outside External capture.
- The test observed the native TUI through its managed PTY capture, but did not
  inject manual keystrokes through a writable tmux client.
- Tool arguments/results are intentionally not exposed by the minimal TUI; only
  bounded Tool status and public assistant text are retained.
- DSH External Headless Mode was not tested because the Adapter rejects it by
  design.
- The restricted DSH Profile proves a workspace write boundary, not confinement
  of reads, process execution, environment credentials, or network access.
