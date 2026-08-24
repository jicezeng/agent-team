# DSH self-hosted plugin case

Use three independent DeepSeek Harness External Agents to finish, review, and
validate the existing `@deepseek-ai/dsh-worktree-status` plugin implementation
in `/Users/zengjice/Projects/deepseek-harness`.

The Developer must inspect the current dirty worktree, repository instructions,
implementation, generated surfaces, documentation, and tests; fix only real
remaining defects; and run the repository-appropriate focused verification.
The Reviewer must independently review the complete change and return every
P0-P3 finding to the Developer. Repeat Developer → Reviewer until no finding
remains.

Only after review is clean, create the Validator. The Validator must be a fresh
Agent-Team-managed DSH Agent whose private profile directly loads the candidate
bundle from `packages/git/worktree-status`. It must invoke the model-visible
`worktree_status` tool in its own DSH session against the real worktree and
independently verify the canonical result, durable tool evidence, package
installation/profile composition, and relevant repository checks. It must not
launch a nested DSH process from Bash.

Completion requires a clean Reviewer verdict plus real Validator evidence that
the newly developed plugin was installed by Agent-Team, loaded by DSH, and
successfully called. Preserve all three Harness Sessions. Do not commit, push,
publish, or modify files outside the requested worktree.

Limits: at most 18 role turns and 7200 seconds. The user explicitly confirmed
full-access for this Run.
