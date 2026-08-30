# Three-DSH greenfield SQLite Plugin self-development case

The user's exact current instructions are:

> 改成用glm-5-2-260617从头开始

After disclosure that this new Run's three DSH Agents can access host files,
environment credentials, and the network without per-command approval, the
user replied:

> 确认

Use three independent DeepSeek Harness External Agents in the single clean Git
worktree `/Users/zengjice/Projects/deepseek-harness-sqlite-glm52-greenfield-e2e`: a
Developer, a Reviewer, and a fresh Validator. The worktree is detached at
baseline commit `47f943859bef60e4160492346772ded9b24f765a`, begins with a clean
Git status, and contains only an empty `packages/storage/tool-sqlite` directory
created so Agent-Team can declare the future Validator bundle path. The
Developer must create the Plugin from nothing in this Run.

This is a strict greenfield test. No role may read, inspect, diff, copy, or use
as reference any prior SQLite Plugin candidate, prior Run input or completion,
generation-private DSH Home, or other DSH worktree, including
`/Users/zengjice/Projects/deepseek-harness-sqlite-e2e`,
`/Users/zengjice/Projects/deepseek-harness-sqlite-greenfield-e2e`,
`/Users/zengjice/Projects/deepseek-harness-sqlite-evolving-greenfield-e2e`,
`/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-greenfield-e2e`,
`/Users/zengjice/Projects/deepseek-harness-sqlite-v4-flash-greenfield-e2e`,
`/Users/zengjice/Projects/deepseek-harness`, and historical Agent-Team
validation files for this Plugin. Roles may use only the clean target worktree,
its repository instructions and existing architectural patterns, their own
managed DSH Profile, and immutable inputs/evidence supplied by this Run.

All roles use `deepseek-official/glm-5-2-260617` through DeepSeek
Harness's native trusted environment and record observed runtime model
evidence. Never persist endpoint or credential values. All roles use the
native interactive mode and the explicitly confirmed `full-access` Profile.

## Product to develop

Create an opt-in, installable, product-quality model-facing function Plugin
named `@deepseek-ai/dsh-tool-sqlite` at
`packages/storage/tool-sqlite`. It must expose:

- `sqlite_schema`, which inspects a workspace-authorized local SQLite database
  and returns model-visible canonical data for user tables, views, and virtual
  tables, including columns, declared types, primary keys, foreign keys,
  indexes, SQL definitions, and exact STRICT status. Internal shadow tables
  must not be presented as ordinary user objects.
- `sqlite_query`, which executes exactly one parameterized, read-only
  `SELECT`, `WITH ... SELECT`, `VALUES`, or `EXPLAIN QUERY PLAN` statement and
  returns model-visible columns, positional rows, typed values, row count,
  truncation, result byte count, and elapsed time.

Preserve duplicate result-column names by position. Preserve integers outside
JavaScript's safe range as decimal strings with explicit safety metadata.
Represent BLOB values only by byte length and SHA-256, never by returning raw
bytes. Enforce row and complete-result byte bounds with explicit truncation.

Read-only behavior must be enforced in depth: open the database read-only and
use SQLite engine-level authorization, not SQL text matching alone. Reject DML,
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
it to any shipped default Profile. Add focused unit and real SQLite integration
tests, real Loader composition, assembled model-visible snapshot coverage,
disposal evidence, package and bilingual documentation, an Agent Note,
generated tool/config/module catalogs as required, TypeScript aggregate/example
wiring only where repository contracts require it, and the smallest sufficient
repository gates.

## Collaboration and validation

Developer is the initial role and uses a resumable Session. It must first
verify and record that the package directory was empty, then independently
design, implement, build, test, document, and installability-check the complete
Plugin using only clean-repository patterns. It may bootstrap ignored local
dependencies through repository-supported commands when necessary. It routes
to Reviewer only after a coherent product candidate and reproducible evidence
exist.

Reviewer uses a separate resumable Session. It independently reviews the
complete live diff and all applicable repository instructions, reruns useful
checks, reproduces real package installation/composition where practical, and
reports every P0-P3 finding. Review the SQLite authorization boundary,
filesystem and symlink authority, statement completeness, parameters,
integer/BLOB/duplicate-column representation, output bounds, cancellation,
connection/worker lifecycle, bundle correctness, model-visible schema/results,
generated surfaces, tests, and documentation. Every finding routes to
Developer; fixes return to Reviewer for a complete relevant re-review. Only a
genuinely clean review routes to Validator.

Validator uses a fresh Session and is Completion Authority. On every route,
Agent-Team freezes and installs the then-current
`packages/storage/tool-sqlite` bundle into a new generation-private DSH
Profile. Validator calls `sqlite_schema` and `sqlite_query` directly through
that managed DSH Session and must not start nested DSH or manage tmux.

Validator independently creates a realistic workspace-local SQLite database
with related users/projects/tasks/events data, foreign keys, indexes, a view, a
STRICT table, an FTS5 virtual table, NULL, duplicate selected column names, an
integer beyond JavaScript's safe range, and BLOB data. Verify schema accuracy,
shadow filtering, named and positional parameters, joins, aggregation, a CTE or
window query, query-plan output, typed values, bounds/truncation, and a
cancellation/deadline path. Attempt DML, DDL, `ATTACH`, writable `PRAGMA`,
multiple statements, absolute/parent/symlink escape, and extension loading;
prove with file hashes and row counts that rejected operations leave the
database unchanged. Verify frozen package identity, private Profile
composition, direct model-visible call/results, selected model, durable trace,
and relevant repository checks.

Any Validator P0-P3 source or test finding routes to Developer in this same
Run. Developer fixes it, Reviewer repeats review, and the next Validator route
receives a new immutable Plugin/Session generation while all earlier
generations remain auditable. A frozen generation is never by itself a Block
reason.

Completion requires a clean Reviewer verdict plus successful Agent-Team-managed
frozen installation, real DSH loading, direct invocation of both tools,
independent result comparison, denial and database-immutability proof,
cancellation/deadline and cleanup evidence, durable trace evidence, selected
model evidence, and all relevant tests/gates. Block only for a genuine runtime
or prerequisite failure, irreconcilable protocol conflict, or hard Run limit.

Do not commit, push, publish, alter user-level DSH configuration, inspect prior
Plugin worktrees/artifacts, or perform unrelated external actions.

Limits: at most 18 role Turns and 7200 seconds. The user explicitly confirmed
this new immutable Run's three DSH roles may use `full-access`.
