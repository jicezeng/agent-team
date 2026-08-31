# Three-DSH SQLite Plugin continuation case v0.2.8

Continue from the live dirty candidate in the single Git worktree:

`/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-ctx256k-greenfield-e2e-v5`

Preserve cancelled Run `at-20260830-143129-091f3e` and its predecessors as
immutable audit records, not proof. The previous Run expired during Developer
turn 11 after Reviewer/Validator findings. Reconstruct from this Request, the
current worktree, repository sources, and current direct evidence. Do not
discard valid candidate work merely because the previous Run ended.

All roles are independent DeepSeek Harness External roles in native interactive
mode using explicit model
`deepseek-official/doubao-seed-2-0-pro-260215`. The user confirmed this new Run
may use `full-access`. Never persist or disclose endpoint, credential, header,
token, or environment values. Do not commit, push, publish, modify user-level
DSH configuration, launch nested model Agents, or manage tmux.

## Open findings and required behavior

Treat every item as open until current evidence closes it. This list does not
narrow the required complete review.

1. No retained managed Validator trace proves direct model tool calls to both
   `sqlite_schema` and `sqlite_query`; Bash, source imports, unit tests, or a
   hand-built call are not substitutes.
2. Exact role order was previously violated and validation-only roles changed
   candidate files. This Run structurally enforces the routes and final
   Git-visible read-only boundary described below.
3. Prove a newly packed archive installed without monorepo source, workspace
   links, pre-existing package, or `node_modules` fallback; apply its active
   package-owned patch with Plugin config omitted; boot the real Loader; observe
   both tools registered with documented defaults; call both installed tools.
4. Preserve lossless real-database integers beyond JavaScript's safe range and
   positional values for duplicate result-column names.
5. Propagate `exec.signal` into actual SQLite work and prove pre-start and
   mid-query cancellation with the repository-required abort reason.
6. Use one operation/lifecycle owner that settles once and cleans workers,
   listeners, timers, statements, and connections on success, error, caller
   cancellation, deadline, worker exit, HMR replacement, and Plugin disposal.
   Use repository-standard `ctx.effect()`/disposers and await quiescence.
7. Make `resultBytes` the exact UTF-8 byte size of the complete emitted JSON,
   including its own digit count, metadata, typed cells, and multibyte data.
   Compute a deterministic fixed point. Return the smallest valid truncated
   envelope when rows do not fit; error only if that envelope cannot fit.
8. Give `rowCount` one documented bounded-streaming meaning; never consume an
   unbounded remainder just to claim a total.
9. Read exact STRICT metadata from SQLite; do not infer it from SQL substrings.
10. Safely handle hostile but valid identifiers such as `odd)name` and FTS
    names containing regex metacharacters using engine-grounded quoting and
    metadata.
11. `sqlite_query` accepts exactly one parameterized `SELECT`, `WITH ...
    SELECT`, `VALUES`, or `EXPLAIN QUERY PLAN`. Reject every PRAGMA, DML, DDL,
    transaction, `ATTACH`, `DETACH`, `VACUUM`, extension load, and trailing
    executable statement while retaining engine-level authorization.
12. Use an engine/parser-grounded single-statement boundary that accepts valid
    trailing comments, SQLite string/identifier quoting, escaped quotes, and
    semicolons; naive splitting or regex classification is insufficient.
13. Omitted Plugin configuration must resolve documented defaults at the real
    Cordis Loader-owned boundary. `apply(ctx, {})` and the archive-only Loader
    composition must work without an ad hoc compatibility boundary.
14. The opt-in `dsh.bundle.patch` must be active when explicitly applied, omit
    hard-coded defaults, and keep the Plugin absent from shipped default
    Profiles.
15. Satisfy repository constraints and standard worker artifact layout. The
    archive contains only intended runtime, declarations, docs, and metadata;
    its worker works with source and monorepo fallbacks absent.
16. Pass applicable contract lint with no broad suppression, unsafe `any`,
    floating cleanup promises, non-null/ts-ignore escapes, or style debt.
17. Minimize exports and pass export JSDoc/type gates for every necessary
    exported surface.
18. Update authoritative tool/config/module generators and commit-equivalent
    generated candidate files so all catalog checks pass.
19. Remove obsolete case-created root probes, scripts, logs, and databases;
    convert useful coverage into package-owned tests/fixtures. Do not remove
    unrelated repository content.
20. Complete English/Chinese Model Experience, security, configuration,
    installation, activation, bounds, cancellation, and limitation docs plus
    translation metadata and a non-trivial Agent Note.
21. Add focused unit/integration coverage for real cancellation, deadline,
    lifecycle/HMR disposal, omitted-config Loader composition, archive consumer,
    exact/tiny/multibyte limits, hostile identifiers, STRICT metadata,
    authorization/database immutability, and an assembled keyless model-visible
    snapshot. Mock only external or nondeterministic dependencies.

## Product contract

Finish the opt-in installable model-facing function Plugin
`@deepseek-ai/dsh-tool-sqlite` at `packages/storage/tool-sqlite`:

- `sqlite_schema` returns canonical user tables, views, and virtual tables with
  columns, declared types, primary keys, foreign keys, indexes, SQL definitions,
  and exact STRICT status while omitting SQLite internal/FTS shadow tables.
- `sqlite_query` returns ordered columns, positional typed rows, the documented
  row count, explicit truncation, exact complete-result bytes, and elapsed time.

Represent unsafe integers as decimal strings with explicit safety metadata and
BLOBs only by byte length and SHA-256. Incrementally consume under row and full
UTF-8 JSON bounds without unbounded materialization.

Open the database read-only and enforce a SQLite engine-level authorizer or
equivalent opcode policy, never SQL text checks alone. Paths must stay within
the authorized workspace and reject absolute paths, parent traversal,
directories, missing/non-database files, and symlink escape. Denied operations
must leave database bytes and row counts unchanged. Cancellation and deadline
must interrupt actual execution, and cleanup must be deterministic.

The package must install outside the monorepo, own its opt-in patch and
invariant companion, expose valid packed artifacts, use the real configuration
boundary, pass applicable package/repository/generated/documentation gates, and
remain absent from shipped default Profiles.

## Mandatory independent gates

Developer, Reviewer, and Validator each preserve reproducible evidence for:

1. applicable build, focused tests, typecheck, lint, constraints, invariants,
   runtime closure, catalogs, docs, snapshot, hygiene, and publish checks;
2. a newly created archive, exact contents, and hash;
3. archive-only installation in a new isolated consumer/Profile;
4. active package patch, omitted Plugin config, real Loader boot, documented
   defaults, and both registered tools;
5. direct calls to both installed tools against a fresh SQLite fixture;
6. positive and hostile behavior above, including cancellation, deadline,
   disposal, exact bounds, identifier/schema cases, path/SQL authorization, and
   unchanged-database proof.

The managed frozen-candidate Validator is an additional real-consumer gate, not
a replacement for these checks. Packaging, loading, activation, dependency,
configuration, or tool defects are product findings and stay in the role loop.

## Exact role actions and routes

- Developer is the only product-writing role. It may only Handoff to Reviewer,
  never Validator, and may not Complete.
- Reviewer is independent and read-only for Git-visible candidate state. It
  returns every P0-P3 finding, failed gate, missing evidence, or unverified
  condition to Developer. Only a complete clean review may Handoff to Validator;
  Reviewer may not Complete or fix the product.
- Validator is fresh, candidate-bound, Completion Authority, and read-only for
  Git-visible candidate state. Any finding returns to Developer; it may not
  route Reviewer or fix the product. It may Complete only with exhaustive
  current evidence and zero findings.

Reviewer and Validator may use disposable validation data but must leave the
final Git-visible worktree byte-for-byte at their Turn's frozen Before state.
Ignored caches are not durable evidence. Agent-Team blocks delivery if their
final Git-visible state changed.

Fresh Validator must call the managed installed `sqlite_schema` and
`sqlite_query` itself; at least one retained trace event must name each tool.
Every Validator finding restarts Developer → Reviewer → a later fresh Validator.
`ROUTE_PREFLIGHT_REJECTED` leaves the sender Turn active. A structurally marked
Candidate Activation Finding returns to its sender, which routes a product
defect to Developer and Blocks only for proven infrastructure failure.

Every formal payload must include concrete `## Decision rationale`,
`## Acceptance coverage`, `## Open findings`, and `## Evidence`. Completion's
single Open Findings section must contain only `None` after full coverage.

Limits: at most 18 business Turns and 10800 seconds.
