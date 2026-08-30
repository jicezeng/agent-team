# Three-DSH greenfield SQLite Plugin self-development case v0.2.1

The user asked Agent-Team to repair the collaboration gap and then continue:

> 好，修复，然后继续

This accepted the immediately preceding proposal to cancel and preserve the old
Block Run, classify a fixable Reviewer-to-Validator Workspace Plugin preflight
failure as an auditable business finding, install the repair, and start a new
from-zero three-DSH Run. That proposal explicitly disclosed that all three DSH
roles would use `full-access`; the user's acceptance is the permission decision
for this immutable Run.

Run a genuine greenfield self-development case with three independent DeepSeek
Harness External roles in the single clean Git worktree
`/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-ctx256k-greenfield-e2e-v4`:
Developer, Reviewer, and Validator. The worktree is detached at baseline
`47f943859bef60e4160492346772ded9b24f765a`, has clean Git status, and begins
with an empty `packages/storage/tool-sqlite` directory. Developer must create
the Plugin from nothing during this Run.

This is a strict greenfield test. No business role may read, inspect, diff,
copy, or use as reference any earlier SQLite Plugin candidate, cancelled Run,
report, generation-private DSH Home, the user's main DeepSeek Harness worktree,
or another DSH worktree. Roles may use only this clean worktree, repository
instructions and existing general architectural patterns inside it, their own
managed DSH Profile, and immutable evidence supplied by this Run.

All roles use `deepseek-official/doubao-seed-2-0-pro-260215` through DeepSeek
Harness's native trusted provider environment. The managed runtime currently
uses the temporary validated capacity override `defaultContextWindow: 256000`
and `maxTokens: 131072`. Record model/capacity evidence without persisting any
endpoint or credential value. All roles use native interactive mode and the
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

Enforce read-only behavior in depth: open the database read-only and use a
SQLite **engine-level authorization callback or equivalent engine-enforced
opcode policy**, not SQL text matching alone. Reject DML, DDL, `ATTACH`,
`DETACH`, `VACUUM`, writable or unrestricted `PRAGMA`, extension loading,
transactions, and multiple/trailing statements. Database paths must stay
inside the authorized workspace and resist absolute paths, parent traversal,
missing paths, non-database files, and symlink escape. Implement cooperative
cancellation, a query deadline with a safe configurable override, predictable
errors, and deterministic connection/worker cleanup on success, failure,
cancellation, worker exit, HMR replacement, and Plugin disposal.

The engine-level authorization requirement is immutable and non-negotiable in
this Run. If the first SQLite binding lacks a suitable authorizer API, roles
must select or add a compatible binding/native bridge, or leave an explicit
open finding and continue the repair loop. They may not reinterpret the
requirement, replace it with text validation, delete or weaken evidence, or
declare the requirement unnecessary.

The package must install outside the monorepo. Provide a package-owned Cordis
patch, valid `dsh.bundle.patch`, complete exports/files and built runtime
artifacts, dependency hygiene, configuration schema/defaults where appropriate,
and an invariant companion. Keep it opt-in; do not add it to a shipped default
Profile. Add focused unit and real SQLite integration tests, real Loader
composition, assembled model-visible snapshot coverage, disposal evidence,
package and bilingual documentation, an Agent Note, generated tool/config/
module catalogs as required, and the smallest sufficient repository gates.

## Archive-only product gate

Before Validator is routed, Developer and Reviewer must each independently:

1. build the package;
2. create and inspect a package archive;
3. install only that archive into a new isolated DSH Profile/environment with
   no monorepo source, workspace-link, or existing-`node_modules` fallback;
4. activate the package-owned Cordis patch; and
5. run a real DSH Loader/composition smoke that resolves the packed runtime
   entry and observes `sqlite_schema` and `sqlite_query` registered.

The smoke must not launch another model Agent or manage tmux. Missing exports,
omitted built files, broken metadata, dependency leakage, activation failure,
or Loader failure is a normal product finding that returns to Developer.
Agent-Team freezes and provisions a reviewed Workspace Plugin generation; it
does not replace this product gate or interpret Node package semantics.

## Collaboration and validation

Developer is initial and resumable. It verifies that the package directory is
empty, independently designs and implements the candidate, tests and documents
it, and passes the complete archive-only product gate before Reviewer.

Reviewer is independent and resumable. On **every** review Turn it rereads this
entire Request and evaluates the complete live candidate, not merely the latest
Handoff or finding list. It reports every P0-P3 finding and independently
repeats the archive-only product gate. It must verify engine-level authorization
with hostile statements and side-effect evidence, path boundaries, statement
completeness, parameters, typed/bounded output, cancellation/lifecycle,
bundle/generated surfaces, tests, docs, and model-visible behavior. A sender's
claim, deleted test, narrower scope, or unsupported API does not close a
requirement. Every finding returns to Developer and then Reviewer. Reviewer may
route Validator only after a clean full review and passing independent gate.

Validator is fresh and is Completion Authority. On each route Agent-Team
freezes the current `packages/storage/tool-sqlite` bundle into a new private DSH
generation. Validator invokes the installed `sqlite_schema` and `sqlite_query`
tools directly through that managed Session; it must not launch a nested DSH or
manage tmux.

Validator independently creates a realistic workspace-local SQLite fixture
containing related users/projects/tasks/events, foreign keys, indexes, a view,
a STRICT table, an FTS5 virtual table, NULL, duplicate selected column names,
an integer beyond JavaScript's safe range, and BLOB data. It verifies schema
accuracy and shadow filtering; named and positional parameters; joins,
aggregation, CTE/window queries and query-plan output; typed values; bounds/
truncation; cancellation/deadline; package identity; private Profile
composition; and direct model-visible results. It attacks DML, DDL, `ATTACH`,
writable `PRAGMA`, multiple statements, absolute/parent/symlink escape and
extension loading, and proves by database hash and row count that denials leave
the database unchanged.

Any Validator P0-P3 source or test finding routes to Developer, then Reviewer,
then a later fresh Validator generation. A frozen defective generation is not a
Block. If Reviewer attempts `handoff --to validator` and receives
`ROUTE_PREFLIGHT_REJECTED`, no Outbox/Event was accepted and Reviewer still owns
the same Turn. Reviewer must record the reported installability defect in a new
payload and formally hand it to Developer; the failed CLI invocation is not the
Turn's action. Frozen Profile drift or a target change detected after Outbox
staging remains a genuine fail-closed Block.

If a resumable Developer or Reviewer invocation structurally exhausts its
output budget before staging an action, Agent-Team may create a new counted
same-role Turn only when all generic runtime gates pass. The role continues its
unfinished responsibility from the live worktree and preserved Session. This
grants no new authority. Fresh Validator exhaustion, ordinary crashes,
permission/audit failures, existing Outbox, hard limits, or repeated no-progress
must Block. Every committed Block returns to the user.

Completion requires a clean full Reviewer verdict, independently passing
archive-only gates, Agent-Team-managed installation and real loading, direct
invocation of both tools, engine-level authorization evidence, correctness and
denial evidence, cancellation/deadline/cleanup evidence, durable model/capacity
and trace evidence, applicable repository gates, and no open P0-P3 finding.

Block only for a genuine runtime/prerequisite failure that cannot be resolved
within role scope, an irreconcilable protocol conflict, or a hard Run limit. Do
not commit, push, publish, alter user-level DSH configuration, inspect forbidden
prior artifacts, or perform unrelated external actions.

Limits: at most 18 role Turns and 7200 seconds. The user explicitly accepted
this immutable Run's three DSH roles using `full-access`.
