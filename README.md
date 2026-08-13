# Agent-Team

[![CI](https://github.com/jicezeng/agent-team/actions/workflows/ci.yml/badge.svg)](https://github.com/jicezeng/agent-team/actions/workflows/ci.yml)

Agent-Team is a local runtime for temporary coding-agent teams described in
natural language. It coordinates Codex and Claude Code roles, gives one role at
a time the execution token, preserves formal handoffs and audit evidence, and
keeps role sessions resumable across review loops.

External roles use their native Harness TUI inside tmux. Agent-Team supervises
the processes and durable event journal; it does not route work by scraping
terminal text or by automating `tmux send-keys`.

## Highlights

- Define roles, responsibilities, routing, and completion rules per task.
- Mix Codex and Claude Code while preserving each role's Session.
- Observe interactive Turns through tmux and structured trace commands.
- Fail closed on ambiguous process, permission, or recovery state.
- Resume a Block only after a new, explicit user instruction.

## Requirements

- macOS or Linux, Python 3.11+, `uv`, Git, and tmux
- Codex CLI and/or Claude Code CLI, already installed and authenticated
- One normal Git worktree root per Run

## Install

On macOS, install or upgrade the hosted package:

```bash
curl -fsSL https://agentteam.zengjice.com:7001/install/mac.sh | bash
```

Or install from a source checkout on macOS or Linux:

```bash
git clone https://github.com/jicezeng/agent-team.git
cd agent-team
uv tool install --force .
agent-team install
```

Then verify the target worktree and installed Harnesses:

```bash
agent-team doctor --workspace /path/to/worktree --json
```

Agent-Team installs its bundled Codex skill and Claude Code plugin, but it does
not install or authenticate either Harness CLI. Wheel, development, upgrade,
and integration-location instructions are in the
[user guide](docs/user-guide.md#installation-and-upgrades).

## Quick start

The recommended entry point is the installed `$agent-team` Codex skill. Open
Codex in the Git worktree the team should modify:

```bash
cd /path/to/worktree
agent-team doctor --workspace "$PWD" --json
codex
```

Then describe the team and task:

```text
$agent-team

Use one Claude Code Developer and one independent Codex Reviewer. Preserve
both Sessions. The Developer implements and tests the change. The Reviewer is
the completion authority and sends every P0-P3 finding back to the Developer.
Repeat the fix and full-review loop until no finding remains.

Task: <describe the change>
Limits: at most 12 role turns and 7200 seconds.
```

The skill writes the immutable Request and Protocol, starts the Run, follows
handoffs, and returns either Completion or a user-visible Block to the Origin
session.

New External roles default to `full-access` (YOLO), which disables the Harness
host sandbox, opens command network access, and suppresses per-command approval
prompts. The skill must obtain explicit confirmation once for each new Run and
passes `--confirm-full-access` only after that confirmation. Choose the
restricted `default` or `trusted-workspace` Profile explicitly when host
containment is required.

| Profile | Filesystem | Command network |
| --- | --- | --- |
| `default` | Workspace-contained | Disabled |
| `trusted-workspace` | Workspace-contained | Enabled |
| `full-access` | Unrestricted host access | Enabled |

Agent-Team freezes its requested mapping in `launch_profile_sha256` and sets
Codex `features.hooks=false`, but Managed Harness policy can still change or
reject the effective configuration. Inspect `doctor` output and administrator
policy when a permission boundary matters.

## Observe and manage a Run

Run these from the owned worktree; the Run ID is optional for active-Run
observation commands:

| Command | Purpose |
| --- | --- |
| `agent-team status` | Current Run, active role, health, and next action |
| `agent-team watch` | Follow derived Run snapshots |
| `agent-team attach [--role <role>]` | Open the active tmux view read-only |
| `agent-team transcript` | Reconstruct Turn inputs, events, and outputs |
| `agent-team tail` | Read or follow normalized trace events |
| `agent-team diagnose` | Inspect failures and recovery evidence |
| `agent-team recover <run-id>` | Apply deterministic technical recovery |
| `agent-team cancel <run-id>` | Stop the Run while retaining its audit store |

Detach from `attach` with tmux's `Ctrl-b d`. Formal Handoff, Completion, Block,
and Resume decisions always go through Agent-Team's validated journal, never
through Pane text or manual TUI input.

See the [user guide](docs/user-guide.md) for manual CLI bootstrap, role options,
Claude workspace trust, observability modes, formal actions, Block/Resume,
recovery, Unlock, and data-retention boundaries.

## Documentation

- [User guide](docs/user-guide.md): installation and operations
- [Product requirements](agent-team_prd_v0.1.md): scope and acceptance criteria
- [Technical design](agent-team_technical_design_v0.1.md): normative runtime
  and recovery contract
- [Validation evidence](docs/validation/README.md): retained real-run reports

## Development

```bash
uv sync --locked
uv run pytest
uv run ruff check --select F src tests
uv run python -m compileall -q src tests
uv build
```
