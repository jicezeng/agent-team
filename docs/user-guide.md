# Agent-Team user guide

This guide contains the installation and operating detail intentionally kept
out of the project [README](../README.md). Product scope is defined by the
[PRD](../agent-team_prd_v0.1.md); runtime and recovery behavior is governed by
the [technical design](../agent-team_technical_design_v0.1.md) and current
tests.

## Installation and upgrades

Agent-Team itself requires Python 3.11 or newer, `uv`, Git, and tmux. Installation
does not require or probe Codex, Claude Code, OpenCode, DeepSeek Harness, or
their credentials. Install and authenticate only the Harness CLIs selected by a
team. A DeepSeek Harness External role additionally requires Node.js, pnpm, and
`DEEPSEEK_API_KEY`; Agent-Team provisions its pinned DSH runtime on first use.
Install a user-facing `dsh` separately only when DSH itself will be the Origin.
Runs require macOS or Linux, a local filesystem with `flock`, atomic
same-directory rename and `fsync`, and exactly one normal Git worktree root.
Sparse checkout and Gitlinks are not supported in v0.1.

Install Agent-Team separately for each OS account that will run it.

### Wheel

Build the wheel from a source checkout:

```bash
uv build --wheel
```

Copy `dist/agent_team-0.1.6-py3-none-any.whl` to the target machine, then run:

```bash
uv tool install --force /path/to/agent_team-0.1.6-py3-none-any.whl
agent-team install
agent-team doctor --workspace /path/to/worktree --json
```

The wheel is platform-independent, but the target machine must still meet the
runtime requirements and expose the credentials required by its selected roles.

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

`agent-team install` installs or replaces only Agent-Team-owned integrations:

- Codex skill: `~/.codex/skills/agent-team`
- Claude Code plugin: `installed/claude-code-plugin` under the fixed account
  state directory
- OpenCode skill: `~/.config/opencode/skills/agent-team`
- DeepSeek Harness skill: `$DSH_HOME/skills/agent-team`, defaulting to
  `~/.dsh/skills/agent-team`
- DeepSeek Harness Origin bundle: `$DSH_HOME/plugins/agent-team-origin`

These integration copies do not require the corresponding Harness executables.
When a team first selects a DSH External role, Agent-Team uses pnpm to install
the pinned managed DeepSeek Harness `0.1.0-rc.6` under
`installed/deepseek-harness-runtime` in the fixed account state directory. It
is reused after its version and integrity are verified, is not added to `PATH`,
and does not reuse a user's DSH profiles. For each DSH External role, Agent-Team
also creates a private Run/Role `DSH_HOME`, installs its bundled minimal
interactive TUI there, and uses environment credentials such as
`DEEPSEEK_API_KEY`. That private TUI is the External Adapter surface; a
separately installed `dsh` remains the Origin surface.

Agent-Team does not modify an existing DSH Profile. When a DSH Origin must
control a Run that itself contains DSH External roles, activate the installed
trusted control-plane bundle in the Profile you use:

```bash
dsh plugin --profile headless add ~/.dsh/plugins/agent-team-origin
```

Use the matching absolute `$DSH_HOME/plugins/agent-team-origin` path when
`DSH_HOME` is customized. The bundle contributes one `agent_team_cli` tool. It
runs only the fixed Agent-Team executable without a shell and resolves
`DEEPSEEK_API_KEY` through DSH's in-process credential service, so the provider
credential reaches DSH External Workers without becoming visible to the model
or its Bash environment. Re-run the profile add after replacing the bundle if
that Profile's package manager copied rather than linked the local package.

For DeepSeek Harness, unset or blank `DSH_HOME` follows the current `$HOME`.
An explicit value must resolve to an absolute path; only `~` and `~/...` expand
to the current user, while `~user` and relative values are rejected. If a DSH
composition sets `dshHome`, pass the same absolute value as `DSH_HOME` to both
`agent-team install` and `agent-team doctor`. Deployments using
`customSkillDirs` or `includeDefaultRoots: false` must install and verify the
Skill themselves because those provider-specific overrides are outside the
automatic installation contract.

The account state directory is `~/Library/Application Support/agent-team` on
macOS and `~/.local/state/agent-team` on Linux. It is not configurable.

After upgrading the package, run `agent-team install` again. Do not replace the
integrations during an active Run; complete or cancel and safely finalize it
first. `install` refuses while any Workspace Owner exists, including
a Blocked Run or a terminal Run whose Origin exit is not finalized. Do not copy
`.agent-team/` or the fixed account state to
another machine to resume a Run: Harness Sessions, process identities, tmux
workers, and workspace ownership are machine-local.

## Start from Codex, OpenCode, or DeepSeek Harness

The recommended entry point is the installed Agent-Team Skill:

```bash
cd /path/to/worktree
agent-team doctor --workspace "$PWD" --json
codex
# or: opencode
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
handoffs. Keep the originating Session open while the Run is active. In
OpenCode, ask it to use the `agent-team` Skill; OpenCode discovers the installed
Skill through its native `skill` tool.

DeepSeek Harness uses the same Skill source as Codex. From the target worktree,
explicitly invoke `/agent-team` in a DSH task, for example:

```bash
dsh --profile headless '/agent-team
Use one restricted Codex role to inspect this worktree and return a concise
evidence-backed result. Limits: at most 2 role turns and 900 seconds.'
```

DSH can also be an External role. Its Adapter always runs the bundled
interactive TUI in tmux, persists the native DSH Session in a private Run/Role
home, and resumes that same Session on later Turns. A real DSH Origin Skill load
is still the authority for which resource root won; `doctor` checks that the
installed Skill, managed runtime, TUI asset, credentials, and Profile mapping
required for the selected direction are available.
For a DSH-Origin → DSH-External team, the shared Skill uses `agent_team_cli`
for every Agent-Team control action; ordinary DSH Bash intentionally scrubs
credential-shaped environment variables and is not a valid substitute.

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
ROLE=opencode:<resume|fresh>[:<profile>]
ROLE=deepseek-harness:<resume|fresh>[:<profile>]
```

`resume` preserves the validated Harness Session across Turns; `fresh` creates
a Session for each Turn. External roles default to `interactive` launch and
`full-access` when those fields are omitted. Optional role-scoped settings are:

```text
--role-model ROLE=MODEL
--role-model-provider ROLE=PROVIDER
--role-reasoning-effort ROLE=EFFORT
--role-fast ROLE
--role-launch-mode ROLE=<interactive|headless>
--role-dsh-plugin ROLE=<workspace-package-directory>
```

Omitted model, Codex Provider, and effort values inherit the relevant Harness
default at `init`, then Agent-Team freezes the requested result in `team.json`.
`--role-model-provider` is Codex-only and accepts a Provider ID already defined
in the user's Codex `config.toml`; it never accepts a URL or credential.
OpenCode models must resolve to `provider/model`; its effort value is passed as the
provider-specific model Variant. If the effective OpenCode default is absent
or unqualified, supply `--role-model ROLE=provider/model` explicitly. The same
frozen OpenCode model is used for its primary agent and lightweight title
generation so a custom endpoint never receives an unrelated catalog model.
For interactive Codex roles, the private `CODEX_HOME` model-availability NUX
table is preseeded for the frozen model at Codex's terminal shown-count (`4`).
This suppresses native tooltip bookkeeping from rewriting the managed
`config.toml`; a different model, count, Workspace trust entry, or any other
config drift still fails closed.
DSH models also use `provider/model`; its default is
`deepseek-official/deepseek-v4-flash`, and effort is `off`, `high`, or `max`.
`--role-fast` is Codex-only. DSH External roles support only `interactive`;
requesting `headless` fails before Kickoff. Launch mode, Profile, model, Codex
Provider, effort, and fast mode cannot change after Kickoff.

For a custom Codex Provider, Agent-Team freezes only its safe structural
definition: display name, HTTP(S) base URL, Responses wire API, retry/timeout
and capability flags, plus `env_key` and `env_http_headers` environment
variable names. It rejects literal bearer tokens, static headers/query
parameters, executable auth commands, unsupported fields, and URLs containing
credentials. The referenced variables must be non-empty when the Run starts;
only those names and their current values are bridged into the role's tmux
Worker. Values are never written to `team.json`, a LaunchSpec, Journal, or
trace. The built-in `openai` Provider continues to require Codex login; a
custom environment-authenticated Provider does not. Start and Resume both
receive the same frozen `model_provider` and Provider definition through
explicit high-priority Codex config overrides.

For example, define the Provider locally without embedding its key:

```toml
# ~/.codex/config.toml
[model_providers.company_proxy]
name = "Company Proxy"
base_url = "https://proxy.example.com/v1"
env_key = "COMPANY_PROXY_API_KEY"
wire_api = "responses"
```

Then export `COMPANY_PROXY_API_KEY` in the shell that starts Agent-Team and add
both `--role-model reviewer=proxy-model` and
`--role-model-provider reviewer=company_proxy` to `agent-team init`.

`--role-dsh-plugin` is DSH-only and declares one installable bundle directory
inside the Run worktree. Agent-Team does not snapshot it at `init`: when that
role is first routed, the Adapter copies the then-current package into the
role-private DSH Profile, freezes its file manifest and SHA-256, and includes
the hash in the LaunchSpec. This lets a Developer and Reviewer finish a plugin
before a fresh Validator Agent is created, without nesting DSH from a
model-facing Bash process. The frozen copy remains authoritative for that role
for the rest of the Run; testing a later revision requires a new Run.

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

| Profile | Codex | Claude Code | OpenCode | DeepSeek Harness |
| --- | --- | --- | --- | --- |
| `default` | Workspace write, scratch paths, no command network, no approval prompts | `acceptEdits`, OS workspace sandbox, internal scratch path, no fallback | Worktree file/search/LSP/todo tools; arbitrary Bash, external paths, web, skills, tasks, and MCP tools denied | DSH `workspace-write`; write effects confined to the worktree |
| `trusted-workspace` | Same filesystem boundary with command network | `acceptEdits`, same OS workspace sandbox, no fallback | Same worktree boundary; built-in web tools additionally allowed; arbitrary Bash still denied | Same DSH write boundary; network remains inherited |
| `full-access` | `danger-full-access`, no approval prompts | `--dangerously-skip-permissions` (`bypassPermissions`), Claude sandbox disabled; Run-private config records the one-time confirmation | All OpenCode tools and host Bash allowed; Agent-Team management command patterns remain denied | DSH `danger-full-access`, no approval prompts |

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

OpenCode has no OS sandbox around Bash. Its contained Profiles therefore allow
only the exact absolute `handoff`, `complete`, and `block` command patterns;
general shell commands—including test commands—are denied. Use an OpenCode
`full-access` role when the task requires arbitrary commands, after the Run's
YOLO confirmation. Agent-Team launches OpenCode with a private per-Run/Role
`XDG_CONFIG_HOME`, inline permission/agent config,
`OPENCODE_DISABLE_PROJECT_CONFIG=1`, and `--pure`. This excludes mutable user
and project permission, MCP, agent, and external-plugin config while preserving
the machine-local OpenCode credential and Session stores. If the selected model
uses a custom provider, Agent-Team freezes only that provider definition on the
role's first activation. Expanded credentials are converted back to
`{env:VARIABLE}` references; a literal credential that cannot be represented
safely fails before launch instead of entering managed state or traces. At
Worker creation, Agent-Team injects only the environment names referenced by
that frozen provider through the tmux window environment. Missing or empty
values fail closed, and their plaintext values are never written to the
provider snapshot, `LaunchSpec`, Journal, or trace. Managed OpenCode
configuration has higher priority and remains outside the Profile Hash.

DSH's sandbox is a file-effect boundary, not a complete host sandbox.
`default` and `trusted-workspace` prevent writes outside the worktree, but reads,
process execution, credentials in the environment, and network access remain
available. The two restricted DSH Profiles are therefore identical in v0.1.
The private profile disables DSH permission switching, user profiles, Skills,
subagents, workflows, telemetry, and title-model calls; formal Agent-Team
actions and the Journal remain the only collaboration control path.

Agent-Team freezes the supplied mapping and `launch_profile_sha256`, excludes
mutable user permission settings, isolates OpenCode project config and external
plugins, and sets Codex `features.hooks=false`.
Managed administrator policy remains higher authority: it can reject a launch,
force managed hooks, add paths or side effects, merge Claude sandbox arrays,
or override scalars. `doctor` reports Agent-Team's mapping but cannot prove the
final cloud-delivered or Managed policy. Inspect administrator configuration
and Claude `/status` and `/permissions`, or use an unmanaged VM, when the
boundary is security-critical.

## Runtime lifecycle and formal actions

`init` commits an UNSTARTED audit directory but starts no process and acquires
no workspace ownership. `start` performs final checks, records the single
Kickoff, acquires durable ownership, and creates a tmux Worker only for the
initial External role. Each Handoff retires the sender Worker and lazily creates
the target Worker; a later route back creates another Worker process and
resumes the frozen Harness Session when requested. Repeated `start` converges
through deterministic recovery; it does not create a second Kickoff.

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

`attach` opens a read-only tmux client. It shows the native Harness terminal
(a Codex/Claude Code/DSH TUI or OpenCode direct-interactive output) for an active
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
fixed account directory contains workspace ownership, operation locks, private
interactive Codex Homes, private OpenCode configuration Homes, private DSH
Homes, and the pinned managed DSH runtime.

Formal action and Resume source files must live inside their Turn directory.
When accepted, Agent-Team reads them without following symlinks, rejects hard
links, and forces mode `0600`, independent of the originating editor's umask.

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

Real Codex, mixed Claude Code/Codex, OpenCode, and DSH validation reports are indexed in
[`docs/validation`](validation/README.md). Reports are historical evidence;
the technical design and current tests define the latest contract.
