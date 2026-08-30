# Three-DSH greenfield SQLite Plugin self-development case

The user's exact current instructions include:

> 临时修改dsh上下文窗口大小，然后使用doubao-seed-2-0-pro-260215从头开始

After the preceding Run exposed that the Validator was activated before the
candidate could actually load, the user clarified:

> 对啊，按这个来改吧。其实应该在我们的端到端 case 描述中描述清楚，然后让 Agent Team 内部的 Agent 自己去完成这些改动。

The user then requested a new from-zero attempt:

> 那就重新穷投再来。

After disclosure that this new Run's three DSH Agents can access host files,
environment credentials, and the network without per-command approval, the
user replied:

> 确认

Use three independent DeepSeek Harness External Agents in the single clean Git
worktree
`/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-ctx256k-greenfield-e2e-v2`:
a Developer, a Reviewer, and a fresh Validator. The worktree is detached at
baseline commit `47f943859bef60e4160492346772ded9b24f765a`, begins with clean
Git status, and contains only an empty `packages/storage/tool-sqlite`
directory created so Agent-Team can declare the future Validator bundle path.
The Developer must create the Plugin from nothing in this Run.

This is a strict greenfield test. No role may read, inspect, diff, copy, or use
as reference any prior SQLite Plugin candidate, prior Run input or completion,
generation-private DSH Home, historical validation artifact for this Plugin,
the user's main DeepSeek Harness worktree, or another DSH worktree. Roles may
use only the clean target worktree, its repository instructions and existing
architectural patterns, their own managed DSH Profile, and immutable
inputs/evidence supplied by this Run.

In particular, the cancelled Run
`at-dsh-sqlite-plugin-doubao2-ctx256k-greenfield-20260829` and its worktree are
historical evidence only for the Origin and are forbidden inputs for all three
business roles. The new Plugin must be created independently from the baseline.

All roles use
`deepseek-official/doubao-seed-2-0-pro-260215` through DeepSeek Harness's
native trusted environment. The local managed DSH runtime has a temporary
deployment override of `defaultContextWindow: 256000` and
`maxTokens: 131072`, because the provider rejected DSH's generic 256000 output
default and explicitly reported a maximum of 131072. Every role must record
the observed provider, model, context window, and output cap from durable DSH
events without persisting endpoint or credential values. All roles use native
interactive mode and the explicitly confirmed `full-access` Profile.

## Product to develop

Create an opt-in, installable, product-quality model-facing function Plugin
named `@deepseek-ai/dsh-tool-sqlite` at `packages/storage/tool-sqlite`. It must
expose:

- `sqlite_schema`, which inspects a workspace-authorized local SQLite database
  and returns canonical model-visible data for user tables, views, and virtual
  tables, including columns, declared types, primary keys, foreign keys,
  indexes, SQL definitions, and exact STRICT status. Internal shadow tables
  must not be presented as ordinary user objects.
- `sqlite_query`, which executes exactly one parameterized, read-only
  `SELECT`, `WITH ... SELECT`, `VALUES`, or `EXPLAIN QUERY PLAN` statement and
  returns columns, positional rows, typed values, row count, truncation,
  result byte count, and elapsed time.

Preserve duplicate result-column names by position. Preserve integers outside
JavaScript's safe range as decimal strings with explicit safety metadata.
Represent BLOB values only by byte length and SHA-256. Enforce row and complete
result byte bounds with explicit truncation.

Enforce read-only behavior in depth: open the database read-only and use
SQLite engine-level authorization, not SQL text matching alone. Reject DML,
DDL, `ATTACH`, `DETACH`, `VACUUM`, writable or unrestricted `PRAGMA`, extension
loading, transactions, and multiple/trailing statements. Database paths must
remain inside the authorized workspace and resist absolute paths, parent
traversal, missing paths, non-database files, and symlink escape. Implement
cooperative cancellation, a query deadline with a safe configurable override,
predictable errors, and deterministic connection/worker cleanup on success,
failure, cancellation, worker exit, HMR replacement, and Plugin disposal.

The package must be genuinely installable outside the monorepo: provide a
package-owned Cordis patch, valid `dsh.bundle.patch`, complete exports/files,
built runtime artifacts, dependency hygiene, configuration schema and defaults
where appropriate, and an invariant companion. Keep it opt-in and do not add
it to a shipped default Profile. Add focused unit and real SQLite integration
tests, real Loader composition, assembled model-visible snapshot coverage,
disposal evidence, package and bilingual documentation, an Agent Note,
generated tool/config/module catalogs as required, and the smallest sufficient
repository gates.

Installability is a product acceptance gate, not an Agent-Team runtime feature.
The candidate itself must survive the repository-native equivalent of this
sequence without relying on monorepo source resolution, workspace links, or an
existing `node_modules`: build the package, create a package archive, inspect
the archive contents, install that archive into a newly created isolated DSH
Profile/environment, activate its package-owned Cordis patch, and perform a
real DSH Loader/composition smoke that imports the declared runtime entry and
observes both registered tools. The smoke must not invoke another model Agent
or manage tmux. Any missing export, omitted built file, broken package metadata,
dependency leak, activation error, or Loader failure is a normal product
finding to fix inside this team loop.

Agent-Team remains artifact-agnostic: it freezes and provisions the reviewed
Workspace Plugin generation for Validator, but it does not interpret Node
package semantics or replace the Developer/Reviewer acceptance gate.

## Collaboration and validation

Developer is initial and resumable. It first verifies the package directory is
empty, then independently designs, implements, builds, tests, documents, and
checks installability using only clean-repository patterns. Before routing to
Reviewer, it must run the complete build → package archive → clean isolated
install → real Loader/composition smoke gate above and record reproducible
commands, archive contents, the resolved runtime entry, Plugin activation, and
both registered tool names. It routes to Reviewer only with a coherent product
candidate and that evidence.

Reviewer is separate and resumable. It independently reviews the complete live
diff and reports every P0-P3 finding. It must not accept Developer's packaging
evidence on trust: from a clean isolated destination, it independently repeats
the build/archive inspection/install/activation/Loader smoke, checks the
effective `main`/`exports`/`files`, proves resolution comes from the packed
artifact rather than the worktree, and observes both tools registered. It also
reviews the SQLite authorization boundary, path authority, statement
completeness, parameters, integer/BLOB and duplicate-column representation,
output bounds, cancellation, lifecycle, bundle correctness, model-visible
output, generated surfaces, tests, and docs. Every failure or finding routes to
Developer for repair and then returns to Reviewer for the complete relevant
recheck. Reviewer must never route an unbuildable, unpackable, un-installable,
or unloadable candidate to Validator, and such a candidate is not a reason to
Block. Only a clean complete review with the independent packaging/load gate
passing routes to Validator.

Validator is fresh and Completion Authority. On every route, Agent-Team
freezes and installs the current `packages/storage/tool-sqlite` bundle into a
new generation-private DSH Profile. Validator calls `sqlite_schema` and
`sqlite_query` directly through that managed DSH Session; it must not launch a
nested DSH or manage tmux.

Validator independently creates a realistic workspace-local SQLite database
with related users/projects/tasks/events data, foreign keys, indexes, a view,
a STRICT table, an FTS5 virtual table, NULL, duplicate selected column names,
an integer beyond JavaScript's safe range, and BLOB data. It verifies schema
accuracy, shadow filtering, named and positional parameters, joins,
aggregation, a CTE or window query, query-plan output, typed values,
bounds/truncation, and cancellation/deadline. It attempts DML, DDL, `ATTACH`,
writable `PRAGMA`, multiple statements, absolute/parent/symlink escape, and
extension loading; it proves with file hashes and row counts that rejected
operations leave the database unchanged. It verifies frozen package identity,
private Profile composition, direct model-visible calls/results, selected
model and temporary limits, durable trace, and relevant repository gates.

Any Validator P0-P3 source or test finding routes to Developer, then Reviewer,
then a later fresh Validator generation. A frozen generation is never itself a
Block reason. The pre-Validator package/load gate remains the Developer and
Reviewer responsibility; Validator does not launch a nested DSH to compensate
for missing evidence. Completion requires a clean Reviewer verdict plus the
independently repeated clean package/load gate, successful Agent-Team-managed
installation, real DSH loading, direct invocation of both tools, independent
results, denial/immutability proof, cancellation/deadline and cleanup evidence,
durable trace and selected-model evidence, and applicable tests/gates. Block
only for a genuine runtime/prerequisite failure after the required product
gates passed, an irreconcilable protocol conflict, or a hard Run limit.

Do not commit, push, publish, alter user-level DSH configuration, inspect prior
Plugin worktrees/artifacts, or perform unrelated external actions.

Limits: at most 18 role Turns and 7200 seconds. The user explicitly confirmed
this new immutable Run's three DSH roles may use `full-access`.
