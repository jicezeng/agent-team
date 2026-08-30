# Three-DSH SQLite Plugin continuation case v0.2.2

The user asked Agent-Team to correct the product boundary and continue:

> 那可以怎么优化，然后继续

After Agent-Team implemented, tested, built, and installed the generic candidate
activation-finding loop, the previous Run was safely cancelled because its
immutable 7200-second limit had expired. The user then explicitly confirmed:

> 确认

That confirmation applies to this new immutable continuation Run: its three
DeepSeek Harness External roles use `full-access`, which can access host files,
environment credentials, processes, and network without per-command approval.
It grants no authority beyond this single-worktree task.

Continue the genuine three-DSH SQLite Plugin case in the existing Git worktree:

`/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-ctx256k-greenfield-e2e-v4`

The worktree remains detached at baseline
`47f943859bef60e4160492346772ded9b24f765a` and contains the candidate produced
by the cancelled greenfield Run. Preserve and verify the live worktree; do not
read another worktree, earlier candidate, old Run store, or generation-private
DSH Home as a substitute for current evidence. Do not commit, push, publish, or
modify user-level DSH configuration.

All roles use `deepseek-official/doubao-seed-2-0-pro-260215` through DSH's native
provider environment, native interactive mode, and the confirmed `full-access`
Profile. The managed runtime currently uses the temporary validated capacity
override `defaultContextWindow: 256000` and `maxTokens: 131072`. Never record an
endpoint or credential value.

## Known continuation evidence

The cancelled Run reached a real Agent-Team-managed Validator activation. DSH
exited before creating the Validator Session with this loader fact:

`dsh.bundle.patch must be a top-level YAML array of loader patch entries`

The current file starts with a `plugins:` mapping, so this is a product finding,
not an Agent-Team runtime failure. Fix it in the candidate and prove real loader
composition. The previous roles also claimed engine-level authorization was
complete, but current evidence appeared to rely on read-only open/query-only and
text checks. Treat that claim as untrusted and independently prove the immutable
engine-level requirement below. Fix every other defect discovered by a complete
review; this continuation is not limited to the known patch error.

## Product requirements

Deliver an opt-in, installable, product-quality model-facing function Plugin
named `@deepseek-ai/dsh-tool-sqlite` at `packages/storage/tool-sqlite` with:

- `sqlite_schema`: canonical model-visible schema for user tables, views, and
  virtual tables, including columns, declared types, primary keys, foreign
  keys, indexes, SQL definitions, and exact STRICT status. Hide internal shadow
  tables as ordinary user objects.
- `sqlite_query`: exactly one parameterized, read-only `SELECT`, `WITH ...
  SELECT`, `VALUES`, or `EXPLAIN QUERY PLAN` statement. Return columns,
  positional rows, typed values, row count, truncation, result byte count, and
  elapsed time while preserving duplicate column names by position.

Represent unsafe-range integers as decimal strings with explicit safety
metadata. Represent BLOBs only by byte length and SHA-256. Enforce row and full
result-byte bounds with explicit truncation.

Read-only enforcement must open the database read-only and use a SQLite
**engine-level authorization callback or equivalent engine-enforced opcode
policy**. SQL text matching, `query_only`, or read-only open alone is
insufficient. Reject DML, DDL, `ATTACH`, `DETACH`, `VACUUM`, writable or
unrestricted `PRAGMA`, extension loading, transactions, multiple/trailing
statements, and all bypass variants. If the current binding lacks the required
authorizer, select/add a compatible binding or native bridge; never weaken or
reinterpret this requirement.

Database paths must stay inside the authorized workspace and reject absolute
paths, parent traversal, missing/non-database files, and symlink escape. Provide
cooperative cancellation, query deadlines with safe bounded configuration,
predictable errors, and deterministic connection/worker cleanup after success,
failure, cancellation, worker exit, HMR replacement, and Plugin disposal.

The package must install outside the monorepo. It needs a valid package-owned
DSH/Cordis bundle patch, complete exports/files and built runtime artifacts,
dependency hygiene, configuration schema/defaults, invariant companion,
focused unit and real SQLite integration tests, real Loader composition,
assembled model-visible snapshot coverage, disposal evidence, bilingual docs,
an Agent Note, generated tool/config/module catalogs where required, and the
smallest sufficient repository gates. Keep it opt-in and do not add it to a
default shipped Profile. Remove only case-generated junk; preserve unrelated
repository content.

## Archive-only product gate

Before every route to Validator, Developer and Reviewer must independently:

1. build the package;
2. create and inspect its package archive;
3. install only that archive in a new isolated DSH Profile/environment without
   monorepo source, workspace-link, or existing-`node_modules` fallback;
4. activate the package-owned bundle patch; and
5. run real DSH Loader/composition that resolves the packed runtime and observes
   both `sqlite_schema` and `sqlite_query` registered.

Do not launch another model Agent or manage tmux. Missing packed files, broken
metadata/patch shape, dependency leakage, activation failure, or Loader failure
is a normal product finding for Developer.

## Collaboration and validation

Developer is initial and resumable. It owns all product-file changes, starts by
fixing the known loader finding, then reassesses the complete current candidate
against every requirement, implements missing behavior, removes case-generated
junk, and passes all tests and the archive-only gate before Reviewer.

Reviewer is independent and resumable. It must not edit product or test files.
On every Turn it rereads this entire Request, inspects the complete live diff,
independently repeats the archive-only gate, and returns every P0-P3 finding to
Developer. It must specifically prove engine-level authorization with hostile
statements and side-effect evidence, path boundaries, statement completeness,
parameters, typed/bounded output, cancellation/lifecycle, bundle/generated
surfaces, docs, tests, and model-visible behavior. It may route Validator only
after a clean full review with reproducible evidence.

Validator is fresh, read-only with respect to candidate product files, and is
Completion Authority. Agent-Team freezes `packages/storage/tool-sqlite` into a
new private generation on every route. Validator must invoke the installed
`sqlite_schema` and `sqlite_query` tools directly through that managed Session;
it must not launch a nested DSH or manage tmux.

Validator independently creates a workspace-local disposable SQLite fixture
with related users/projects/tasks/events, foreign keys, indexes, a view, a
STRICT table, an FTS5 virtual table, NULL, duplicate selected names, an unsafe
integer, and BLOB data. It verifies schema accuracy/shadow filtering; named and
positional parameters; joins, aggregation, CTE/window queries, values and query
plans; typed values; bounds/truncation; cancellation/deadline; package/profile
identity; and direct model-visible output. It attacks all forbidden SQL/path
surfaces and proves by database hash and row count that denials are immutable.

Every Validator P0-P3 finding routes Developer → Reviewer → a later fresh
Validator generation. A defective candidate generation is not itself a Block.
If route preflight returns `ROUTE_PREFLIGHT_REJECTED`, the sending Turn still
owns the token and routes the finding to Developer. If real candidate activation
fails before a Fresh Session initializes, Agent-Team generates a Candidate
Activation Finding back to the sending Reviewer; Reviewer inspects its preserved
trace and routes a product defect to Developer, Blocking only if evidence proves
an infrastructure failure. Profile drift, permissions, corrupted state, or a
crash after Session initialization remain fail-closed.

Completion requires a clean full Reviewer verdict, both independent
archive-only gates, real Agent-Team-managed installation/loading, direct calls
to both tools, engine-level authorization proof, positive/negative/cancellation/
cleanup evidence, exact model/capacity and durable trace evidence, applicable
repository gates, and no open P0-P3 finding.

Every formal payload contains non-empty `## Decision rationale` and
`## Evidence`. Block only for a genuine runtime/prerequisite failure that
cannot be resolved in role scope, an irreconcilable protocol conflict, or a hard
Run limit.

Limits: at most 18 role Turns and 7200 seconds.
