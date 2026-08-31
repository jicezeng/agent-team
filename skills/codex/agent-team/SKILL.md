---
name: agent-team
description: Create and operate temporary, natural-language-defined coding-agent teams across Codex, Claude Code, OpenCode, and DeepSeek Harness. Use when a user requests multiple agents or dynamic roles, cross-harness collaboration, explicit handoffs, iterative developer/reviewer or QA loops, resumable role sessions, or completion returned to the current Origin session.
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
4. End the Turn with exactly one shell invocation of the absolute
   `$AGENT_TEAM_CLI` command shown in the prompt:
   `handoff --to ... --file ...`, `complete --file ...`, or
   `block --file ...`.
5. Stop business work after that CLI command succeeds.

## Bootstrap

1. Preserve the user's exact request in a local `REQUEST.md`.
2. Extract dynamic roles, Origin/External bindings, external adapters, session
   policies, initial role, handoff/loop rules, completion authority, final
   delivery, safety limits, and any model, reasoning-effort, Codex model
   provider, or Codex fast-mode choices the user explicitly made.
   When the task needs Chrome or another session-owned browser-control
   capability, designate exactly one capable External role as the browser
   owner and give it the `resume` Session policy. Freeze in `PROTOCOL.md` that
   only this role operates browser tabs and every other role routes browser
   requests to it by Handoff. Tab persistence or Handoff markers do not
   transfer ownership to another role or Session.
3. Reject true parallel fan-out/join, multiple workspaces, non-Git roots,
   missing completion conditions, unavailable Harnesses, or dangerous
   ambiguity. Do not silently serialize a requested parallel topology.
4. Record every inference under `Assumptions made during bootstrap`. Keep
   business conditions in natural-language `PROTOCOL.md`; do not invent a
   workflow DSL or machine-parse reviewer verdicts. If the user explicitly
   requires hard role-to-role boundaries, repeat `--allow-handoff FROM=TO` for
   every permitted role-selected edge; supplying any edge closes the allowlist
   and gives unlisted sources no outgoing edge. Omit it when routing should stay
   dynamic. If a role is explicitly forbidden to change candidate files, add
   `--read-only-role ROLE`. This is a Git-visible Turn-boundary guard, not a
   business verdict, OS sandbox, or reason to infer a restricted Launch Profile.
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
   OpenCode has no OS Bash sandbox, so its restricted Profiles keep arbitrary
   Bash denied, allow only worktree file tools plus exact formal Agent-Team
   commands, and let `trusted-workspace` add only built-in web tools.
   DeepSeek Harness restricted Profiles constrain file writes to the worktree
   but inherit host reads, process execution, environment credentials, and
   network access; `default` and `trusted-workspace` are identical in v0.1.
   `full-access` removes the host sandbox and is appropriate only on a
   controlled machine or VM. Never infer elevated access from a role name, a
   request to run tests, or a natural-language `read-only` restriction; the
   latter may select the separate `--read-only-role` guard but does not select
   a Launch Profile. Never treat mutable local Harness settings as the selected Profile. Doctor
   shows the Agent-Team-supplied mapping, not the final effect of
   administrator-managed Harness policy. On a managed host, use a
   workspace-contained Claude Profile only after verifying that policy adds no
   host write roots or sandbox exclusions. Agent-Team disables non-managed
   Codex hooks, but trusted Workspace project configuration and extensions
   remain part of that Workspace's trust boundary. Also verify that Codex
   requirements do not force managed hooks or add log effects; an incompatible
   requirement can reject the selected Profile and must not be bypassed.
6. Add `--role-model <role>=<model>`,
   `--role-reasoning-effort <role>=<effort>`,
   `--role-model-provider <role>=<provider>`, or `--role-fast <role>` only for
   choices the user explicitly made. Do not infer these choices from role names
   or task complexity. Omit each unspecified option so `init` snapshots that
   Harness user's default for the field. `--role-model-provider` applies only
   to Codex and Claude Code; `--role-fast` is Codex-only. A selected custom Codex Provider must already
   exist in that user's Codex `config.toml`; pass only its Provider ID. Never
   put an endpoint credential, token, header value, or environment value in the
   Agent-Team CLI, Request, or Protocol. Agent-Team freezes the safe Provider
   structure and referenced environment variable names itself. For Claude
   Code, pass only `anthropic`, `bedrock`, `vertex`, `foundry`, or `gateway`;
   the corresponding native Claude environment must already be present in the
   shell that runs `init` and `start`. Omit an unspecified Claude Route so
   `init` detects and freezes that environment.
   OpenCode Model IDs must resolve to `provider/model`; its reasoning-effort
   value is a provider-specific Variant. Agent-Team completes an unqualified
   user default only when exactly one configured Provider declares it, then
   verifies the qualified Model against the effective OpenCode catalog. Supply
   an explicit OpenCode Model when the default is absent or ambiguous.
   Explicit DeepSeek Harness Model IDs also use `provider/model`; explicit
   effort is `off`, `high`, or `max`. When either is omitted, leave it
   unspecified so the private DSH Profile uses DSH's native default-model
   services. Never invent a DSH model environment variable or Agent-Team
   fallback. Launch mode is separate: every new External role defaults
   to native `interactive` PTY execution in its tmux Pane. For Codex, Claude
   Code, or OpenCode, add
   `--role-launch-mode <role>=headless` only when the user explicitly requests
   headless/structured-stream execution; an explicit `interactive` value may
   be recorded but is normally redundant. DeepSeek Harness is interactive-only
   and must never be assigned `headless`. Before starting any interactive
   Claude Code role, require the user to have opened `claude` once in the exact
   Workspace (or a trusted parent), accepted its workspace-trust prompt, and
   exited. Never edit Claude's user trust state or accept the prompt with
   `send-keys`. `HARNESS_WORKSPACE_TRUST_REQUIRED` occurs before Kickoff, so
   after that one-time confirmation retry the same UNSTARTED Run; headless
   Claude roles do not require this preflight. This is Claude's independent
   worktree prerequisite, not another Run permission decision. For
   `full-access`, the Adapter reuses the confirmation from step 5 to suppress
   Claude's separate dangerous-mode prompt.
   When a DSH role must consume a Workspace bundle produced during the Run,
   add `--role-dsh-plugin <role>=<workspace-package-directory>`. The directory
   may be absent at `init`, allowing another role to create it from scratch, but
   it must stay inside the Workspace and the candidate-bound role must use the
   `fresh` Session policy. Agent-Team copies and freezes its current contents in
   a generation-private DSH Profile on every route to the role. A source finding
   follows the natural-language Protocol; the next route to the candidate-bound
   role receives a new immutable artifact generation while prior generations
   remain preserved. Do not Block merely because an installed generation
   contains a source defect. Route preflight happens before an Outbox or Handoff Event is
   accepted. If the CLI returns `ROUTE_PREFLIGHT_REJECTED`, the current Turn
   still owns the token: treat the reported artifact problem as a finding and
   submit a new payload choosing the next Protocol-valid role. A failed CLI call
   is not the Turn's formal action. Frozen Profile drift or a change after Outbox
   staging still fails closed. If the frozen candidate reaches the real DSH
   loader but the Harness exits before the candidate-bound Fresh Session is durably
   initialized, Agent-Team does not interpret loader prose or duplicate DSH
   plugin rules. It consumes that failed generation and commits an
   `Agent-Team Candidate Activation Finding` Handoff, structurally marked
   `system_handoff_reason=candidate_activation_failed`, back to the sending role.
   That role must inspect the preserved trace and choose the next Protocol-valid
   action, or Block only when the evidence proves an infrastructure failure.
   Tell the candidate-bound role to call the installed tool directly. Never ask
   it to launch a nested DSH or manage tmux itself.
   A supported Harness may structurally report that a model Turn exhausted its
   output budget. Agent-Team can then create a counted same-role continuation
   only for a durably initialized Session and only before any Block exists. A
   `resume` role reuses that Session; a `fresh` role receives a new generation
   and reconstructs from durable inputs. Ordinary crashes, permissions, audit
   truncation, exhausted limits, and existing Outboxes still Block. Configured
   Turn and wall-time limits bound repeated continuations; Git mutation is not a
   progress signal. Do
   not encode task-specific recovery commands or ask the Origin to Resume this
   pre-Block system Handoff. Budget enough Turns for such continuations when a
   selected model may need multiple long responses.
7. Choose and record the observability policy. Use `full` only when every
   business role is External and the Origin is control-plane only; otherwise
   use `standard` and disclose that Origin role internals are not captured.
   Keep standard redaction and redacted raw retention unless the user
   explicitly requests another privacy tradeoff. New Runs that enable the
   audited payload contract require four concrete sections in every formal role
   payload: `## Decision rationale`, `## Acceptance coverage`,
   `## Open findings`, and `## Evidence`. Require the
   Completion Authority to map every material Request and Protocol condition to
   current evidence. Its Completion must contain exactly one `## Open findings`
   section whose only content is `None`; the CLI rejects any other content.
   Historical Runs retain their frozen older payload contract.
8. Resolve `agent-team` once to its canonical absolute executable path and
   retain that literal path for the entire Origin loop. Substitute it for
   `<absolute-agent-team-cli>` in every command below; do not re-resolve it
   from `PATH` between turns.
   In a DeepSeek Harness Origin, use the installed `agent_team_cli` tool for
   every command below, passing the tokens after the executable as its `args`
   array. Do not invoke Agent-Team through DSH Bash: that environment
   intentionally scrubs provider credentials needed by DSH External roles.
   The trusted tool resolves the executable once and forwards the credential
   in-process without returning it. If that tool is unavailable and the Run
   contains a DSH External role, stop before `init` and ask the user to activate
   `$DSH_HOME/plugins/agent-team-origin` in the current DSH Profile.
   Write Request and Protocol outside `.agent-team`,
   select the explicit Origin metadata from the managed shell, add the workflow
   flags chosen in step 4 to the `init` invocation, then run:

```bash
if [ "${DSH_SHELL:-}" = "1" ]; then
  origin_harness=deepseek-harness
else
  origin_harness=codex
fi
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
  --origin-harness "$origin_harness"
"<absolute-agent-team-cli>" start <run-id> --confirm-full-access
```

`DSH_SHELL=1` selects `deepseek-harness`; every other value selects `codex`.
This branch records Origin metadata only and grants no permission.

Role specifications stay immutable for the Run, while External Worker
processes are lazy: Agent-Team creates only the currently routed role and
retires it after the token moves. Do not pre-launch roles or emulate dynamic
Agents with nested Harness processes.

Omit `--confirm-full-access` when every External role explicitly uses a
restricted Profile. The flag asserts that the user confirmation required in
step 5 has already occurred; never pass it speculatively.

9. Save the returned Run ID and immediately call
   `"<absolute-agent-team-cli>" wait-origin --run <run-id> --timeout 90`.

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
  deliver an evidence-backed summary rather than forwarding one sentence. A
  terminal Journal state proves only that the authority submitted Completion;
  if its coverage is incomplete or contradicted by direct evidence, report the
  validation failure and do not present the business objective as achieved.
- On `TEAM_BLOCKED`, show the Block to the user and end the Agent turn.
  Read-only diagnosis or deterministic `recover` may precede the response, but
  never Resume in the same turn.
- A Journal Handoff with `continuation_reason=output_limit` is a pre-Block,
  same-role system continuation, not a user-authorized Resume. Continue the
  normal wait loop; if any Block is later committed, the rule above applies
  without exception.
- Resume only after a new, explicit user instruction:

```bash
"<absolute-agent-team-cli>" origin-resume \
  --run <run-id> --claim=<management-claim> \
  --to <role-id> --file <exact-user-instruction.md> \
  --wait-timeout 90
```

Limit/Profile Changed Blocks and changes to the Request, Protocol, roles,
bindings, workspace, launch mode, profile, model, model provider, reasoning
effort, fast mode, or limits require Cancel plus a new Run.

## Structured control

Use `status --json` and `diagnose --json`; act on the structured envelope,
`health`, `recommended_action`, and evidence paths. Use `transcript --json`
and `tail --jsonl --role <role>` only for audit and observation. Never parse
Pane text, human-readable Status, Harness prose, or logs to decide routing,
completion, Resume, Unlock, or recovery.

`"<absolute-agent-team-cli>" attach [<run-id>] --role <role>` is a read-only
live view. Omit the
Run ID inside its actively owned Workspace, or provide it explicitly. An
interactive role shows its native Harness TUI. An operator may explicitly use
a writable tmux client for manual TUI input, which the Supervisor relays as raw
terminal bytes, but neither the Pane nor that input is a formal Agent-Team
action channel; formal CLI Outbox actions and the Journal remain authoritative.

Never share, guess, or replace another Origin session's Claim. Claim loss has
no takeover path in v0.1: diagnose read-only, cancel the old Run, confirm the
old Origin turn stopped, safely Unlock if required, then Bootstrap a new Run.
