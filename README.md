# Agent-Team

Agent-Team v0.1 is a local runtime for temporary coding-agent teams described
in natural language. It gives one dynamic role at a time the execution token,
persists every formal handoff in an immutable journal, keeps external Codex or
Claude Code sessions resumable, and returns completion or a user-visible Block
to the current Origin session.

The normative contract is
[`agent-team_technical_design_v0.1.md`](agent-team_technical_design_v0.1.md).
Stage 1 deliberately keeps business workflow in `PROTOCOL.md`; the runtime
structures transport, ownership, process safety, session continuity, and
recovery rather than trying to parse reviewer verdicts from model prose.

## Requirements

- Python 3.11 or newer
- Git
- tmux when any role has an External binding
- Codex CLI and/or Claude Code CLI for the configured External roles
- macOS or Linux on a local filesystem with `flock`, atomic same-directory
  rename, and `fsync`
- exactly one Git worktree root per Run; sparse checkout and Gitlinks are not
  supported in v0.1

## Install

Install the CLI from this checkout and then install its harness integrations:

```bash
uv tool install .
agent-team install
agent-team doctor --json
```

For development, use the project environment instead:

```bash
uv sync
uv run agent-team install
uv run agent-team doctor --json
```

`agent-team install` replaces only Agent-Team's exact integration trees:

- Codex skill: `~/.codex/skills/agent-team`
- Claude Code plugin: the account's fixed Agent-Team state directory under
  `installed/claude-code-plugin`

On macOS the fixed state directory is
`~/Library/Application Support/agent-team`; on Linux it is
`~/.local/state/agent-team`. The location is derived from the current OS
account and is not configurable. `doctor` reports tool availability,
authentication when it can be determined without a model call, profile
fingerprints, Resume support, integration contents, filesystem capabilities,
workspace boundaries, state permissions, and any current Workspace owner.

## Bootstrap a Run

The bundled Codex skill and Claude Code plugin turn a one-shot team request
into two readable files outside `.agent-team/`:

- `REQUEST.md`, preserving the original objective
- `PROTOCOL.md`, defining dynamic roles, routing, review loops, completion
  authority, context policy, assumptions, and safety limits

Create a Run from the exact Git worktree root:

```bash
agent-team init \
  --workspace /path/to/worktree \
  --request /path/to/REQUEST.md \
  --protocol /path/to/PROTOCOL.md \
  --role developer=codex:resume:default \
  --role reviewer=claude-code:resume:default \
  --initial-role developer \
  --origin-harness codex \
  --max-turns 20 \
  --max-wall-time-seconds 7200 \
  --run-id at-example

agent-team start at-example --workspace /path/to/worktree
agent-team wait-origin \
  --run at-example \
  --workspace /path/to/worktree \
  --timeout 90
```

A role specification is one of:

```text
ROLE=origin
ROLE=codex:resume:default
ROLE=codex:fresh:default
ROLE=claude-code:resume:default
ROLE=claude-code:fresh:default
```

Role IDs match `[a-z][a-z0-9_-]{0,31}`. `resume` preserves a validated harness
session across that role's Turns; `fresh` creates a new session every Turn.
The `default` Launch Profile is a technical permission mapping, not a business
role. A protocol restriction such as “review only” remains a natural-language
role responsibility and is never inferred from the role name.

`init` atomically commits an UNSTARTED audit directory but acquires no
Workspace ownership and starts no process. `start` performs final capability
and Git-visible snapshot checks, acquires the durable Workspace owner, commits
the one Kickoff Event, and creates one tmux Worker window for every External
role. Repeating `start` converges through the same deterministic recovery path;
it does not create a second Kickoff.

## Formal role actions

External role prompts receive the current immutable input and use exactly one
terminal action:

```bash
agent-team handoff --to <role-id> --file <payload.md>
agent-team complete --file <payload.md>
agent-team block --file <payload.md>
```

The Worker injects `AGENT_TEAM_RUN_ID`, `AGENT_TEAM_ROLE_ID`,
`AGENT_TEAM_TURN_ID`, and `AGENT_TEAM_RUN_DIR`, so these commands do not accept
Run or Role arguments. The action copies and hashes its payload before
acceptance. Ordinary final text, tmux pane content, and log prose never move
the execution token.

An Origin-bound role uses the Claim-bearing `origin-*` commands. These are
normally driven by the integration skill rather than typed manually:

```bash
agent-team origin-context \
  --run <run-id> --event <event-id> --claim <claim>

agent-team origin-handoff \
  --run <run-id> --turn <turn-id> --claim <claim> \
  --from-role <role-id> --to <role-id> --file <payload.md>

agent-team origin-complete \
  --run <run-id> --turn <turn-id> --claim <claim> \
  --from-role <role-id> --file <payload.md>

agent-team origin-block \
  --run <run-id> --turn <turn-id> --claim <claim> \
  --from-role <role-id> --file <payload.md>
```

`origin-handoff` submits and waits in the same call. `origin-complete` and
`origin-block` leave the host Turn in an auditable `exited` phase; the next
user Agent turn calls `wait-origin` with the same Claim to confirm that the old
host Turn stopped and safely finalize it. v0.1 has no Claim takeover.

## Observe a Run

```bash
agent-team status [<run-id>] [--workspace <root>] [--json]
agent-team watch [<run-id>] [--workspace <root>] [--jsonl]
agent-team diagnose [<run-id>] [--workspace <root>] [--role <role-id>] [--json]
agent-team attach <run-id> [--role <role-id>]
```

When Run ID is omitted, observation resolves only the current Workspace owner;
it never guesses the newest audit directory. Structured output is the stable
control surface. `status`, `diagnose`, and each `watch --jsonl` line share the
same derived snapshot, including `run_status`, `health`, active Turn, process
identity, session state, Block policy, evidence paths, and one technical
`recommended_action`.

`attach` is read-only and pane output is diagnostic only. Neither pane text nor
raw logs participate in routing, completion, Resume, or recovery decisions.

## Blocks, Resume, and cancellation

Every Block must be returned to the user. Read-only diagnosis and deterministic
technical `recover` may run first, but a resumable Block remains Blocked until
a later, explicit user instruction is recorded:

```bash
agent-team origin-resume \
  --run <run-id> \
  --claim <management-claim> \
  --to <role-id> \
  --file <exact-user-instruction.md> \
  --wait-timeout 90
```

The generated Resume Event becomes the next Turn's direct `input.md`.
Limit/Profile Changed Blocks cannot Resume. A change to the original request,
protocol, roles, bindings, Workspace, Launch Profile, or safety limits also
requires cancelling the old Run and bootstrapping a new one.

Cancellation is an explicit management action and preserves the audit store:

```bash
agent-team cancel <run-id> --workspace <root>
```

## Recovery and Unlock

```bash
agent-team recover <run-id> --workspace <root>
agent-team unlock \
  --workspace <root> \
  --expect-run <run-id> \
  [--confirm-origin-stopped]
```

`recover` performs only conclusions uniquely supported by persisted evidence:
it may rebuild missing idle Workers, finish a Runtime after an already
committed Event, deliver a fully frozen normal-completion Outbox, or commit a
fixed technical Block. It never chooses a business route and never creates a
Resume Event.

`unlock` is the final escape hatch for an ownership record that cannot be
released through normal recovery. It requires the exact Run ID and refuses
while a Worker, Supervisor, Runner process group, or tmux session may still be
alive. `--confirm-origin-stopped` is required when an embedded Origin Turn
cannot be machine-proven stopped. Run `diagnose --json` first and do not use
Unlock to bypass an uncertain process identity.

## State and security boundaries

Each worktree contains a private `.agent-team/` State Root with immutable Run
inputs, Event payloads, Runtime snapshots, facts, raw streams, and completion
artifacts. A separate per-account fixed state directory contains the durable
Workspace owner and operation lock.

Agent-Team does not modify `.gitignore` or `.git/info/exclude`. Never stage
`.agent-team/`; `doctor` warns when it is not covered by a user-managed ignore
rule. Files are private by default, but the local Run Store can contain
sensitive harness output and is not a secret manager.

The Runner process group provides bounded local process cleanup, not container
isolation. The protocol forbids roles from launching daemons that escape it.
Workspace ownership prevents a second Agent-Team Run; it cannot prevent an IDE
or unrelated process from editing the same files. v0.1 therefore requires no
concurrent manual edits and records Git-visible facts at every business Turn
boundary.

## Development and verification

```bash
uv sync
uv run pytest
uv run python -m compileall -q src tests
uv build
```

The real two-Codex validation materials are in
[`docs/validation`](docs/validation). They define a resumable Developer /
Reviewer loop where every P0–P3 finding must be accepted and fixed or rejected
with evidence, followed by a complete re-review.
