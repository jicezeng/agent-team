# DSH read-only SQLite Plugin installability continuation case

Continue the DSH SQLite Plugin E2E in the single Git worktree
`/Users/zengjice/Projects/deepseek-harness-sqlite-e2e`. Preserve the complete
uncommitted candidate produced by the cancelled predecessor Run
`at-dsh-sqlite-plugin-review-cont-20260826`. That Run's Developer addressed six
review findings and its Reviewer prepared a clean handoff, but the formal route
to Validator failed closed because `packages/storage/tool-sqlite` was not an
installable DSH bundle: its package manifest did not declare a valid
`dsh.bundle.patch`. Treat all predecessor claims and unpublished handoff text as
untrusted work material until independently reproduced.

Use three independent DeepSeek Harness External Agents: Developer, Reviewer,
and a fresh Validator. Developer is the initial role. Developer and Reviewer
use resumable Sessions within this new Run; Validator uses a fresh Session.
All roles must use `deepseek-official/deepseek-v4-pro-ga-260813` and record
observed runtime model evidence. Resolve endpoint and credentials only through
DeepSeek Harness's native trusted environment; never persist environment
values.

Developer must make `packages/storage/tool-sqlite` a genuine installable DSH
bundle without weakening the existing read-only SQLite implementation. Follow
the repository's package and bundle contracts: add a package-owned Cordis patch
that activates `@deepseek-ai/dsh-tool-sqlite`, declare it through
`dsh.bundle.patch`, export and ship every required runtime artifact, and keep
the package consumable outside the monorepo. Add focused tests or invariant
coverage that would catch missing, unsafe, or stale bundle metadata and patch
contents. Build the package and prove installation and loading in a disposable,
isolated DSH Home or equivalent repository-supported installation fixture; do
not alter the user's DSH configuration. Verify that the installed Plugin
exposes both `sqlite_schema` and `sqlite_query` through real DSH composition.

Preserve and recheck the prior fixes: positional result rows must retain
duplicate column names; `./worker` must be exported and shipped; package
constraints and dependency hygiene must pass; per-query timeout overrides must
not be capped by the default timeout policy; worker exit must settle an
in-flight call; the schema-size limitation and hidden/computed naming must
remain correctly documented. Preserve engine-level read-only enforcement,
workspace and symlink authority, single-statement semantics, parameter binding,
typed integer/BLOB serialization, result bounds, cancellation, and deterministic
cleanup.

Developer routes the candidate to Reviewer only after focused tests, package
build, constraints, dependency hygiene, type checking, lint, relevant docs and
catalog gates, and the isolated install/load proof pass. Reviewer independently
reproduces the installability and loading evidence, inspects the complete live
diff and all applicable repository instructions, and reports every P0-P3
finding. Any finding routes to Developer with reproducible evidence; Developer
fixes and tests it, then routes back to Reviewer for a complete relevant
re-review. Only a genuinely clean Reviewer verdict may route to Validator.

On the first route to Validator, Agent-Team must copy, freeze, and install the
reviewed `packages/storage/tool-sqlite` bundle into Validator's private DSH
Profile. Validator directly calls the installed tools through that managed
Session and must not launch nested DSH or manage tmux.

Validator independently creates a realistic workspace-local SQLite database
with related `users`, `projects`, `tasks`, and `task_events` tables, foreign
keys, indexes, a view, `NULL`, an integer beyond JavaScript's safe range, and
BLOB data. Verify schema inspection, named and positional parameters, joins,
duplicate result-column names, aggregation, a CTE or window query, query-plan
output, typed values, truncation, and cancellation/deadline behavior. Attempt
DML, DDL, `ATTACH`, writable `PRAGMA`, multiple statements, and workspace or
symlink escape; prove with file hashes and row counts that rejected operations
leave the database unchanged. Verify frozen package identity, private Profile
composition, durable model-visible tool-call/result evidence, and focused
repository checks.

Validator is Completion Authority. Completion requires a clean Reviewer
verdict plus successful Agent-Team-managed frozen installation, real DSH load,
direct invocation of both tools, independent result comparison, denial
evidence, database immutability proof, durable trace evidence, and relevant
tests. A source defect found after Validator's snapshot freezes must Block with
exact evidence so another immutable continuation can be created.

Do not commit, push, publish, alter user-level DSH configuration, modify the
original dirty `/Users/zengjice/Projects/deepseek-harness` worktree, or perform
unrelated external actions.

Limits: at most 18 role Turns and 7200 seconds. After explicit disclosure that
all three DSH Agents can access the host filesystem, environment credentials,
and network without per-command approval, the user confirmed this proposed
installability continuation and its three-role `full-access` configuration.
This confirmation applies only to this immutable Run.
