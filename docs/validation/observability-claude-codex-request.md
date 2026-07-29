# Agent-Team observability validation request

Audit and finish the current uncommitted Agent-Team observability
implementation in this worktree. The implementation must satisfy all six
acceptance areas below without weakening existing lifecycle, recovery,
integrity, process-safety, or backward-compatibility guarantees:

1. Every quiescent External Turn has `trace-manifest.json` with byte sizes,
   counts, and SHA-256 hashes for retained trace artifacts, and the manifest
   hash is anchored set-once in the Turn Runtime. Later artifact tampering must
   be detected.
2. Harness records are normalized into `trace.jsonl`, including agent
   messages, tool calls/results, file changes, usage, errors, and only
   Harness-exposed reasoning summaries. Every normalized event must retain a
   raw stdout/stderr sequence reference, with fallback events preventing
   silent loss of unknown record types.
3. `agent-team transcript` and `agent-team tail` support role/Turn filters and
   machine-readable output. Transcript summaries include event/tool counts and
   available token, cost, and duration data.
4. Full audit mode requires all business roles to be External, leaves Origin
   as control plane only, and blocks a Turn if raw or normalized capture is
   truncated.
5. Audited formal Handoff, Completion, and Agent Block payloads require
   non-empty `Decision rationale` and `Evidence` Markdown sections. The design
   must explicitly avoid depending on private hidden chain-of-thought.
6. Trace policy provides heuristic secret redaction, per-Turn byte limits, and
   raw retention modes with clear privacy limitations.

Use the current live diff as work material. Inspect the implementation
independently, fix any defects you find, and add or improve tests and
documentation as needed. Verify at minimum:

- `uv run python -m compileall -q src tests`
- `uv run pytest`
- `git diff --check`
- `uv build`

Do not create a Git commit. Do not stage `.agent-team/`.
