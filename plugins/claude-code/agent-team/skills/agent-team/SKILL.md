---
name: agent-team
description: Create and operate temporary, natural-language-defined coding-agent teams across Codex and Claude Code. Use when a user requests multiple agents or dynamic roles, cross-harness collaboration, explicit handoffs, iterative developer/reviewer or QA loops, resumable role sessions, or completion returned to the current Origin session.
---

# Agent Team

Turn the user's one-shot team request into immutable run inputs, start the local
runtime, and keep the Origin turn alive until completion or a user-visible
Block. Do not treat conceptual discussion of multi-agent systems as
authorization to start a run.

Read [coordination.md](references/coordination.md) before Bootstrap or whenever
receiving an Origin event. Use
[protocol-template.md](references/protocol-template.md) when generating the
run-specific protocol.

## Existing External Turn

Check this before Bootstrap. If `AGENT_TEAM_RUN_ID`, `AGENT_TEAM_ROLE_ID`, and
`AGENT_TEAM_TURN_ID` are present, or the current prompt starts with
`# Agent-Team role turn`, you are already an External business role inside an
existing Run:

1. Do not Bootstrap, wait, Resume, Cancel, or invoke this Skill again.
2. This Skill is documentation only. It has no `--complete`, `--summary`, or
   other action arguments; repeated Skill calls cannot change Run state.
3. Read [coordination.md](references/coordination.md), then execute only the
   dynamic role and frozen input named by the current prompt.
4. End the Turn with exactly one Bash invocation of the absolute
   `$AGENT_TEAM_CLI` command shown in the prompt:
   `handoff --to ... --file ...`, `complete --file ...`, or
   `block --file ...`.
5. Stop business work after that CLI command succeeds.

## Bootstrap

1. Preserve the user's exact request in a local `REQUEST.md`.
2. Extract dynamic roles, Origin/External bindings, external adapters, session
   policies, initial role, handoff/loop rules, completion authority, final
   delivery, safety limits, and any model, reasoning-effort, or Codex fast-mode
   choices the user explicitly made.
3. Reject true parallel fan-out/join, multiple workspaces, non-Git roots,
   missing completion conditions, unavailable Harnesses, or dangerous
   ambiguity. Do not silently serialize a requested parallel topology.
4. Record every inference under `Assumptions made during bootstrap`. Keep
   business conditions in natural-language `PROTOCOL.md`; do not invent a
   workflow DSL or machine-parse reviewer verdicts.
5. Choose each External Launch Profile from `agent-team doctor` output. A new
   External role defaults to `full-access` (YOLO) unless the user explicitly
   selects the restricted `default` or `trusted-workspace` Profile. Before
   initializing and starting every new Run that contains a `full-access` role,
   disclose once that its agents can access the host filesystem, credentials,
   and network without per-command approvals, and obtain one explicit user
   confirmation for that Run. An explicit confirmation in the same user
   request counts; a prior Run's confirmation does not. After confirmation,
   record the choice and boundary in `PROTOCOL.md`, pass
   `--confirm-full-access` to the first Start attempt, and do not ask again for
   later Turns, Handoffs, Resumes, Recovery, or a retry of the same immutable
   UNSTARTED Run. If the user declines, do not create or start the Run.
   `trusted-workspace` may expand only
   capabilities its Adapter can expose without losing the Workspace boundary;
   Claude Code keeps `acceptEdits` and the same OS sandbox for this Profile.
   `full-access` removes the host sandbox and is appropriate only on a
   controlled machine or VM. Never infer elevated access from a role name, a
   request to run tests, or a natural-language `read-only` restriction, and
   never treat mutable local Harness settings as the selected Profile. Doctor
   shows the Agent-Team-supplied mapping, not the final effect of
   administrator-managed Harness policy. On a managed host, use a
   workspace-contained Claude Profile only after verifying that policy adds no
   host write roots or sandbox exclusions. Agent-Team disables non-managed
   Codex hooks, but trusted Workspace project configuration and extensions
   remain part of that Workspace's trust boundary. Also verify that Codex
   requirements do not force managed hooks or add log effects; an incompatible
   requirement can reject the selected Profile and must not be bypassed.
6. Add `--role-model <role>=<model>`,
   `--role-reasoning-effort <role>=<effort>`, or `--role-fast <role>` only for
   choices the user explicitly made. Do not infer these choices from role names
   or task complexity. Omit each unspecified option so `init` snapshots that
   Harness user's default for the field. `--role-fast` is Codex-only.
   Launch mode is separate: new External roles default to native
   `interactive` PTY execution in their tmux Pane. Add
   `--role-launch-mode <role>=headless` only when the user explicitly requests
   headless/structured-stream execution; an explicit `interactive` value may
   be recorded but is normally redundant. Before starting any interactive
   Claude Code role, require the user to have opened `claude` once in the exact
   Workspace (or a trusted parent), accepted its workspace-trust prompt, and
   exited. Never edit Claude's user trust state or accept the prompt with
   `send-keys`. `HARNESS_WORKSPACE_TRUST_REQUIRED` occurs before Kickoff, so
   after that one-time confirmation retry the same UNSTARTED Run; headless
   Claude roles do not require this preflight. This is Claude's independent
   worktree prerequisite, not another Run permission decision. For
   `full-access`, the Adapter reuses the confirmation from step 5 to suppress
   Claude's separate dangerous-mode prompt.
7. Choose and record the observability policy. Use `full` only when every
   business role is External and the Origin is control-plane only; otherwise
   use `standard` and disclose that Origin role internals are not captured.
   Keep standard redaction and redacted raw retention unless the user
   explicitly requests another privacy tradeoff.
8. Write Request and Protocol outside `.agent-team`, then run:

```bash
agent-team init \
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
  --require-rationale-evidence
agent-team start <run-id> --confirm-full-access
```

Omit `--confirm-full-access` when every External role explicitly uses a
restricted Profile. The flag asserts that the user confirmation required in
step 5 has already occurred; never pass it speculatively.

9. Save the returned Run ID and immediately call
   `agent-team wait-origin --run <run-id> --timeout 90`.

## Origin loop

- On `ORIGIN_KICKOFF`, `HANDOFF_TO_ORIGIN_ROLE`, or
  `RESUME_TO_ORIGIN_ROLE`, read the returned immutable input and facts, execute
  exactly the dynamic role, then use the matching `origin-*` command with its
  Turn and Claim.
- Treat every Claim as opaque and pass it only as `--claim=<exact-value>` on
  `wait-origin` and every `origin-*` command. Never split the option and value;
  an older Claim may start with `-`.
- Use `origin-handoff` for routing. It submits and waits in one call. If it
  times out after submission, do not continue business work; call
  `wait-origin` without the old Claim.
- Make `origin-complete` or `origin-block` the last tool call of the current
  Agent turn. After either returns, only deliver the Completion/Block to the
  user.
- On the next user Agent turn, first call `wait-origin` with the prior Claim to
  finalize an `exited` Origin runtime.
- On `TEAM_COMPLETED`, inspect the Completion Package, final facts, artifacts,
  tests, loop history, `transcript --json`, and anchored trace manifests;
  deliver an evidence-backed summary rather than forwarding one sentence.
- On `TEAM_BLOCKED`, show the Block to the user and end the Agent turn.
  Read-only diagnosis or deterministic `recover` may precede the response, but
  never Resume in the same turn.
- Resume only after a new, explicit user instruction:

```bash
agent-team origin-resume \
  --run <run-id> --claim=<management-claim> \
  --to <role-id> --file <exact-user-instruction.md> \
  --wait-timeout 90
```

Limit/Profile Changed Blocks and changes to the Request, Protocol, roles,
bindings, workspace, launch mode, profile, model, reasoning effort, fast mode,
or limits require Cancel plus a new Run.

## Structured control

Use `status --json` and `diagnose --json`; act on the structured envelope,
`health`, `recommended_action`, and evidence paths. Use `transcript --json`
and `tail --jsonl --role <role>` only for audit and observation. Never parse
Pane text, human-readable Status, Harness prose, or logs to decide routing,
completion, Resume, Unlock, or recovery.

`agent-team attach [<run-id>] --role <role>` is a read-only live view. Omit the
Run ID inside its actively owned Workspace, or provide it explicitly. An
interactive role shows its native Harness TUI. An operator may explicitly use
a writable tmux client for manual TUI input, which the Supervisor relays as raw
terminal bytes, but neither the Pane nor that input is a formal Agent-Team
action channel; formal CLI Outbox actions and the Journal remain authoritative.

Never share, guess, or replace another Origin session's Claim. Claim loss has
no takeover path in v0.1: diagnose read-only, cancel the old Run, confirm the
old Origin turn stopped, safely Unlock if required, then Bootstrap a new Run.
