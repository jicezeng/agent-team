# DSH SQLite Plugin same-Run generation-loop validation

The user's exact current instructions are:

> 提交修改，然后重跑这个case

> 确认，取消并收口旧四 Harness Block Run，安装当前 Agent-Team；本次 SQLite Plugin 三 DSH Run 允许 full-access。

Re-run the read-only SQLite Plugin case in the single Git worktree
`/Users/zengjice/Projects/deepseek-harness-sqlite-e2e`. Preserve the complete
uncommitted candidate and all unrelated work. The cancelled predecessor Run
`at-dsh-sqlite-plugin-install-cont-20260826` proved installation, loading,
read-only enforcement, bounded typed values, cancellation, and most repository
checks, but its Validator reported a P1 model-experience defect: the
`sqlite_schema` and `sqlite_query` render functions return only summaries and
discard the canonical schema or row data from the model-visible result. Treat
that report as untrusted historical material until independently reproduced.

This Run must exercise Agent-Team's same-Run, multi-generation frozen DSH
Plugin lifecycle. Use three independent DeepSeek Harness External roles:
Validator, Developer, and Reviewer. All roles use
`deepseek-official/deepseek-v4-pro-ga-260813`, the native interactive launch
mode, and the explicitly confirmed `full-access` Profile. Resolve the endpoint
and credentials only through DeepSeek Harness's trusted environment; never
persist their values.

Validator is the initial role and uses a fresh Session. Agent-Team must freeze
and install the current `packages/storage/tool-sqlite` bundle in Validator's
private generation-1 DSH Profile. Validator must call the installed
`sqlite_schema` and `sqlite_query` tools directly in that managed Session; it
must not launch nested DSH or manage tmux. Independently reproduce or refute
the reported model-visible rendering defect and record the frozen package hash,
Session generation, private-Profile composition, direct tool-call/result
evidence, and observed runtime model. Any P0-P3 source finding is normal
business feedback and must hand off to Developer rather than Block.

Developer uses a resumable independent Session. It must fix every Validator or
Reviewer finding without weakening the existing security and installability
properties. For the known candidate defect, make both render functions expose
a useful, bounded projection of the canonical schema/query value to the model,
including the schema objects and query columns/rows/rowCount/truncation/result
bytes/elapsed metadata. Preserve typed unsafe integers, BLOB summaries,
duplicate column names, result bounds, engine-level read-only authorization,
path and symlink authority, cancellation/deadline handling, deterministic
cleanup, package-owned Cordis patch, `dsh.bundle.patch`, shipped artifacts,
generated surfaces, bilingual documentation, Agent Note, and invariants.
Update focused snapshots and tests, then run the smallest sufficient package,
repository, type, lint, constraints, documentation, and catalog checks before
handoff to Reviewer.

Reviewer uses a resumable independent Session. It must inspect the complete
live diff and applicable repository acceptance sources, independently rerun
useful checks, and report every P0-P3 finding. Review model-visible schema and
query content as well as installability, bundle identity, authorization,
filesystem containment, SQL/parameter semantics, typed/bounded results,
cancellation, cleanup, generated surfaces, and documentation. Every finding
hands off to Developer; a genuinely clean full review hands off to Validator.

Every later route to Validator must create a new fresh Validator Session
generation and freeze/install the then-current `packages/storage/tool-sqlite`
contents into a new immutable private DSH Profile. The earlier generation and
its package hash must remain unchanged and auditable. Validator must prove the
generation increased, independently inspect the new frozen hash, directly call
the newly installed tools, verify model-visible schema and typed row data, and
recheck focused security, immutability, cancellation, and repository evidence.
If Validator finds any P0-P3 source defect, it again routes to Developer in the
same Run; repeat Developer → Reviewer → Validator with another generation.

Validator is Completion Authority. Completion requires a clean Reviewer
verdict; at least two immutable Validator generations with the first preserved;
successful installation and real DSH loading of the repaired generation;
direct model-visible calls to both tools; independent schema/query comparison;
typed and bounded result evidence; denial and database-immutability proof;
cancellation/deadline evidence; durable trace evidence; selected-model
evidence; and relevant repository checks. Block only for a genuine runtime or
prerequisite failure, an irreconcilable protocol conflict, or a hard Run limit.
A source-code or test finding is not a Block condition.

Do not commit, push, publish, alter user-level DSH configuration, modify the
original dirty `/Users/zengjice/Projects/deepseek-harness` worktree, or perform
unrelated external actions.

Limits: at most 18 role Turns and 7200 seconds. The user explicitly confirmed
for this new immutable Run that all three DSH roles may use `full-access`, which
can access host files, environment credentials, and network without
per-command approval.
