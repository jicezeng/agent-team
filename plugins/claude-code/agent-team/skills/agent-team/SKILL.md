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

## Bootstrap

1. Preserve the user's exact request in a local `REQUEST.md`.
2. Extract dynamic roles, Origin/External bindings, external adapters, session
   policies, initial role, handoff/loop rules, completion authority, final
   delivery, and safety limits.
3. Reject true parallel fan-out/join, multiple workspaces, non-Git roots,
   missing completion conditions, unavailable Harnesses, or dangerous
   ambiguity. Do not silently serialize a requested parallel topology.
4. Record every inference under `Assumptions made during bootstrap`. Keep
   business conditions in natural-language `PROTOCOL.md`; do not invent a
   workflow DSL or machine-parse reviewer verdicts.
5. Choose each External Launch Profile explicitly from `agent-team doctor`
   output. Never infer it from a role name or a natural-language `read-only`
   restriction.
6. Write Request and Protocol outside `.agent-team`, then run:

```bash
agent-team init \
  --request <REQUEST.md> \
  --protocol <PROTOCOL.md> \
  --role <role>=origin \
  --role <role>=codex:resume:default \
  --initial-role <role> \
  --max-turns <positive-int> \
  --max-wall-time-seconds <positive-int>
agent-team start <run-id>
```

7. Save the returned Run ID and immediately call
   `agent-team wait-origin --run <run-id> --timeout 90`.

## Origin loop

- On `ORIGIN_KICKOFF`, `HANDOFF_TO_ORIGIN_ROLE`, or
  `RESUME_TO_ORIGIN_ROLE`, read the returned immutable input and facts, execute
  exactly the dynamic role, then use the matching `origin-*` command with its
  Turn and Claim.
- Use `origin-handoff` for routing. It submits and waits in one call. If it
  times out after submission, do not continue business work; call
  `wait-origin` without the old Claim.
- Make `origin-complete` or `origin-block` the last tool call of the current
  Agent turn. After either returns, only deliver the Completion/Block to the
  user.
- On the next user Agent turn, first call `wait-origin` with the prior Claim to
  finalize an `exited` Origin runtime.
- On `TEAM_COMPLETED`, inspect the Completion Package, final facts, artifacts,
  tests, and loop history; deliver an evidence-backed summary rather than
  forwarding one sentence.
- On `TEAM_BLOCKED`, show the Block to the user and end the Agent turn.
  Read-only diagnosis or deterministic `recover` may precede the response, but
  never Resume in the same turn.
- Resume only after a new, explicit user instruction:

```bash
agent-team origin-resume \
  --run <run-id> --claim <management-claim> \
  --to <role-id> --file <exact-user-instruction.md> \
  --wait-timeout 90
```

Limit/Profile Changed Blocks and changes to the Request, Protocol, roles,
bindings, workspace, profile, or limits require Cancel plus a new Run.

## Structured control

Use `status --json` and `diagnose --json`; act on the structured envelope,
`health`, `recommended_action`, and evidence paths. Never parse Pane text,
human-readable Status, Harness prose, or logs to decide routing, completion,
Resume, Unlock, or recovery.

Never share, guess, or replace another Origin session's Claim. Claim loss has
no takeover path in v0.1: diagnose read-only, cancel the old Run, confirm the
old Origin turn stopped, safely Unlock if required, then Bootstrap a new Run.
