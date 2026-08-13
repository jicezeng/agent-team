# Agent-Team user guide

This guide contains the installation and operating detail intentionally kept
out of the project [README](../README.md). Product scope is defined by the
[PRD](../agent-team_prd_v0.1.md); runtime and recovery behavior is governed by
the [technical design](../agent-team_technical_design_v0.1.md) and current
tests.

## Installation and upgrades

Agent-Team requires Python 3.11 or newer, `uv`, Git, and tmux when any role has
an External binding. Install and authenticate Codex CLI and/or Claude Code CLI
separately. Runs require macOS or Linux, a local filesystem with `flock`, atomic
same-directory rename and `fsync`, and exactly one normal Git worktree root.
Sparse checkout and Gitlinks are not supported in v0.1.

Install Agent-Team separately for each OS account that will run it.

### Hosted macOS package

```bash
curl -fsSL https://agentteam.zengjice.com:7001/install/mac.sh | bash
```

The installer verifies the wheel's pinned SHA-256 before replacing the tool,
refreshes both bundled integrations, and refuses to upgrade while an
Agent-Team Run owns a workspace.

### Wheel

Build the wheel from a source checkout:

```bash
uv build --wheel
```

Copy `dist/agent_team-0.1.3-py3-none-any.whl` to the target machine, then run:

```bash
uv tool install --force /path/to/agent_team-0.1.3-py3-none-any.whl
agent-team install
agent-team doctor --workspace /path/to/worktree --json
```

The wheel is platform-independent, but the target machine must still meet the
runtime requirements and have its Harness CLIs authenticated.

### Source checkout

```bash
git clone https://github.com/jicezeng/agent-team.git
cd agent-team
uv tool install --force .
agent-team install
agent-team doctor --workspace /path/to/worktree --json
```

### Development environment

```bash
uv sync --locked
uv run agent-team install
uv run agent-team doctor --workspace /path/to/worktree --json
```

`agent-team install` replaces only Agent-Team's integration trees:

- Codex skill: `~/.codex/skills/agent-team`
- Claude Code plugin: `installed/claude-code-plugin` under the fixed account
  state directory

The account state directory is `~/Library/Application Support/agent-team` on
macOS and `~/.local/state/agent-team` on Linux. It is not configurable.

After upgrading the package, run `agent-team install` again. Do not upgrade the
runtime or integrations during an active Run; complete or cancel and safely
recover it first. Do not copy `.agent-team/` or the fixed account state to
another machine to resume a Run: Harness Sessions, process identities, tmux
workers, and workspace ownership are machine-local.

## Start from Codex

The recommended entry point is the installed `$agent-team` skill:

```bash
cd /path/to/worktree
agent-team doctor --workspace "$PWD" --json
codex
```

Give the skill a complete natural-language team request. For example:

```text
$agent-team

Work in the current Git worktree with one Claude Code Developer and one
independent Codex Reviewer. Use resumable Sessions for both roles.

The Developer implements the requested change and runs relevant tests. The
Reviewer is the sole completion authority and reports every P0-P3 finding to
the Developer. The Developer must accept and fix, or reject with evidence,
every finding. After each fix, the same Reviewer Session performs a complete
re-review. Continue until no finding remains.

Task: <describe the change>
Limits: at most 12 role turns and 7200 seconds.
```

The skill preserves the request in `REQUEST.md`, generates `PROTOCOL.md`,
checks the selected Harness profiles, starts the Run, and follows Origin
handoffs. Keep the originating Codex Session open while the Run is active.

## Manual CLI bootstrap

Create `REQUEST.md` with the original objective and `PROTOCOL.md` with the
roles, routing, review loop, completion authority, context policy, assumptions,
and limits. Keep both outside `.agent-team/` and initialize from the exact Git
worktree root:

```bash
agent-team init \
  --workspace /path/to/worktree \
  --request /path/to/REQUEST.md \
  --protocol /path/to/PROTOCOL.md \
  --role developer=claude-code:resume \
  --role reviewer=codex:resume \
  --initial-role developer \
  --origin-harness codex \
  --max-turns 12 \
  --max-wall-time-seconds 7200 \
  --audit-mode full \
  --trace-redaction standard \
  --max-trace-bytes 67108864 \
  --raw-retention redacted \
  --run-id at-example

agent-team start at-example \
  --workspace /path/to/worktree \
  --confirm-full-access

agent-team wait-origin \
  --run at-example \
  --workspace /path/to/worktree \
  --timeout 90
```

Role specifications are:

```text
ROLE=origin
ROLE=codex:<resume|fresh>[:<profile>]
ROLE=claude-code:<resume|fresh>[:<profile>]
```

`resume` preserves the validated Harness Session across Turns; `fresh` creates
a Session for each Turn. External roles default to `interactive` launch and
`full-access` when those fields are omitted. Optional role-scoped settings are:

```text
--role-model ROLE=MODEL
--role-reasoning-effort ROLE=EFFORT
--role-fast ROLE
--role-launch-mode ROLE=<interactive|headless>
```

Omitted model and effort values inherit the relevant Harness default at
`init`, then Agent-Team freezes the requested result in `team.json`.
`--role-fast` is Codex-only. Launch mode, Profile, model, effort, and fast mode
cannot change after Kickoff.

Before the first interactive Claude Code Run in a worktree, establish Claude's
own workspace trust in a normal terminal:

```bash
cd /path/to/worktree
claude
# Accept “Yes, I trust this folder”, then exit Claude Code.
```

Agent-Team never edits Claude's trust database or simulates this answer. If
trust is missing, `start` fails before Kickoff with
`HARNESS_WORKSPACE_TRUST_REQUIRED`; establish trust and retry the same
UNSTARTED Run. Headless Claude roles do not require this TUI preflight.

## Permission profiles

| Profile | Codex | Claude Code |
| --- | --- | --- |
| `default` | Workspace write, scratch paths, no command network, no approval prompts | `acceptEdits`, OS workspace sandbox, internal scratch path, no fallback |
| `trusted-workspace` | Same filesystem boundary with command network | `acceptEdits`, same OS workspace sandbox, no fallback |
| `full-access` | `danger-full-access`, no approval prompts | `bypassPermissions`, Claude sandbox disabled |

Omitting a Profile selects `full-access` (YOLO). It removes the Harness host
filesystem boundary, opens command network access, and disables per-command
approval prompts. Before the first Kickoff of a new Run containing such a
role, the Skill must obtain one explicit user confirmation and `start` requires
`--confirm-full-access`. The immutable Kickoff records that confirmation, so
it is not requested again during the same Run. The CLI flag asserts that
upstream confirmation; it does not prompt on stdin or manufacture consent.

Use `full-access` only on a machine or VM whose files, credentials, and network
may be exposed to the Agent. `default` and `trusted-workspace` must be selected
explicitly when host containment is required. Claude uses `acceptEdits` for
both contained Profiles because its OS sandbox constrains Bash and children,
while built-in Edit/Write tools still depend on the permission system.

Agent-Team freezes the supplied mapping and `launch_profile_sha256`, excludes
mutable user permission settings, and sets Codex `features.hooks=false`.
Managed administrator policy remains higher authority: it can reject a launch,
force managed hooks, add paths or side effects, merge Claude sandbox arrays,
or override scalars. `doctor` reports Agent-Team's mapping but cannot prove the
final cloud-delivered or Managed policy. Inspect administrator configuration
and Claude `/status` and `/permissions`, or use an unmanaged VM, when the
boundary is security-critical.

## Runtime lifecycle and formal actions

`init` commits an UNSTARTED audit directory but starts no process and acquires
no workspace ownership. `start` performs final checks, records the single
Kickoff, acquires durable ownership, and creates one tmux Worker window for
each External role. Repeated `start` converges through deterministic recovery;
it does not create a second Kickoff.

Each External Turn receives these environment variables:

```text
AGENT_TEAM_RUN_ID
AGENT_TEAM_ROLE_ID
AGENT_TEAM_TURN_ID
AGENT_TEAM_RUN_DIR
AGENT_TEAM_TURN_DIR
AGENT_TEAM_CLI
```

It must finish with exactly one formal action:

```bash
agent-team handoff --to <role-id> --file <payload.md>
agent-team complete --file <payload.md>
agent-team block --file <payload.md>
```

The action copies and hashes its payload before acceptance. Ordinary model
text, tmux Pane content, logs, and manual TUI input never move the execution
token.

Origin-bound roles normally use these commands through the integration skill:

```bash
agent-team origin-context \
  --run <run-id> --event <event-id> --claim=<claim>

agent-team origin-handoff \
  --run <run-id> --turn <turn-id> --claim=<claim> \
  --from-role <role-id> --to <role-id> --file <payload.md>

agent-team origin-complete \
  --run <run-id> --turn <turn-id> --claim=<claim> \
  --from-role <role-id> --file <payload.md>

agent-team origin-block \
  --run <run-id> --turn <turn-id> --claim=<claim> \
  --from-role <role-id> --file <payload.md>
```

Always pass opaque Claims as `--claim=<value>` so legacy values beginning with
`-` cannot be parsed as options. v0.1 has no Claim takeover.

## Observation

```bash
agent-team status [<run-id>] [--workspace <root>] [--json]
agent-team watch [<run-id>] [--workspace <root>] [--jsonl]
agent-team diagnose [<run-id>] [--workspace <root>] [--role <role-id>] [--json]
agent-team transcript [<run-id>] [--workspace <root>] \
  [--role <role-id>] [--turn <turn-id>] [--json]
agent-team tail [<run-id>] [--workspace <root>] \
  [--role <role-id>] [--turn <turn-id>] [--lines <n>] [--follow] [--jsonl]
agent-team attach [<run-id>] [--workspace <root>] [--role <role-id>]
```

When the Run ID is omitted, these commands resolve only the current workspace
owner. `status`, `diagnose`, and `watch --jsonl` expose the same derived
snapshot. `transcript` reconstructs selected Turn inputs, normalized events,
formal outputs, and usage summaries. `tail` follows normalized events.

`attach` opens a read-only tmux client. It shows the native TUI for an active
interactive role and Worker diagnostics for a headless role. Detach with
`Ctrl-b d`. A separately opened writable tmux client may relay raw keyboard
input, but that input is never a formal Agent-Team action.

## Audit policy

Each Run freezes its observability policy:

- `--audit-mode standard` permits Origin-bound business roles and records the
  trace detail each Harness exposes.
- `--audit-mode full` requires every business role to be External and creates
  a technical Block if required capture is incomplete.
- `--trace-redaction standard` heuristically redacts common secrets from
  normalized and retained raw Harness output; it is not a guarantee.
- `--max-trace-bytes` independently caps source and normalized bytes per Turn.
- `--raw-retention redacted|keep|delete` controls retained raw output. Full
  audit does not allow `delete`.
- `--require-rationale-evidence` requires the formal payload sections below;
  Full Audit enables it automatically.

```markdown
## Decision rationale

Explain the decision and tradeoffs.

## Evidence

List reproducible inspections, commands, results, and artifact paths.
```

These sections are an auditable explanation, not hidden chain-of-thought.
Agent-Team records a reasoning summary only when the Harness exposes one.

## Block, Resume, cancellation, and recovery

A Block must be returned to the user. Read-only diagnosis and deterministic
technical recovery may run first, but a resumable Block remains Blocked until
a later user instruction is recorded:

```bash
agent-team origin-resume \
  --run <run-id> \
  --claim=<management-claim> \
  --to <role-id> \
  --file <exact-user-instruction.md> \
  --wait-timeout 90
```

Limit/Profile Changed Blocks cannot Resume. Changes to the Request, Protocol,
roles, bindings, workspace, launch configuration, or safety limits require
cancelling the old Run and creating a new one.

```bash
agent-team diagnose <run-id> --workspace <root> --json
agent-team recover <run-id> --workspace <root>
agent-team cancel <run-id> --workspace <root>
agent-team unlock \
  --workspace <root> \
  --expect-run <run-id> \
  [--confirm-origin-stopped]
```

`recover` applies only conclusions uniquely supported by persisted evidence;
it never chooses a business route or creates a Resume Event. `unlock` is the
last escape hatch for ownership that normal recovery cannot release. It
refuses while a Worker, Supervisor, Runner process group, or tmux Session may
still be alive. Run `diagnose --json` first.

## State, privacy, and security

Each worktree contains `.agent-team/` with immutable inputs, Events, Runtime
snapshots, traces, retained raw streams, and completion artifacts. A separate
fixed account directory contains workspace ownership, operation locks, and
private interactive Codex Homes.

Agent-Team does not modify `.gitignore` or `.git/info/exclude`. Never stage
`.agent-team/`; `doctor` warns if a user-managed ignore rule does not cover it.
The Run Store may contain sensitive Harness output and is not a secret manager.
Avoid credentials in prompts and repositories.

Normalized traces omit private `thinking` and generic `reasoning` block
contents, but raw output has a different privacy boundary. Redaction is
heuristic, `keep` preserves the original stream, and there is no automatic TTL
or purge command. Retained data lasts until the user removes the Run directory.

Runner process groups provide bounded cleanup, not container isolation.
Workspace ownership prevents a second Agent-Team Run, not an IDE or unrelated
process from editing the same files. Avoid concurrent manual edits.

## Verification evidence

Real Codex and mixed Claude Code/Codex validation reports are indexed in
[`docs/validation`](validation/README.md). Reports are historical evidence;
the technical design and current tests define the latest contract.
