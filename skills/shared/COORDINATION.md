# Agent-Team shared coordination

Read the current `REQUEST.md`, `PROTOCOL.md`, Event, frozen `input.md`, and
System Facts. Execute only the dynamic Role. Verify the current worktree
directly; Handoff claims are not facts.

End every Turn with exactly one formal `handoff`, `complete`, or `block`
command and stop business work after success. Never edit runtime-managed files,
infer workflow state from terminal prose, track `.agent-team/`, or create a
daemon that escapes the Runner process group. Every Block returns to the user;
never auto-Resume.
