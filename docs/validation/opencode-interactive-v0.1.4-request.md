# Request

Validate a four-Turn Agent-Team collaboration using two independent OpenCode
sessions and their built-in workspace file tools.

The required sequence is deterministic:

1. On the Developer's first Turn, create `result.txt` whose complete content is
   exactly `phase-one` followed by one newline, then hand off to Reviewer.
2. On the Reviewer's first Turn, read and verify `result.txt`, then hand back a
   single finding requiring the content to become exactly `phase-two` followed
   by one newline.
3. On the Developer's resumed Turn, update `result.txt` to that exact final
   content, verify it with the built-in read tool, then hand off to Reviewer.
4. On the Reviewer's resumed Turn, independently read the final file and
   complete the Run only when it is exact and no finding remains.

Only `result.txt` may be changed. Use OpenCode's built-in read/write/edit tools
for repository work. Do not run shell commands, temporary probes, package
installers, tests, or network tools. The only Bash command allowed is the one
formal `$AGENT_TEAM_CLI handoff`, `complete`, or `block` action that ends the
current Turn.
