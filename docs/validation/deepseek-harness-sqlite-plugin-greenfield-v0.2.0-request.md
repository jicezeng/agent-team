# Three-DSH greenfield SQLite Plugin self-development case v0.2.0

The user's current instructions include:

> 临时修改dsh上下文窗口大小，然后使用doubao-seed-2-0-pro-260215从头开始

After discussing why a model could stop at its output limit before submitting a
formal action, the user rejected task-specific Agent-Team hard-coding and asked
for a generic product-level recovery rule plus clearer team guidance:

> 对啊，按这个来改吧。其实应该在我们的端到端 case 描述中描述清楚，然后让 Agent Team 内部的 Agent 自己去完成这些改动。

> 那就按这个方案进行修复，然后再从头执行。

Agent-Team was updated and verified so a structurally reported output-budget
stop can safely continue the same resumable role in a new counted Turn before
any Block is committed. After disclosure that this new Run's three DSH Agents
can access host files, environment credentials, and the network without
per-command approval, the user explicitly confirmed:

> 确认。

Run a genuine from-zero self-development case with three independent DeepSeek
Harness External roles in the single clean Git worktree
`/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-ctx256k-greenfield-e2e-v3`:
Developer, Reviewer, and Validator. The worktree is detached at baseline
`47f943859bef60e4160492346772ded9b24f765a`, has clean Git status, and begins
with an empty `packages/storage/tool-sqlite` directory. The Developer must
create the Plugin from nothing during this Run.

This is a strict greenfield test. No business role may read, inspect, diff,
copy, or use as reference any prior SQLite Plugin implementation, prior
Agent-Team Run or report for this Plugin, generation-private DSH Home, the
user's main DeepSeek Harness worktree, or another DSH worktree. Roles may use
only this clean worktree, its repository instructions and existing general
architectural patterns, their own managed DSH Profile, and immutable evidence
provided by this Run. Historical candidates and cancelled Runs are visible to
the Origin only and are forbidden inputs for all business roles.

All roles use `deepseek-official/doubao-seed-2-0-pro-260215` through DeepSeek
Harness's native trusted provider environment. The managed DSH runtime has a
temporary deployment override of `defaultContextWindow: 256000` and
`maxTokens: 131072`, matching the provider's observed maximum output limit.
Roles must record durable provider/model/capacity evidence without persisting
endpoint or credential values. All roles use native interactive mode and the
explicitly confirmed `full-access` Profile.

## Product to develop

Create an opt-in, installable, product-quality model-facing function Plugin
named `@deepseek-ai/dsh-tool-sqlite` at `packages/storage/tool-sqlite`. It must
expose:

- `sqlite_schema`: inspect a workspace-authorized local SQLite database and
  return canonical model-visible data for user tables, views, and virtual
  tables, including columns, declared types, primary keys, foreign keys,
  indexes, SQL definitions, and exact STRICT status. Internal shadow tables
  must not be presented as ordinary user objects.
- `sqlite_query`: execute exactly one parameterized, read-only `SELECT`,
  `WITH ... SELECT`, `VALUES`, or `EXPLAIN QUERY PLAN` statement and return
  columns, positional rows, typed values, row count, truncation, result byte
  count, and elapsed time.

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

The package must be genuinely installable outside the monorepo. Provide a
package-owned Cordis patch, valid `dsh.bundle.patch`, complete exports/files,
built runtime artifacts, dependency hygiene, configuration schema/defaults
where appropriate, and an invariant companion. Keep it opt-in; do not add it
to a shipped default Profile. Add focused unit and real SQLite integration
tests, real Loader composition, assembled model-visible snapshot coverage,
disposal evidence, package and bilingual documentation, an Agent Note,
generated tool/config/module catalogs as required, and the smallest sufficient
repository gates.

Installability is a product gate, not an Agent-Team runtime feature. Before
Validator is routed, both Developer and Reviewer must independently exercise
the repository-native equivalent of:

1. build the package;
2. create and inspect a package archive;
3. install only that archive into a new isolated DSH Profile/environment with
   no monorepo source, workspace-link, or existing-`node_modules` fallback;
4. activate the package-owned Cordis patch; and
5. run a real DSH Loader/composition smoke that resolves the packed runtime
   entry and observes `sqlite_schema` and `sqlite_query` registered.

The smoke must not invoke another model Agent or manage tmux. Missing exports,
omitted built files, broken metadata, dependency leakage, activation failure,
or Loader failure is a normal product finding that must return to Developer.
Agent-Team remains artifact-agnostic: it freezes and provisions a reviewed
Workspace Plugin generation but does not interpret Node package semantics or
replace this acceptance gate.

## Collaboration and validation

Developer is initial and uses a resumable Session. It verifies the package
directory is empty, independently designs and implements the entire candidate,
tests and documents it, and passes the complete build/package/isolated-install/
real-load gate before routing to Reviewer. Its Handoff records reproducible
commands, archive contents, runtime resolution, activation, and both tools.

Reviewer is independent and resumable. It reviews the complete live diff and
reports every P0-P3 finding. It independently repeats the packaging/load gate
from a clean destination and proves the loaded code comes from the packed
artifact. It also checks SQLite authorization and path boundaries, statement
completeness, parameters, typed and bounded output, cancellation/lifecycle,
bundle/generated surfaces, tests, docs, and model-visible behavior. Every
finding returns to Developer and then Reviewer. Reviewer may route to Validator
only after a clean complete review and a passing independent package/load gate.

Validator is fresh and is Completion Authority. On every route Agent-Team
freezes the current `packages/storage/tool-sqlite` bundle into a new private
DSH generation. Validator calls the installed `sqlite_schema` and
`sqlite_query` tools directly through that managed Session; it must not launch
a nested DSH or manage tmux.

Validator independently creates a realistic workspace-local SQLite fixture
containing related users/projects/tasks/events, foreign keys, indexes, a view,
a STRICT table, an FTS5 virtual table, NULL, duplicate selected column names,
an integer beyond JavaScript's safe range, and BLOB data. It verifies schema
accuracy and shadow filtering; named and positional parameters; joins,
aggregation, CTE/window queries, and query-plan output; typed values;
bounds/truncation; cancellation/deadline; package identity; private Profile
composition; and direct model-visible results. It attacks DML, DDL, `ATTACH`,
writable `PRAGMA`, multiple statements, absolute/parent/symlink escape, and
extension loading, and proves by database hash and row count that denials leave
the database unchanged.

Any Validator P0-P3 source or test finding routes to Developer, then Reviewer,
then a later fresh Validator generation. A frozen defective generation is not
a Block. Completion requires a clean Reviewer verdict, independently passing
pack/load gates, successful Agent-Team-managed installation and real loading,
direct invocation of both tools, independent correctness and denial evidence,
cancellation/deadline/cleanup evidence, durable trace/model/capacity evidence,
and all applicable repository gates.

If a DSH Developer or Reviewer Turn structurally exhausts its output budget
before submitting an action, Agent-Team may create a new counted same-role
Turn on the preserved Session only when its generic safety gates pass. The role
must inspect the live worktree and continue its unfinished responsibility; no
new authority is granted. This is not a Block Resume and is not specific to
SQLite. Fresh Validator output exhaustion, ordinary crashes, permission or
audit failures, an existing Outbox, exhausted Run limits, or repeated
Git-visible no-progress must still Block. Once a Block exists it always returns
to the user.

Block only for a genuine runtime/prerequisite failure that the role cannot
resolve within scope, an irreconcilable protocol conflict, or a hard Run limit.
Do not commit, push, publish, alter user-level DSH configuration, inspect prior
Plugin worktrees/artifacts, or perform unrelated external actions.

Limits: at most 18 role Turns and 7200 seconds. The user explicitly confirmed
this new immutable Run's three DSH roles may use `full-access`.
