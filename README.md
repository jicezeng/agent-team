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
- `uv` for the installation and development commands below
- Git
- tmux when any role has an External binding
- Codex CLI and/or Claude Code CLI for the configured External roles
- macOS or Linux on a local filesystem with `flock`, atomic same-directory
  rename, and `fsync`
- exactly one Git worktree root per Run; sparse checkout and Gitlinks are not
  supported in v0.1

## Install

Install Agent-Team separately for every OS account that will run it. The
Python package contains the CLI, Codex skill, and Claude Code plugin, but it
does not install or authenticate the Codex and Claude Code CLIs.

### Install on another machine from a wheel

Build a wheel on a machine that has this source checkout:

```bash
cd /path/to/agent-team
uv build --wheel
```

Copy `dist/agent_team-0.1.0-py3-none-any.whl` to the target macOS or Linux
machine, then run:

```bash
uv tool install --force /path/to/agent_team-0.1.0-py3-none-any.whl
agent-team install
agent-team doctor --workspace /path/to/worktree --json
```

The wheel is platform-independent, but the target machine must still satisfy
the requirements above. Configure and authenticate each harness CLI on that
machine before starting a Run.

### Install from a source checkout

If the target machine has a copy of this repository, install directly from
the checkout:

```bash
cd /path/to/agent-team
uv tool install --force .
agent-team install
agent-team doctor --workspace /path/to/worktree --json
```

Use `--force` to make upgrades and reinstalls deterministic. After upgrading
the Python package, run `agent-team install` again so the copied integrations
match the installed package.

### Development install

To run from the project environment without installing the CLI as a global
tool:

```bash
uv sync
uv run agent-team install
uv run agent-team doctor --workspace /path/to/worktree --json
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
workspace boundaries, state permissions, and any current Workspace owner. An
authentication result may be `unknown` when a harness cannot be checked
without an interactive or model call; confirm that harness separately before
using it.

Do not copy `.agent-team/` or the per-account fixed state directory to another
machine and try to resume a Run. Harness sessions, process identities, tmux
workers, and Workspace ownership are machine-local. Install Agent-Team on the
new machine and bootstrap a new Run there.

## Use from Codex

The recommended entry point is the installed `$agent-team` Codex skill. Start
a new Codex session in the Git worktree that the team should modify:

```bash
cd /path/to/worktree
agent-team doctor --workspace "$PWD" --json
codex
```

Then invoke the skill with a complete natural-language team request. For
example:

```text
$agent-team

Work in the current Git worktree with one Claude Code Developer and one
independent Codex Reviewer. Use resumable sessions for both roles.

The Developer should implement the requested change and run relevant tests.
The Reviewer is the sole completion authority and must report every P0-P3
finding to the Developer. The Developer must explicitly accept and fix, or
reject with evidence, every finding. After each fix, the same Reviewer session
must perform a complete re-review. Continue until there are no open P0-P3
findings, then return the final result to this Codex session.

Task: <describe the change here>

Limits: at most 12 role turns and 7200 seconds.
```

The skill preserves the request in `REQUEST.md`, generates a readable
`PROTOCOL.md`, checks the selected harness profiles, starts the Run, and
handles Origin handoffs until the Reviewer completes the team or a Block must
be shown to the user. Keep the originating Codex session open while the Run is
active. Use `status`, `watch`, or `attach` from another terminal if you want to
observe it.

## Manual CLI bootstrap

To operate without the Codex skill, first create two readable files outside
`.agent-team/`:

- `REQUEST.md`, preserving the original objective
- `PROTOCOL.md`, defining dynamic roles, routing, review loops, completion
  authority, context policy, assumptions, and safety limits

Create a Run from the exact Git worktree root:

```bash
agent-team init \
  --workspace /path/to/worktree \
  --request /path/to/REQUEST.md \
  --protocol /path/to/PROTOCOL.md \
  --role developer=claude-code:resume:default \
  --role reviewer=codex:resume:default \
  --initial-role developer \
  --origin-harness codex \
  --max-turns 12 \
  --max-wall-time-seconds 7200 \
  --audit-mode full \
  --trace-redaction standard \
  --max-trace-bytes 67108864 \
  --raw-retention redacted \
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

### Observability modes

Every new Run has an immutable observability policy:

- `--audit-mode standard` allows Origin-bound business roles. External Turns
  receive structured Harness tracing under the configured limits; Origin
  Turns expose only their formal input/output and workspace boundaries because
  the host does not export its internal tool stream.
- `--audit-mode full` requires every business role to be External. The Origin
  session remains the control plane only, every role Turn must produce a
  complete trace, and any raw or normalized capture truncation creates a
  technical Block instead of silently accepting an incomplete audit.
- `--trace-redaction standard` heuristically redacts common bearer tokens,
  API keys, passwords, private keys, and secret-bearing structured fields in
  normalized and retained raw Harness output. `none` is an explicit opt-out.
- `--max-trace-bytes` caps source bytes retained across stdout and stderr and
  separately caps normalized `trace.jsonl` bytes for each Turn. The minimum is
  1024 bytes; the default is 64 MiB.
- `--raw-retention redacted` rewrites the raw archive with heuristic secret
  substitutions; it does not remove every kind of private content. Tool
  arguments/results, prompts, code, and even provider-emitted
  thinking/reasoning may remain. `keep` retains the original raw stream;
  `delete` removes raw stdout/stderr after normalized trace creation. Full
  audit mode does not allow `delete`.
- `--require-rationale-evidence` makes the formal payload contract mandatory
  in standard mode. Full audit mode enables it automatically.

For a full-audit Run, every Handoff, Completion, and Agent Block payload must
contain non-empty sections with these exact headings:

```markdown
## Decision rationale

Explain the explicit decision and relevant tradeoffs.

## Evidence

List reproducible inspections, commands, test results, and artifact paths.
```

These sections capture an auditable explanation, not private hidden
chain-of-thought. Agent-Team records a Harness-provided reasoning summary only
when that Harness actually exposes one.

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
agent-team transcript [<run-id>] [--workspace <root>] \
  [--role <role-id>] [--turn <turn-id>] [--json]
agent-team tail [<run-id>] [--workspace <root>] \
  [--role <role-id>] [--turn <turn-id>] [--lines <n>] [--follow] [--jsonl]
agent-team attach <run-id> [--role <role-id>]
```

When Run ID is omitted, observation resolves only the current Workspace owner;
it never guesses the newest audit directory. Structured output is the stable
control surface. `status`, `diagnose`, and each `watch --jsonl` line share the
same derived snapshot, including `run_status`, `health`, active Turn, process
identity, session state, Block policy, evidence paths, and one technical
`recommended_action`.

`transcript` reconstructs every selected Turn's policy-filtered frozen input,
Harness prompt, normalized event stream, formal output, and per-Turn/run aggregate
event, tool, token, cost, and duration summaries. `tail` emits the latest
normalized events and can follow a live Run; `--role` and `--turn` provide
stable filters, while `--json`/`--jsonl` are the machine-readable interfaces.
Normalized event kinds include agent messages, tool calls/results, file
changes, usage, errors, and exposed reasoning summaries. Each event links back
to the source stdout/stderr sequence range.

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
inputs, Event payloads, Runtime snapshots, facts, normalized traces, retained
raw streams, and completion artifacts. A separate per-account fixed state
directory contains the durable Workspace owner and operation lock.

After an External Turn becomes quiescent, Agent-Team writes
`turns/<turn-id>/trace.jsonl` and `trace-manifest.json`. The manifest records
the capture/truncation counts, event/tool/usage summary, and SHA-256 plus byte
size for every retained trace artifact. Its SHA-256 is set once in
`runtime.json`; `status`, `diagnose`, transcript reads, and recovery validate
that anchor and report later artifact tampering as corruption.

Agent-Team does not modify `.gitignore` or `.git/info/exclude`. Never stage
`.agent-team/`; `doctor` warns when it is not covered by a user-managed ignore
rule. Files are private by default, but the local Run Store can contain
sensitive harness output and is not a secret manager. Standard redaction is
heuristic, not a guarantee. Authoritative Request, Protocol, input, LaunchSpec,
formal payload, and workspace artifacts remain byte-exact for integrity and
may contain secrets; avoid placing credentials in prompts or repositories.
Normalized traces omit private `thinking` and generic `reasoning` block
contents, but retained raw output is a different privacy boundary:
`--raw-retention redacted` only applies heuristic secret substitutions and may
still contain provider-emitted private text, while `keep` preserves the
original stream. There is no automatic TTL or purge command: retained data
lasts with the Run Store until the user removes that Run directory; `delete`
applies only to raw stdout/stderr after normalization.

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

The real two-Codex and mixed Claude Code/Codex validation materials are in
[`docs/validation`](docs/validation). The observability implementation's
mixed-Harness evidence is summarized in
[`observability-claude-codex-report.md`](docs/validation/observability-claude-codex-report.md).
These validations define a resumable Developer / Reviewer loop where every
P0–P3 finding must be accepted and fixed or rejected with evidence, followed
by a complete re-review.
