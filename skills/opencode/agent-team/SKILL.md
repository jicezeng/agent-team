---
name: agent-team
description: Create and operate temporary, natural-language-defined coding-agent teams across Codex, Claude Code, OpenCode, and DeepSeek Harness. Use for dynamic roles, cross-harness collaboration, explicit handoffs, iterative review loops, resumable role sessions, or completion returned to the current Origin session.
compatibility: opencode
---

# Agent Team

Turn the user's one-shot team request into immutable run inputs, start the local
runtime, and keep the Origin turn alive until completion or a user-visible
Block. Do not treat conceptual discussion of multi-agent systems as
authorization to start a Run.

Read [coordination.md](references/coordination.md) completely before Bootstrap
or whenever receiving an Origin event. Use
[protocol-template.md](references/protocol-template.md) when generating the
Run-specific protocol.

## Existing External Turn

If `AGENT_TEAM_RUN_ID`, `AGENT_TEAM_ROLE_ID`, and `AGENT_TEAM_TURN_ID` are
present, or the current prompt starts with `# Agent-Team role turn`, this is an
External business Turn inside an existing Run:

1. Do not Bootstrap, wait, Resume, Cancel, or load this Skill again.
2. Read the authoritative Request, Protocol, input, and Facts named by the
   current prompt, then perform only the named dynamic role.
3. Finish with exactly one shell invocation of the absolute
   `$AGENT_TEAM_CLI` command shown in the prompt: `handoff`, `complete`, or
   `block` with a Markdown payload inside the Turn directory.
4. Stop business work after that command succeeds. The Skill is guidance, not
   a state-transition interface.

## Bootstrap

1. Preserve the user's exact request in `REQUEST.md` outside `.agent-team`.
2. Extract dynamic roles, Origin/External bindings, Harness adapters, Session
   policies, initial role, routing loops, completion authority, final delivery,
   safety limits, and only the model or variant choices the user made.
3. Reject true parallel fan-out/join, multiple workspaces, non-Git roots,
   missing completion conditions, unavailable Harnesses, or dangerous
   ambiguity. Do not silently serialize requested parallel work.
4. Record every inference under `Assumptions made during bootstrap`. Keep
   business behavior in natural-language `PROTOCOL.md`; do not invent a
   workflow DSL or parse reviewer prose as machine state.
5. Choose each External Launch Profile from `agent-team doctor` output. Each
   new External role defaults to `full-access` unless the user explicitly selects
   `default` or `trusted-workspace`. Before every new Run containing a
   `full-access` role, disclose host filesystem, credential, and network risk
   and obtain one explicit confirmation for that Run. An explicit confirmation
   in the same user request counts; a prior Run's confirmation does not. Record
   it in the Protocol and pass `--confirm-full-access` only after consent.
   If the user declines, do not create or start the Run.
   OpenCode has no OS Bash sandbox: its restricted Profiles allow worktree file
   tools and formal Agent-Team commands, while arbitrary Bash remains denied;
   `trusted-workspace` additionally allows built-in web tools. Managed
   DeepSeek Harness restricted Profiles constrain file writes to the worktree
   but inherit host reads, processes, environment credentials, and network;
   its `default` and `trusted-workspace` mappings are identical in v0.1.
   Managed
   administrator Harness policy remains outside Doctor's complete proof. On a
   managed host, use a contained Profile only after verifying that policy adds
   no host write roots, sandbox exclusions, hooks, or higher-priority tool
   grants.
6. Add `--role-model ROLE=MODEL` and
   `--role-reasoning-effort ROLE=VARIANT` only for choices the user made.
   OpenCode models use `provider/model`; its reasoning-effort option maps to a
   provider-specific variant. DSH models also use `provider/model`, with effort
   `off`, `high`, or `max`. `--role-fast` remains Codex-only. External roles
   default to native `interactive` execution. Add
   `--role-launch-mode ROLE=headless` only for Codex, Claude Code, or OpenCode
   when explicitly requested; DSH External roles are interactive-only.
   Before starting an interactive Claude Code role, require the user to open
   `claude` once in the exact Workspace, accept Claude's independent workspace
   trust prompt, and exit. Never edit its trust state or accept it with tmux
   input; `HARNESS_WORKSPACE_TRUST_REQUIRED` is a pre-Kickoff retry of the same
   UNSTARTED Run, not a second Agent-Team permission decision.
7. Choose the observability policy. Use `full` only when every business role is
   External and Origin is control-plane only; otherwise use `standard`.
8. Resolve `agent-team` once to its canonical absolute executable path and
   retain that literal path for the entire Origin loop. Substitute it for
   `<absolute-agent-team-cli>` in every command below; do not re-resolve it
   from `PATH` between turns. Generate Request and Protocol, then run the exact
   CLI flow:

```bash
"<absolute-agent-team-cli>" init \
  --request <REQUEST.md> \
  --protocol <PROTOCOL.md> \
  --role <role>=<binding-spec> \
  --initial-role <role> \
  --max-turns <positive-int> \
  --max-wall-time-seconds <positive-int> \
  --audit-mode <standard|full> \
  --trace-redaction standard \
  --max-trace-bytes 67108864 \
  --raw-retention redacted \
  --require-rationale-evidence \
  --origin-harness opencode
"<absolute-agent-team-cli>" start <run-id> --confirm-full-access
```

Omit `--confirm-full-access` when all External roles explicitly use restricted
Profiles. Save the Run ID and immediately call:

```bash
"<absolute-agent-team-cli>" wait-origin --run <run-id> --timeout 90
```

## Origin loop

- On `ORIGIN_KICKOFF`, `HANDOFF_TO_ORIGIN_ROLE`, or `RESUME_TO_ORIGIN_ROLE`,
  read the immutable input and Facts, execute exactly the dynamic role, and use
  the matching `origin-*` command with its Turn and Claim.
- Claims are opaque. Pass them only as `--claim=<exact-value>` on every
  `wait-origin` and `origin-*` command because a historical Claim may begin
  with `-`.
- `origin-handoff` submits and waits in one call. After a wait timeout, stop
  business work and call `wait-origin` without the old Claim.
- Make `origin-complete` or `origin-block` the final tool call of the Agent
  turn. Every Block returns to the user before any Resume.
- On the next user Agent turn, first call `wait-origin` with the prior Claim to
  finalize an `exited` Origin runtime before doing new business work.
- Resume only after a new explicit user instruction:

```bash
"<absolute-agent-team-cli>" origin-resume \
  --run <run-id> --claim=<management-claim> \
  --to <role-id> --file <exact-user-instruction.md> \
  --wait-timeout 90
```

- Limit/Profile changes and changes to immutable Request, Protocol, roles,
  bindings, workspace, launch mode, profile, model, variant, fast mode, or
  limits require Cancel plus a new Run.
- On `TEAM_COMPLETED`, inspect the Completion Package, final Facts, artifacts,
  tests, loop history, transcript, and trace manifests before reporting to the
  user.

## Structured control

Use `status --json` and `diagnose --json`; act on the structured envelope,
health, recommended action, and evidence paths. Use `transcript --json` and
`tail --jsonl` only for audit. Never infer routing, completion, Resume, Unlock,
or recovery from Pane text, Harness prose, or logs.

`"<absolute-agent-team-cli>" attach [<run-id>] --role <role>` is a read-only
live view. The
native Codex, Claude Code, or DSH TUI and OpenCode direct-interactive terminal
may expose ordinary Harness interaction,
but terminal input and Pane content are never formal Agent-Team actions; Outbox
actions and the Journal are authoritative.

Never share, guess, or replace another Origin session's Claim. Claim loss has
no takeover path in v0.1: diagnose, cancel the old Run, confirm the Origin Turn
stopped, safely Unlock if needed, then Bootstrap a new Run.
