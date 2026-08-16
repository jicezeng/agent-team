# DeepSeek Harness Origin validation report

> **Date**: 2026-08-16  
> **Result**: PASS  
> **Agent-Team candidate**: v0.1.4 working tree  
> **DeepSeek Harness**: v0.1.0-rc.5,
> `47f943859bef60e4160492346772ded9b24f765a`

## Scope

This validation covers the new Phase 1 boundary only: DeepSeek Harness (DSH)
loads the installed shared Agent-Team Skill, acts as the Origin, starts one
restricted Codex External role, waits for its formal Completion, and delivers
the result. It does not claim that DSH is an Agent-Team External Adapter.

## Build and deterministic regression

The candidate passed:

```text
uv run pytest                                      371 passed
uv run ruff check --select F src tests             passed
uv run python -m compileall -q src tests            passed
uv build                                            wheel + sdist passed
```

The wheel contained the shared Codex/DSH Skill body and both references under
`agent_team/bundled/skills/codex/agent-team/`.

## Installation and provider proof

`uv run agent-team install` installed the shared source at both:

```text
/Users/zengjice/.codex/skills/agent-team
/Users/zengjice/.dsh/skills/agent-team
```

`doctor --json` reported
`integration:deepseek_harness_skill.status=pass`, `installed=true`, and
`matches_bundled=true`. Its check catalog contained no `dsh` CLI requirement;
the DSH CLI was deliberately invoked from the fixed sibling checkout rather
than a globally installed `dsh` command.

Two independent DSH observations established the winning Skill:

1. The real headless DSH Session
   `session-16d82c64-058e-46af-b95a-3fe9369add2d` persisted a
   `user/message` at sequence 10 with source
   `{kind: "skill-invocation", name: "agent-team", form: "instructions"}`.
   Its canonical `<skill_resources>` block named
   `/Users/zengjice/.dsh/skills/agent-team` as the base directory.
2. Loading the same Skill through DSH v0.1.0-rc.5's actual
   `SkillRegistry` plus `FileSystemSkillProvider` returned:

```json
{"name":"agent-team","provider":"filesystem","source":"user-dsh","resourceBase":{"kind":"directory","path":"/Users/zengjice/.dsh/skills/agent-team"},"path":"/Users/zengjice/.dsh/skills/agent-team/SKILL.md"}
```

An additional read-only headless probe called the DSH `skill` tool exactly
once; Session `session-9d891fb6-a6bc-4d88-8c88-0289aa0e0481` records the
`skill` call at sequence 91 and the same resource base in its result at
sequence 92.

## Real Origin loop

The fixture was a fresh Git worktree containing tracked `.gitignore` and
`TARGET.txt`. The Origin was instructed not to read the target itself. It used
the following immutable Run shape:

```text
Run ID:          at-20260816-024824-4d98c5
Origin:          deepseek-harness / embedded
Initial role:    inspector
Binding:         codex:resume:default
Launch mode:     headless
Audit:           standard + required rationale/evidence
Limits:          1 business Turn / 900 seconds
```

DSH ran `init` with explicit `--origin-harness deepseek-harness`, then ran
`start` without `--confirm-full-access`. This was correct because the only
External role explicitly used the restricted `default` Profile. DSH's own
headless process used `DSH_PERMISSION_MODE=danger-full-access` solely so its
managed Bash could reach Agent-Team's account-level state directory; that DSH
host permission did not alter or authorize the External Codex Profile.

The persisted DSH Session records `DSH_SHELL=1`, the single canonical
Agent-Team executable
`/Users/zengjice/Projects/agent-team/.venv/bin/agent-team`, and these formal
results:

```text
init          RUN_INITIALIZED / UNSTARTED
start         RUNNING / kickoff-0001
wait-origin   TEAM_COMPLETED / complete-0002
```

The `wait-origin` result was:

```json
{"code":"TEAM_COMPLETED","event":{"event_id":"complete-0002","event_type":"complete","from_role":"inspector","turn_id":"turn-0001","payload_path":"completion/0002-inspector.md","payload_sha256":"9e4aa2d1eaa10ac551456bd1d97f9a84fdfe90cf1f987fe9e6ad65763656b8ba"}}
```

## Independent Agent-Team audit

The Run's own files—not DSH prose—proved:

- `team.json` recorded `origin.harness=deepseek-harness`, `session_mode=embedded`,
  `launch_profile=default`, and `launch_mode=headless`;
- `turn-0001` finalized with `outcome=success`, `adapter_completed=true`, exit
  code 0, `termination_kind=normal`, and `group_quiescent=true`;
- the Completion contained the exact token
  `DSH_ORIGIN_SMOKE_20260816_4f2d` and non-empty `Decision rationale` and
  `Evidence` sections;
- the Completion file SHA-256 was
  `9e4aa2d1eaa10ac551456bd1d97f9a84fdfe90cf1f987fe9e6ad65763656b8ba`,
  exactly matching `complete-0002.payload_sha256`;
- the trace manifest SHA-256 was
  `b3e97846a84cbd05f3c08fc0e01890cd4055ec4a9ce3da835e698f3d69405475`;
- the tracked target's committed and worktree Git blob IDs were both
  `cf063a134f68f683b1d22cb0d220e731a2a34c13`;
- final `status --json` reported `run_status=COMPLETED`, `health=ok`,
  `recommended_action=READ_COMPLETION`, and `workspace_owner=released`;
- the Run's tmux Session no longer existed.

One Codex model-catalog refresh timeout appeared as a non-fatal diagnostic.
The business Turn itself exited 0, completed normally, and retained a valid
trace, so it did not weaken the acceptance result.

## Conclusion

All Phase 1 acceptance points passed: the package installs one shared
Codex/DSH source, Doctor verifies only that copy, real DSH resolution selected
the `filesystem` / `user-dsh` resource, explicit Origin metadata reached
`team.json`, the restricted External role completed, and DSH received and
delivered `TEAM_COMPLETED`. No DSH Adapter, Bridge, SDK dependency, or second
state machine was required.
