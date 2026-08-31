# Three-DSH SQLite Plugin BigInt continuation case v0.2.9

Continue from the live dirty candidate in the single Git worktree:

`/Users/zengjice/Projects/deepseek-harness-sqlite-doubao2-ctx256k-greenfield-e2e-v5`

Preserve cancelled Run `at-20260830-165308-774984` and its predecessors as
immutable audit records, not proof. Reconstruct from this Request, the current
worktree, repository sources, and fresh direct evidence. Keep valid candidate
work, but do not trust prior role claims or mocked evidence.

All roles are independent DeepSeek Harness External roles in native interactive
mode using explicit model
`deepseek-official/doubao-seed-2-0-pro-260215`. The user explicitly confirmed
this new Run may use `full-access`. Never persist or disclose endpoint,
credential, header, token, or environment values. Do not commit, push, publish,
modify user-level DSH configuration, launch nested model Agents, or manage tmux.

## Immediate findings from the cancelled Run

Treat every item as open until current reproducible evidence closes it.

1. A real `node:sqlite` query returning `9007199254740993` raised
   `RangeError: Value is too large to be represented as a JavaScript number`
   instead of returning an exact decimal string with safety metadata. Existing
   mocked tests did not reproduce the runtime API and hid this defect.
2. Investigate the actual repository-supported Node runtime and `node:sqlite`
   API. Do not assume that constructor option `bigInt`, `int64`, or statement
   method `setReadBigInts` exists or behaves as claimed; prove the selected API
   against the real module and the built Worker.
3. Add real integration coverage that creates a SQLite database, executes the
   built production path, and proves exact preservation immediately above and
   below JavaScript's safe-integer boundary, negative unsafe integers, duplicate
   columns, and ordinary numeric values. Mocks may supplement but never replace
   this gate.
4. The previous Validator did not produce retained managed trace evidence naming
   direct calls to both installed `sqlite_schema` and `sqlite_query`.
5. The previous Validator changed Git-visible workspace content. Reviewer and
   Validator are read-only. They must put every database, archive consumer,
   Profile, script, cache, report, and diagnostic probe under a newly allocated
   OS temporary directory outside the worktree, remove it when finished, and
   never create, edit, rename, or delete a Git-visible workspace path.
6. Remove obsolete case-created root probes, scripts, logs, and databases as a
   Developer responsibility. Convert useful coverage into package-owned tests
   or fixtures; do not remove unrelated repository content.

This focused continuation does not waive any condition below.

## Product contract

Finish the opt-in installable model-facing function Plugin
`@deepseek-ai/dsh-tool-sqlite` at `packages/storage/tool-sqlite`:

- `sqlite_schema` returns canonical user tables, views, and virtual tables with
  columns, declared types, primary keys, foreign keys, indexes, SQL definitions,
  and exact STRICT status while omitting SQLite internal/FTS shadow tables.
- `sqlite_query` returns ordered columns, positional typed rows, the documented
  bounded row count, explicit truncation, exact complete-result UTF-8 bytes, and
  elapsed time.

Represent unsafe integers as exact decimal strings with explicit safety
metadata and BLOBs only by byte length and SHA-256. Incrementally consume under
row and complete UTF-8 JSON bounds without unbounded materialization. Preserve
duplicate result-column positions.

Open the database read-only and enforce a SQLite engine-level authorizer or
equivalent opcode policy, never SQL text checks alone. Paths must stay within
the authorized workspace and reject absolute paths, parent traversal,
directories, missing/non-database files, and symlink escape. Denied operations
must leave database bytes and row counts unchanged. Cancellation and deadline
must interrupt actual execution, and cleanup must deterministically settle once
across success, error, cancellation, deadline, Worker exit, HMR replacement,
and Plugin disposal.

`sqlite_query` accepts exactly one parameterized `SELECT`, `WITH ... SELECT`,
`VALUES`, or `EXPLAIN QUERY PLAN`. Reject PRAGMA, DML, DDL, transactions,
`ATTACH`, `DETACH`, `VACUUM`, extension loading, and trailing executable
statements while retaining engine authorization. The statement boundary must
handle comments, SQLite quoting, escaped quotes, and semicolons without naive
splitting or regex-only classification.

`resultBytes` is the deterministic fixed-point UTF-8 size of the complete
emitted JSON, including its own digit count, metadata, typed cells, and
multibyte data. Emit the smallest valid truncated envelope when rows do not fit;
error only when that envelope cannot fit. `rowCount` has one documented bounded
streaming meaning.

The package must install outside the monorepo, own its opt-in patch and
invariant companion, expose valid packed artifacts, use the real omitted-config
Cordis boundary, pass applicable package/repository/generated/documentation
gates, and remain absent from shipped default Profiles. Its Worker and runtime
must work with monorepo source, workspace links, pre-existing package, and
`node_modules` fallback absent.

## Mandatory independent gates

Developer and Reviewer must preserve reproducible evidence for the whole
candidate. Fresh Validator independently repeats the consumer-facing gates.

1. Applicable build, focused unit and real integration tests, typecheck, lint,
   constraints, invariants, runtime closure, catalogs, docs, assembled snapshot,
   hygiene, and publish checks pass without broad suppression, unsafe `any`,
   floating cleanup, non-null/ts-ignore escapes, or generated drift.
2. A newly packed archive has recorded exact contents and hash, contains only
   intended runtime/declarations/docs/metadata, and installs in a new isolated
   consumer/Profile with no monorepo fallback.
3. The package-owned opt-in patch is applied, Plugin config is omitted, the real
   Loader boots with documented defaults, and both tools register.
4. The installed tools are called directly against a fresh SQLite fixture and
   prove schema/query positives, exact unsafe-integer behavior, duplicate
   columns, hostile identifiers, STRICT/FTS handling, limits, cancellation,
   deadline, disposal, authorization, path rejection, and unchanged database.
5. English and Chinese installation, activation, Model Experience, security,
   configuration, bounds, cancellation, and limitations docs, translation
   metadata, exported-surface docs, and a non-trivial Agent Note are complete.
6. All case-created root probes and disposable validation residue are absent
   from the final candidate.

Packaging, loading, activation, configuration, dependency, runtime, or direct
tool-call defects are product findings and stay inside the role loop. Bash,
source imports, hand-built mocks, unit tests, or nested DSH are not substitutes
for managed installed-tool calls.

## Exact role actions and routes

- Developer is the only product-writing role. It addresses the complete finding
  set in coherent batches, runs all gates, and may only Handoff to Reviewer.
- Reviewer is independent and read-only for Git-visible candidate state. It
  returns every P0-P3 finding, failed gate, missing evidence, or unverified
  condition to Developer. Only a complete clean review may Handoff to Validator.
- Validator is fresh, candidate-bound, Completion Authority, and read-only for
  Git-visible candidate state. Any finding returns to Developer. It may Complete
  only after exhaustive current evidence and zero findings.

Freeze the exact role-selected edges `developer -> reviewer`,
`reviewer -> developer`, `reviewer -> validator`, and `validator -> developer`.
Freeze Reviewer and Validator as read-only roles. A read-only role must perform
all disposable work strictly outside the worktree and leave the Git-visible
workspace byte-for-byte equal to its frozen Before facts.

Fresh Validator must call the managed installed `sqlite_schema` and
`sqlite_query` itself; retained trace evidence must name both tools. Every
Validator finding restarts Developer -> Reviewer -> a later fresh Validator.
`ROUTE_PREFLIGHT_REJECTED` leaves the sender Turn active. A structurally marked
Candidate Activation Finding returns to its sender, which routes a product
defect to Developer and Blocks only for proven infrastructure failure.

Every formal payload must include concrete `## Decision rationale`,
`## Acceptance coverage`, `## Open findings`, and `## Evidence`. Completion's
single Open Findings section contains only `None` after full coverage.

Limits: at most 9 business Turns and 7200 seconds.
